"""GitLab.com and configured private instances use the same isolated contract."""
import json
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def instances(monkeypatch):
    monkeypatch.setenv('GRAPE_GITLAB_INSTANCES', json.dumps({
        'corp': {'url': 'https://gitlab.example.test/gitlab', 'token_env': 'CORP_GITLAB_TOKEN'},
    }))
    monkeypatch.setenv('CORP_GITLAB_TOKEN', 'private-test-token')


def test_public_instance_default():
    from src.vcs.gitlab_config import get_instance
    assert get_instance('gitlab').api_base == 'https://gitlab.com/api/v4'


def test_private_subpath(instances):
    from src.repo import Repo
    repo = Repo('gitlab@corp:group/subgroup/project')
    assert repo.api_base == 'https://gitlab.example.test/gitlab/api/v4'
    assert repo.clone_url == 'https://gitlab.example.test/gitlab/group/subgroup/project.git'


def test_unknown_instance_fails_closed():
    from src.vcs.gitlab_config import get_instance
    with pytest.raises(ValueError):
        get_instance('gitlab@missing')


@pytest.mark.parametrize('url', ['http://example.test', 'https://user:secret@example.test', 'https://example.test/?token=x', 'file:///tmp/gitlab'])
def test_invalid_instance_urls(monkeypatch, url):
    from src.vcs.gitlab_config import get_instance
    monkeypatch.setenv('GRAPE_GITLAB_INSTANCES', json.dumps({'bad': {'url': url}}))
    with pytest.raises(ValueError):
        get_instance('gitlab@bad')


def test_gitlab_project_encoding(instances):
    from src.vcs.api_client import VcsApiClient
    client = VcsApiClient('gitlab@corp', 'test', 'group', 'subgroup/project')
    with patch.object(client, '_gitlab_request', return_value={}) as request:
        client.get_issue(7)
    assert request.call_args.args[1] == '/projects/group%2Fsubgroup%2Fproject/issues/7'


def test_create_mr_native_fields():
    from src.vcs.api_client import VcsApiClient
    client = VcsApiClient('gitlab', 'test', 'group', 'project')
    with patch.object(client, '_gitlab_request', return_value={'iid': 8}) as request:
        client.create_pr('Title', 'feature/a', 'main', 'Description')
    assert request.call_args.args[2] == {'title': 'Title', 'source_branch': 'feature/a', 'target_branch': 'main', 'description': 'Description'}


def test_provider_translation():
    from src.vcs.providers import translate_inbound
    assert translate_inbound('gitlab', 'issues', 'update', {'state': 'closed', 'body': 'done'}) == {'state_event': 'close', 'description': 'done'}


def test_private_provider_routes(instances):
    from src.api.git_repo_api import _vcs_url
    assert _vcs_url('group', 'subgroup/project', '/pulls/7/comments', None, 'gitlab@corp') == 'https://gitlab.example.test/gitlab/api/v4/projects/group%2Fsubgroup%2Fproject/merge_requests/7/notes'


def test_normalize_mr_response():
    from src.vcs.providers.gitlab import normalize_response
    result = normalize_response({'id': 100, 'iid': 7, 'description': 'text', 'web_url': 'https://example.test/mr/7', 'source_branch': 'feat', 'target_branch': 'main'})
    assert (result['number'], result['body'], result['html_url'], result['head']['ref'], result['base']['ref']) == (7, 'text', 'https://example.test/mr/7', 'feat', 'main')


def test_clone_private(instances):
    from src.runtime.repogit import clone_url_for
    assert clone_url_for('gitlab@corp:group/subgroup/project') == 'https://gitlab.example.test/gitlab/group/subgroup/project.git'


def test_provider_factory(instances):
    from src.token_resolver import create_provider_from_repo_key
    provider = create_provider_from_repo_key('gitlab@corp:group/project')
    assert provider.provider_name == 'gitlab@corp'
    assert provider.pat_create_url().startswith('https://gitlab.example.test/gitlab/')


