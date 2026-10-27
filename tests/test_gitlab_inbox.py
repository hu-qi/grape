"""Durable delivery acceptance, restart recovery and retry contracts."""
import sqlite3
import threading

import pytest

from src.vcs.gitlab_inbox import Inbox


@pytest.fixture
def inbox(tmp_path):
    db = sqlite3.connect(tmp_path / 'inbox.db', check_same_thread=False)
    queue = Inbox(db, threading.RLock())
    yield queue
    db.close()


def put(queue, provider='gitlab', repo='gitlab:g/p', delivery='delivery-1'):
    return queue.enqueue(provider, repo, delivery, b'{"object_kind":"issue"}', 'Issue Hook', now=100)


def test_acceptance_survives_new_worker(inbox):
    assert put(inbox)
    restarted = Inbox(inbox.db, inbox.lock)
    job = restarted.claim(now=101)
    assert job['body'] == '{"object_kind":"issue"}'
    assert restarted.claim(now=102) is None


def test_delivery_is_scoped_to_project_and_instance(inbox):
    assert put(inbox)
    assert not put(inbox)
    assert put(inbox, repo='gitlab:g/other')
    assert put(inbox, provider='gitlab@corp', repo='gitlab@corp:g/p')


def test_expired_lease_recovers_and_fences_old_worker(inbox):
    put(inbox)
    old = inbox.claim(now=101)
    new = inbox.claim(now=200)
    assert new['id'] == old['id']
    assert new['lease_owner'] != old['lease_owner']
    assert not inbox.finish(old, now=201)
    assert inbox.finish(new, now=202)
    assert inbox.claim(now=300) is None


def test_heartbeat_prevents_concurrent_dispatch(inbox):
    put(inbox)
    job = inbox.claim(now=101)
    assert inbox.renew(job, now=150)
    assert inbox.claim(now=170) is None


def test_failure_backoff_bounded_retry_and_explicit_redelivery(inbox):
    put(inbox)
    for now in (101, 200, 300):
        job = inbox.claim(now=now)
        assert job
        inbox.finish(job, failed=True, now=now)
        assert inbox.claim(now=now+1) is None
    assert inbox.claim(now=1000) is None
    assert put(inbox)  # Manual GitLab redelivery restarts exhausted work.
    assert inbox.claim(now=1001)['attempts'] == 1


def test_completed_delivery_is_not_replayed(inbox):
    put(inbox)
    inbox.finish(inbox.claim(now=101), now=102)
    assert not put(inbox)
    assert inbox.claim(now=200) is None


def test_reopen_connection_recovers_persisted_event(tmp_path):
    path = tmp_path / 'restart.db'
    first = sqlite3.connect(path)
    put(Inbox(first, threading.RLock()))
    first.close()
    second = sqlite3.connect(path)
    try:
        assert Inbox(second, threading.RLock()).claim(now=101)['repo_key'] == 'gitlab:g/p'
    finally:
        second.close()


def test_two_database_connections_cannot_claim_same_delivery(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'competing.db'
    db = sqlite3.connect(path)
    put(Inbox(db, threading.RLock()))
    db.close()

    def claim():
        connection = sqlite3.connect(path, timeout=10)
        try:
            return Inbox(connection, threading.RLock()).claim(now=101)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: claim(), range(2)))
    assert sum(job is not None for job in jobs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('raises', [False, True])
async def test_worker_records_dispatch_outcome(inbox, monkeypatch, raises):
    import asyncio
    from src.vcs import gitlab_inbox
    put(inbox)
    monkeypatch.setattr(gitlab_inbox, 'get_inbox', lambda: inbox)
    calls = []

    def dispatch(job):
        calls.append(job['repo_key'])
        if raises:
            raise RuntimeError('secret error must not be logged')
        return {'ok': True}

    monkeypatch.setattr(gitlab_inbox, 'dispatch', dispatch)
    worker = asyncio.create_task(gitlab_inbox.run_worker())
    try:
        expected = 'pending' if raises else 'done'
        for _ in range(200):
            with inbox.lock:
                row = inbox.db.execute('SELECT status, attempts FROM gitlab_webhook_inbox').fetchone()
            if tuple(row) == (expected, 1):
                break
            await asyncio.sleep(0.01)
        assert tuple(row) == (expected, 1)
        assert calls == ['gitlab:g/p']
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
