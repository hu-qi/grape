"""Durable, at-least-once GitLab dispatch. Never store authentication headers."""
import asyncio
import hashlib
import json
import time
import uuid

LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


class Inbox:
    def __init__(self, db, lock):
        self.db, self.lock = db, lock
        with lock:
            db.execute('CREATE TABLE IF NOT EXISTS gitlab_webhook_inbox ('
                       'id VARCHAR(64) PRIMARY KEY, repo_key VARCHAR(512) NOT NULL, '
                       'body LONGTEXT NOT NULL, event VARCHAR(128) NOT NULL, '
                       'status VARCHAR(16) NOT NULL, attempts INTEGER NOT NULL, '
                       'available_at DOUBLE NOT NULL, lease_owner VARCHAR(64) NOT NULL, '
                       'lease_until DOUBLE NOT NULL, updated_at DOUBLE NOT NULL)')
            db.commit()

    def enqueue(self, provider, repo_key, delivery_id, body, event, now=None):
        now = time.time() if now is None else now
        delivery_id = delivery_id or uuid.uuid4().hex
        key = hashlib.sha256(json.dumps([provider, repo_key, delivery_id]).encode()).hexdigest()
        with self.lock:
            # Only completed payloads expire; pending work must survive long outages.
            self.db.execute("DELETE FROM gitlab_webhook_inbox WHERE status='done' AND updated_at<?", (now - 86400,))
            row = self.db.execute('SELECT status FROM gitlab_webhook_inbox WHERE id=?', (key,)).fetchone()
            if row:
                if row[0] == 'failed':
                    changed = self.db.execute("UPDATE gitlab_webhook_inbox SET status='pending', attempts=0, available_at=?, updated_at=? WHERE id=? AND status='failed'", (now, now, key)).rowcount
                    self.db.commit()
                    return bool(changed)
                self.db.commit()
                return False
            try:
                self.db.execute('INSERT INTO gitlab_webhook_inbox (id,repo_key,body,event,status,attempts,available_at,lease_owner,lease_until,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                                (key, repo_key, body.decode('utf-8'), event, 'pending', 0, now, '', 0, now))
                self.db.commit()
            except Exception:
                self.db.rollback()
                if self.db.execute('SELECT 1 FROM gitlab_webhook_inbox WHERE id=?', (key,)).fetchone():
                    return False
                raise
        return True

    def claim(self, now=None):
        now = time.time() if now is None else now
        eligible = "((status='pending' AND available_at<=?) OR (status='processing' AND lease_until<=?))"
        with self.lock:
            self.db.execute("UPDATE gitlab_webhook_inbox SET status='failed', updated_at=? WHERE status='processing' AND lease_until<=? AND attempts>=?", (now, now, MAX_ATTEMPTS))
            row = self.db.execute('SELECT id FROM gitlab_webhook_inbox WHERE ' + eligible + ' AND attempts<? ORDER BY available_at LIMIT 1', (now, now, MAX_ATTEMPTS)).fetchone()
            if not row:
                self.db.commit()
                return None
            owner = uuid.uuid4().hex
            changed = self.db.execute("UPDATE gitlab_webhook_inbox SET status='processing', attempts=attempts+1, lease_owner=?, lease_until=?, updated_at=? WHERE id=? AND " + eligible + ' AND attempts<?',
                                      (owner, now + LEASE_SECONDS, now, row[0], now, now, MAX_ATTEMPTS)).rowcount
            self.db.commit()
            if not changed:
                return None
            row = self.db.execute('SELECT id,repo_key,body,event,attempts,lease_owner FROM gitlab_webhook_inbox WHERE id=? AND lease_owner=?', (row[0], owner)).fetchone()
            return dict(zip(('id', 'repo_key', 'body', 'event', 'attempts', 'lease_owner'), row)) if row else None

    def renew(self, job, now=None):
        now = time.time() if now is None else now
        with self.lock:
            changed = self.db.execute("UPDATE gitlab_webhook_inbox SET lease_until=? WHERE id=? AND lease_owner=? AND status='processing'", (now + LEASE_SECONDS, job['id'], job['lease_owner'])).rowcount
            self.db.commit()
            return bool(changed)

    def finish(self, job, failed=False, now=None):
        now = time.time() if now is None else now
        status = ('failed' if job['attempts'] >= MAX_ATTEMPTS else 'pending') if failed else 'done'
        with self.lock:
            changed = self.db.execute("UPDATE gitlab_webhook_inbox SET status=?, available_at=?, lease_until=0, updated_at=? WHERE id=? AND lease_owner=? AND status='processing'",
                                      (status, now + 5 * 2 ** job['attempts'], now, job['id'], job['lease_owner'])).rowcount
            self.db.commit()
            return bool(changed)


def get_inbox():
    from src.session_manager import db, db_lock
    return Inbox(db, db_lock)


def dispatch(job):
    from src.api.event_pipeline import run_pipeline
    from src.user.db import get_webhook_secret
    # Unhosting while a job is pending revokes its permission to run.
    if not get_webhook_secret(job['repo_key']):
        return
    repo = job['repo_key'].split(':', 1)[1]
    return run_pipeline(job['body'].encode(), {'X-Gitlab-Event': job['event']}, account=repo, repo_key_override=job['repo_key'])


async def run_worker():
    from src.config import log
    queue = get_inbox()
    while True:
        try:
            job = await asyncio.to_thread(queue.claim)
            if not job:
                await asyncio.sleep(1)
                continue
            task = asyncio.create_task(asyncio.to_thread(dispatch, job))
            try:
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=10)
                    if not done:
                        try:
                            await asyncio.to_thread(queue.renew, job)
                        except Exception:
                            # A temporary DB outage must not mark a still-running
                            # dispatch as failed and immediately launch it again.
                            log.error('GitLab inbox lease renewal unavailable')
                task.result()
            except asyncio.CancelledError:
                # A thread cannot be cancelled: keep renewing until dispatch exits.
                # If the process is killed, another worker reclaims after lease expiry.
                while not task.done():
                    await asyncio.wait({task}, timeout=10)
                    await asyncio.to_thread(queue.renew, job)
                try:
                    task.result()
                except Exception:
                    await asyncio.to_thread(queue.finish, job, True)
                else:
                    await asyncio.to_thread(queue.finish, job)
                raise
            except Exception:
                await asyncio.to_thread(queue.finish, job, True)
                log.warning('GitLab dispatch failed; inbox=%s attempt=%s', job['id'], job['attempts'])
            else:
                await asyncio.to_thread(queue.finish, job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not log exceptions that may include payloads or remote credentials.
            log.error('GitLab inbox worker unavailable; retrying')
            await asyncio.sleep(5)