def test_gitlab_event_body_and_mr_branch():
    from src.vcs.gitlab import GitLabBackend
    event = GitLabBackend().normalize(json.dumps({'object_kind': 'merge_request', 'project': {'path_with_namespace': 'g/sub/p'}, 'object_attributes': {'action': 'open', 'iid': 7, 'description': 'text', 'source_branch': 'feat', 'target_branch': 'main'}, 'user': {'username': 'alice'}}).encode(), 'Merge Request Hook')
    assert (event['branch'], event['payload']['pull_request']['number'], event['payload']['pull_request']['body']) == ('feat', 7, 'text')


def test_http_does_not_follow_redirect_or_leak_token(instances, caplog):
    from src.vcs.gitlab_http import request
    response = Mock(status_code=302)
    with patch('src.vcs.gitlab_http.requests.request', return_value=response) as send:
        with pytest.raises(RuntimeError, match='302'):
            request('gitlab@corp', 'GET', '/user', 'never-log-this')
    assert send.call_args.kwargs['allow_redirects'] is False
    assert send.call_args.kwargs['verify'] is True
    assert 'never-log-this' not in caplog.text


@pytest.fixture
def api_client(instances):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.deps import get_current_user_or_api_key, require_no_session_token, require_org_member
    from src.api import git_repo_api
    from src.api.routes import gitlab, webhook, repos
    app = FastAPI()
    app.include_router(git_repo_api.router)
    app.include_router(gitlab.router)
    app.include_router(webhook.router)
    app.include_router(repos.router)
    user = {'id': 98001, 'username': 'gitlab_tester', 'auth_type': 'session_token',
            'agent_type': 'vod', 'repo_key': 'gitlab@corp:group/subgroup/project', 'org_id': 0}
    app.dependency_overrides[get_current_user_or_api_key] = lambda: user
    app.dependency_overrides[require_no_session_token] = lambda: user
    app.dependency_overrides[require_org_member] = lambda: user
    with TestClient(app) as client:
        yield client, user
    from src.session_manager import db, db_lock
    with db_lock:
        db.execute('DELETE FROM managed_repos WHERE repo_key LIKE ?', ('gitlab%:group/%',))
        db.execute('CREATE TABLE IF NOT EXISTS gitlab_host_reservations (repo_key VARCHAR(512) PRIMARY KEY, user_id INTEGER NOT NULL, org_id INTEGER NOT NULL, secret VARCHAR(128) NOT NULL, expires_at DOUBLE NOT NULL)')
        db.execute('DELETE FROM gitlab_host_reservations WHERE user_id=?', (98001,))
        db.execute('DELETE FROM user_credentials WHERE user_id=?', (98001,))
        db.execute('DELETE FROM user_repos WHERE user_id=?', (98001,))
        db.commit()


def _json_response(data, status=200):
    response = Mock(status_code=status, content=json.dumps(data).encode())
    response.json.return_value = data
    return response


def test_user_binding_encrypts_and_never_returns_token(api_client):
    from src.vcs.gitlab_credentials import user_token
    from src.user.db import get_credential
    client, user = api_client
    token = 'private-credential-for-test'
    with patch('src.vcs.gitlab_http.requests.request', return_value=_json_response({'id': 10, 'username': 'alice', 'state': 'active'})):
        response = client.put('/api/gitlab/connection', json={'provider': 'gitlab@corp', 'token': token})
    assert response.status_code == 200
    assert token not in response.text
    assert token not in json.dumps(get_credential(user['id'], 'gitlab@corp'))
    assert user_token(user['id'], 'gitlab@corp') == token
    assert user_token(user['id'], 'gitlab') == ''


def test_user_gitlab_credential_is_not_a_login_identity(api_client):
    from src.vcs.gitlab_credentials import save_user_token
    from src.auth.grape_account_db import get_user_identities
    _, user = api_client
    save_user_token(user['id'], 'gitlab@corp', 'private-test', 'alice')
    assert not any(i.get('provider') == 'gitlab@corp' for i in get_user_identities(user['id']))


def test_namespace_issue_request_resolves_private_token(api_client):
    from src.vcs.gitlab_credentials import save_user_token
    from src.user.db import add_managed_repo
    client, user = api_client
    save_user_token(user['id'], 'gitlab@corp', 'test-private-user', 'alice')
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'])
    with patch('src.vcs.gitlab_http.requests.request', return_value=_json_response({'iid': 7, 'title': 'test'})) as send:
        response = client.get('/api/git-repo/group/subgroup/project/issues/7')
    assert response.status_code == 200
    assert response.json()['number'] == 7
    assert send.call_args.args[:2] == ('GET', 'https://gitlab.example.test/gitlab/api/v4/projects/group%2Fsubgroup%2Fproject/issues/7')
    assert send.call_args.kwargs['headers']['PRIVATE-TOKEN'] == 'test-private-user'


def test_namespace_cannot_read_other_project_with_session(api_client):
    client, _ = api_client
    with patch('src.vcs.gitlab_http.requests.request') as send:
        response = client.get('/api/git-repo/group/subgroup/another/issues/7')
    assert response.status_code == 403
    send.assert_not_called()


def test_namespace_metadata_route_does_not_shadow_issue_routes(api_client):
    from src.vcs.gitlab_credentials import save_user_token
    from src.user.db import add_managed_repo
    client, user = api_client
    save_user_token(user['id'], 'gitlab@corp', 'test-private-user', 'alice')
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'])
    with patch('src.vcs.gitlab_http.requests.request', return_value=_json_response({'path_with_namespace': 'group/subgroup/project'})) as send:
        response = client.get('/api/git-repo/group/subgroup/project')
    assert response.status_code == 200
    assert send.call_args.args[1].endswith('/projects/group%2Fsubgroup%2Fproject')


def test_gitlab_mr_update_converts_patch_and_body(instances):
    from src.vcs.providers.gitlab import Adapter
    adapter = Adapter('gitlab@corp')
    with patch('src.vcs.gitlab_http.requests.request', return_value=_json_response({'iid': 9})) as send:
        adapter.request('PATCH', adapter.url('g/sub', 'p', '/pulls/9'), 'test', {'body': 'updated'})
    assert send.call_args.args[0] == 'PUT'
    assert send.call_args.kwargs['json'] == {'description': 'updated'}


def test_gitlab_notes_keep_body(instances):
    from src.vcs.providers.gitlab import Adapter
    adapter = Adapter('gitlab@corp')
    with patch('src.vcs.gitlab_http.requests.request', return_value=_json_response({'id': 12, 'body': 'comment'})) as send:
        adapter.request('POST', adapter.url('g', 'p', '/issues/1/comments'), 'test', {'body': 'comment'})
    assert send.call_args.kwargs['json'] == {'body': 'comment'}


def test_token_resolver_never_uses_other_instance_env(instances, monkeypatch):
    from src.token_resolver import TokenResolver, create_provider
    monkeypatch.setenv('GITLAB_TOKEN', 'public-token')
    monkeypatch.setattr('src.user.db.get_host_user_id', lambda key: 0)
    assert TokenResolver(create_provider('gitlab@corp')).get_token('gitlab@corp:group/project') == 'private-test-token'
    monkeypatch.delenv('CORP_GITLAB_TOKEN')
    assert TokenResolver(create_provider('gitlab@corp')).get_token('gitlab@corp:group/project') == ''


def test_http_errors_do_not_echo_server_secrets(instances):
    from src.vcs.gitlab_http import request
    response = _json_response({'error': 'never-log-secret'}, 401)
    with patch('src.vcs.gitlab_http.requests.request', return_value=response):
        with pytest.raises(RuntimeError) as caught:
            request('gitlab@corp', 'GET', '/user', 'never-log-secret')
    assert 'never-log-secret' not in str(caught.value)


def test_gitlab_webhook_auth_and_instance_isolation(api_client):
    from src.user.db import add_managed_repo
    client, user = api_client
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'], webhook_secret='webhook-test-secret')
    payload = {'object_kind': 'issue', 'project': {'path_with_namespace': 'group/subgroup/project', 'web_url': 'https://gitlab.example.test/gitlab/group/subgroup/project'}, 'object_attributes': {'iid': 7, 'action': 'open'}}
    assert client.post('/webhook/gitlab/corp', json=payload).status_code == 401
    assert client.post('/webhook/gitlab/corp', json=payload, headers={'X-Gitlab-Token': 'wrong'}).status_code == 403
    assert client.post('/webhook/gitlab/default', json=payload, headers={'X-Gitlab-Token': 'webhook-test-secret'}).status_code == 400
    with patch('src.vcs.gitlab_inbox.Inbox.enqueue', return_value=True) as run:
        response = client.post('/webhook/gitlab/corp', json=payload, headers={'X-Gitlab-Token': 'webhook-test-secret', 'X-Gitlab-Event': 'Issue Hook'})
    assert response.status_code == 200


def test_private_git_credential_scope_and_url(api_client):
    from src.vcs.gitlab_credentials import save_user_token
    from src.user.db import add_managed_repo
    client, user = api_client
    save_user_token(user['id'], 'gitlab@corp', 'test-private-user', 'alice')
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'])
    response = client.get('/api/repos/group/subgroup/project/git-credential')
    assert response.status_code == 200
    assert response.json()['clone_url'] == 'https://gitlab.example.test/gitlab/group/subgroup/project.git'
    assert client.get('/api/repos/group/subgroup/another/git-credential').status_code == 403


def test_discovery_paginates_and_detects_maintainer(instances):
    from src.vcs.gitlab_manager import fetch_repos
    project = {'id': 1, 'path_with_namespace': 'g/sub/p', 'permissions': {'group_access': {'access_level': 40}}}
    with patch('src.vcs.gitlab_manager.request', side_effect=[[project] * 100, []]) as send:
        repos = fetch_repos('test', 'gitlab@corp')
    assert send.call_count == 2
    assert repos[0]['is_admin'] is True
    assert repos[0]['repo_key'] == 'gitlab@corp:g/sub/p'


def test_repository_access_does_not_match_same_path_other_instance(api_client, monkeypatch):
    from src.api.deps import check_repo_access
    from fastapi import HTTPException
    _, user = api_client
    monkeypatch.setattr('src.user.db.get_managed_repos', lambda uid: [{'repo_key': 'gitlab:group/project'}])
    with pytest.raises(HTTPException) as caught:
        check_repo_access({**user, 'auth_type': 'jwt'}, 'gitlab@corp:group/project')
    assert caught.value.status_code == 403


def test_host_rejects_another_account_before_network(api_client):
    from src.user.db import add_managed_repo
    client, user = api_client
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'] + 1)
    with patch('src.vcs.gitlab_http.requests.request') as send:
        response = client.post('/api/gitlab/host', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project'})
    assert response.status_code == 403
    send.assert_not_called()


def test_updating_body_does_not_close_issue():
    from src.vcs.providers.gitlab import translate
    assert translate('issues', 'update', {'body': 'text', 'state': ''}, {}) == {'description': 'text'}


def test_invalid_state_is_rejected():
    from src.vcs.providers.gitlab import translate
    with pytest.raises(ValueError):
        translate('issues', 'update', {'state': 'nonsense'}, {})


def test_expired_user_credential_is_isolated_and_rebind_recovers(api_client):
    from src.vcs.gitlab_credentials import save_user_token
    from src.token_resolver import UserToken, create_provider
    from src.user.db import add_managed_repo
    _, user = api_client
    save_user_token(user['id'], 'gitlab@corp', 'test-token', 'alice')
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'])
    strategy = UserToken(create_provider('gitlab@corp'))
    assert strategy.get_token(user['repo_key']) == 'test-token'
    strategy.mark_expired(user['repo_key'])
    assert strategy.get_token(user['repo_key']) == ''
    assert strategy._fetch_stored(user['repo_key']) == 'test-token'
    save_user_token(user['id'], 'gitlab@corp', 'new-test-token', 'alice')
    assert strategy.get_token(user['repo_key']) == 'new-test-token'


def test_host_saves_webhook_secret_and_instance(api_client, monkeypatch):
    from src.vcs.gitlab_credentials import save_user_token
    from src.user.db import get_webhook_secret
    client, user = api_client
    user['auth_type'] = 'jwt'
    save_user_token(user['id'], 'gitlab@corp', 'test-private-user', 'alice')
    monkeypatch.setattr('src.user.db.get_meta_value', lambda key: 'https://grape.example.test' if key == 'server_base_url' else '')
    responses = [{'id': 10, 'username': 'alice'}, {'path_with_namespace': 'group/subgroup/project'}, {'access_level': 40}, [], {'id': 77}]
    with patch('src.vcs.gitlab_http.requests.request', side_effect=[_json_response(r) for r in responses]) as send:
        result = client.post('/api/gitlab/host', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project'})
    assert result.status_code == 200
    assert result.json()['repo_key'] == user['repo_key']
    assert send.call_args.kwargs['json']['url'] == 'https://grape.example.test/webhook/gitlab/corp'
    assert send.call_args.kwargs['json']['token'] == get_webhook_secret(user['repo_key'])
    assert 'test-private-user' not in result.text


def test_session_cannot_manage_hosting(api_client):
    client, _ = api_client
    with patch('src.vcs.gitlab_http.requests.request') as send:
        result = client.post('/api/gitlab/host', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project'})
    assert result.status_code == 403
    send.assert_not_called()


def test_repo_bot_token_is_encrypted_and_overrides_user(api_client):
    from src.user.db import add_managed_repo
    from src.vcs.gitlab_credentials import save_user_token, repo_token
    from src.session_manager import db, db_lock
    client, user = api_client
    user['auth_type'] = 'jwt'
    save_user_token(user['id'], 'gitlab@corp', 'user-test-token', 'alice')
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'])
    with patch('src.vcs.gitlab_http.requests.request', side_effect=[_json_response({'id': 20, 'username': 'bot'}), _json_response({'access_level': 40})]):
        response = client.put('/api/gitlab/bot-token', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project', 'token': 'bot-test-token'})
    assert response.status_code == 200
    with db_lock:
        stored = db.execute('SELECT bot_gitlab_token FROM managed_repos WHERE repo_key=?', (user['repo_key'],)).fetchone()[0]
    assert 'bot-test-token' not in stored
    assert repo_token(user['repo_key']) == 'bot-test-token'
    client.put('/api/gitlab/bot-token', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project', 'token': ''})
    assert repo_token(user['repo_key']) == 'user-test-token'


def test_gitlab_subprocess_credential_is_not_in_argv(instances):
    from src.runtime.repogit import _run_git
    with patch('src.runtime.repogit.subprocess.run') as run:
        _run_git(['ls-remote', 'https://gitlab.example.test/gitlab/g/p.git'], 'secret-test-token', repo_key='gitlab@corp:g/p')
    assert 'secret-test-token' not in repr(run.call_args.args)
    env = run.call_args.kwargs['env']
    assert env['GIT_CONFIG_VALUE_0'] == 'true'
    assert env['GIT_CONFIG_VALUE_1'] == 'false'


def test_gitlab_actor_mapping_does_not_use_gitcode_login(api_client):
    from src.vcs.gitlab_credentials import save_user_token
    from src.runtime.engine_select import _repo_user_id
    _, user = api_client
    save_user_token(user['id'], 'gitlab@corp', 'test-private-user', 'alice')
    assert _repo_user_id(user['repo_key'], 'alice') == user['id']
    assert _repo_user_id('gitlab:group/project', 'alice') == 0


def test_cleanup_keeps_other_instances_and_legacy_workspace(instances, tmp_path, monkeypatch):
    from src.repo_cleanup import _cleanup_files, _normalize_keys
    from src.repo_key import workspace_repo_dir
    monkeypatch.setattr('src.config.WORKSPACE_DIR', str(tmp_path))
    monkeypatch.setattr('src.user.db.SKILL_DIR', str(tmp_path / 'skills' / 'custom'))
    target = __import__('pathlib').Path(workspace_repo_dir('gitlab@corp:g/sub/p'))
    other = __import__('pathlib').Path(workspace_repo_dir('gitlab:g/sub/p'))
    legacy = tmp_path / 'g' / 'sub' / 'p'
    for folder in (target, other, legacy):
        folder.mkdir(parents=True)
    keys = _normalize_keys('gitlab@corp:g/sub/p')
    assert keys == ['gitlab@corp:g/sub/p']
    _cleanup_files(keys, 'g/sub', 'p', {'skill_dirs': 0, 'workspace_dirs': 0})
    assert not target.exists()
    assert other.exists() and legacy.exists()


def test_chat_token_never_uses_gitcode_token_for_gitlab(instances, monkeypatch):
    from src.api.routes.chat import _resolve_gitcode_token
    monkeypatch.setattr('src.vcs.gitlab_credentials.repo_token', lambda key: 'isolated-private-token')
    assert _resolve_gitcode_token('wrong-gitcode-token', 'gitlab@corp:g/p') == 'isolated-private-token'


def test_project_creation_uses_private_instance_and_group(instances):
    from src.vcs.gitlab_manager import create_project
    with patch('src.vcs.gitlab_manager.request', side_effect=[{'id': 7}, {'path_with_namespace': 'g/p'}]) as send:
        project = create_project('test', 'p', namespace='g', provider='gitlab@corp')
    assert send.call_args.args[0:3] == ('gitlab@corp', 'POST', '/projects')
    assert send.call_args.args[4]['namespace_id'] == 7
    assert send.call_args.args[4]['visibility'] == 'private'
    assert project['full_name'] == 'g/p'


def test_gitlab_repo_exists_only_treats_404_as_deleted():
    from src.vcs.api_client import VcsApiClient
    client = VcsApiClient('gitlab', 'test', 'g', 'p')
    with patch.object(client, 'get_repo_info', return_value={'error': 'GitLab API failed (404)'}):
        assert client.repo_exists() is False
    with patch.object(client, 'get_repo_info', return_value={'error': 'GitLab API failed (403)'}):
        assert client.repo_exists() is True


def test_no_global_token_lookup_excludes_gitlab_env(instances, monkeypatch):
    from src.user.db import get_repo_bot_token_no_global
    monkeypatch.setattr('src.user.db.get_host_user_id', lambda key: 0)
    assert get_repo_bot_token_no_global('gitlab@corp:g/p') == ''


def test_remote_task_keeps_its_scoped_gitlab_transport_token(instances):
    from src.user.db import get_task_token
    assert get_task_token({'repo_key': 'gitlab@corp:g/p', 'repo_gitcode_token': 'scoped-worker-token'}) == 'scoped-worker-token'


def test_host_reservation_rejects_concurrent_requests(api_client):
    from src.api.routes.gitlab import _reserve_host
    from fastapi import HTTPException
    _, user = api_client
    secret = _reserve_host(user, user['repo_key'], 'group/subgroup/project')
    assert secret
    with pytest.raises(HTTPException) as busy:
        _reserve_host(user, user['repo_key'], 'group/subgroup/project')
    assert busy.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        _reserve_host({**user, 'id': user['id'] + 1}, user['repo_key'], 'group/subgroup/project')
    assert caught.value.status_code == 403


def test_gitlab_git_auth_is_url_scoped_and_enforces_tls(instances, monkeypatch):
    from src.runtime.repogit import _run_git
    monkeypatch.setenv('GIT_SSL_NO_VERIFY', 'true')
    with patch('src.runtime.repogit.subprocess.run') as run:
        _run_git(['ls-remote', 'https://gitlab.example.test/gitlab/g/p.git'], 'test-token', repo_key='gitlab@corp:g/p')
    env = run.call_args.kwargs['env']
    assert 'GIT_SSL_NO_VERIFY' not in env
    assert env['GIT_CONFIG_KEY_2'] == 'http.https://gitlab.example.test/gitlab/.extraHeader'


def test_notes_on_commits_do_not_start_issue_tasks():
    from src.vcs.gitlab import GitLabBackend
    payload = {'object_kind': 'note', 'project': {'path_with_namespace': 'g/p'},
               'object_attributes': {'noteable_type': 'Commit', 'note': 'test'}}
    assert GitLabBackend().normalize(json.dumps(payload).encode(), 'Note Hook')['type'] == 'note'


def test_contents_subdirectory_fallback_fetches_all_pages():
    from src.vcs.providers.gitlab import Adapter
    from src.vcs.gitlab_http import GitLabError
    adapter = Adapter()
    first = [{'name': str(i), 'type': 'blob'} for i in range(20)]
    with patch('src.vcs.providers.gitlab.api_request', side_effect=[GitLabError(404), first, [{'name': 'last'}]]) as send:
        result = adapter.request('GET', adapter.url('group', 'project', '/contents/src', {'ref': 'feature/a'}), 'test')
    assert len(result) == 21
    assert send.call_args.kwargs['params'] == {'path': 'src', 'ref': 'feature/a', 'page': 2}


def test_failed_host_setup_does_not_leave_managed_repo(api_client, monkeypatch):
    from src.api.routes import gitlab
    from src.session_manager import db
    client, user = api_client
    user['auth_type'] = 'jwt'
    monkeypatch.setattr(gitlab, '_token', lambda *a: 'test')
    monkeypatch.setattr('src.user.db.get_meta_value', lambda *a: 'https://grape.example.test')
    with patch.object(gitlab, '_api', side_effect=[{'id': 1}, {'path_with_namespace': 'group/subgroup/project'}, {'access_level': 40}]), patch.object(gitlab, 'setup_webhook', side_effect=RuntimeError('remote unavailable')):
        response = client.post('/api/gitlab/host', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project'})
    assert response.status_code == 502
    assert db.execute('SELECT 1 FROM managed_repos WHERE repo_key=?', (user['repo_key'],)).fetchone() is None


def test_webhook_persists_before_ack_and_storage_failure_is_retryable(api_client):
    from src.user.db import add_managed_repo
    from src.vcs.gitlab_inbox import get_inbox
    import uuid
    client, user = api_client
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'], webhook_secret='hook-secret')
    payload = {'object_kind': 'issue', '_internal': True, 'project': {'path_with_namespace': 'group/subgroup/project', 'web_url': 'https://gitlab.example.test/gitlab/group/subgroup/project'}}
    headers = {'X-Gitlab-Token': 'hook-secret', 'X-Gitlab-Event': 'Issue Hook', 'X-Gitlab-Event-UUID': uuid.uuid4().hex}
    response = client.post('/webhook/gitlab/corp', json=payload, headers=headers)
    assert response.status_code == 200
    queue = get_inbox()
    job = queue.claim()
    assert job['repo_key'] == user['repo_key']
    assert 'hook-secret' not in str(job) and '_internal' not in job['body']
    assert client.post('/webhook/gitlab/corp', json=payload, headers=headers).json()['status'] == 'duplicate'
    queue.finish(job)
    with patch('src.vcs.gitlab_inbox.Inbox.enqueue', side_effect=RuntimeError('db unavailable')):
        assert client.post('/webhook/gitlab/corp', json=payload, headers=headers).status_code == 503


def test_failed_rehosting_preserves_existing_hook(api_client, monkeypatch):
    from src.api.routes import gitlab
    from src.user.db import add_managed_repo
    from src.session_manager import db
    client, user = api_client
    user['auth_type'] = 'jwt'
    add_managed_repo(user['repo_key'], 'group/subgroup', 'project', host_user_id=user['id'], webhook_id=42, webhook_secret='original-secret')
    monkeypatch.setattr(gitlab, '_token', lambda *a: 'test')
    monkeypatch.setattr('src.user.db.get_meta_value', lambda *a: 'https://grape.example.test')
    with patch.object(gitlab, '_api', side_effect=[{'id': 1}, {'path_with_namespace': 'group/subgroup/project'}, {'access_level': 40}]), patch.object(gitlab, 'setup_webhook', side_effect=RuntimeError('remote unavailable')):
        assert client.post('/api/gitlab/host', json={'provider': 'gitlab@corp', 'project': 'group/subgroup/project'}).status_code == 502
    assert tuple(db.execute('SELECT webhook_id, webhook_secret FROM managed_repos WHERE repo_key=?', (user['repo_key'],)).fetchone()) == (42, 'original-secret')
