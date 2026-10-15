"""Gitea OAuth: configured instances, browser-bound state and isolated identities."""
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.admin import config_store
from src.api.jwt_utils import verify_access_token


def instance(key='private_gitea', **changes):
    return dict(id=key, name=key, base_url='http://172.16.145.202:30344',
                client_id='gitea-app-id', client_secret='gitea-secret-value',
                redirect_uri='http://grape.test/api/auth/callback/gitea',
                enabled=True, **changes)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from src.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, 'COOKIE_SECURE', False)
    monkeypatch.setattr(config_store, '_GBT_PATH', str(tmp_path / 'config.json'))
    config_store.save_config({'gitea_mode': 'single', 'gitea_default_instance': 'private_gitea',
                              'gitea_instances': [instance()]})
    return TestClient(app)


def start(client, instance_id='private_gitea'):
    r = client.get('/api/auth/login/gitea', params={'instance': instance_id})
    assert r.status_code == 200
    return parse_qs(urlsplit(r.json()['redirect_url']).query)['state'][0]


def upstream():
    token = Mock()
    token.json.return_value = {'access_token': 'upstream-token-gitea', 'token_type': 'bearer'}
    user = Mock()
    user.json.return_value = {'id': 9999, 'username': 'dev', 'full_name': 'Dev User'}
    return patch('requests.post', return_value=token), patch('requests.get', return_value=user)


def finish(client, state):
    post, get = upstream()
    with post, get:
        return client.get('/api/auth/callback/gitea', params={'state': state, 'code': 'code'},
                          follow_redirects=False)


def test_masks_secret_and_preserves_on_blank_update(configured):
    public = config_store.read_public_config()['gitea_instances'][0]
    assert 'client_secret' not in public
    assert public['client_secret_present']
    config_store.save_config({'gitea_instances': [dict(public, client_secret='')]})
    assert config_store.load_config()['gitea_instances'][0]['client_secret'] == 'gitea-secret-value'


def test_single_and_multi_selection(configured):
    config_store.save_config({'gitea_instances': [instance(), dict(instance('cloud_gitea'), base_url='https://gitea.com')]})
    assert len(configured.get('/api/auth/gitea/instances').json()['instances']) == 1
    assert configured.get('/api/auth/login/gitea?instance=cloud_gitea').status_code == 400
    config_store.save_config({'gitea_mode': 'multi'})
    assert len(configured.get('/api/auth/gitea/instances').json()['instances']) == 2


def test_authorize_uses_instance_and_pkce(configured):
    r = configured.get('/api/auth/login/gitea?instance=private_gitea')
    parsed = urlsplit(r.json()['redirect_url'])
    assert parsed.netloc == '172.16.145.202:30344'
    assert parse_qs(parsed.query)['code_challenge_method'] == ['S256']
    assert 'gitea-secret-value' not in r.text


def test_login_and_replay(configured):
    state = start(configured)
    r = finish(configured, state)
    assert r.status_code == 302
    token = parse_qs(urlsplit(r.headers['location']).query)['token'][0]
    claims = verify_access_token(token)
    assert claims['login_provider'] == 'gitea'
    assert claims['login_instance'] == 'private_gitea'
    assert 'upstream-token-gitea' not in r.headers['location']
    assert finish(configured, state).status_code == 400


def test_state_bound_to_browser(configured):
    state = start(configured)
    with TestClient(app) as other:
        assert finish(other, state).status_code == 400


def test_identity_isolated_by_instance_origin(configured):
    first = finish(configured, start(configured))
    first_claims = verify_access_token(parse_qs(urlsplit(first.headers['location']).query)['token'][0])
    config_store.save_config({'gitea_mode': 'multi', 'gitea_instances': [instance(),
        dict(instance('second'), base_url='https://gitea.com')]})
    second = finish(configured, start(configured, 'second'))
    second_claims = verify_access_token(parse_qs(urlsplit(second.headers['location']).query)['token'][0])
    assert first_claims['sub'] != second_claims['sub']


def test_disabled_instance_cannot_finish(configured):
    state = start(configured)
    config_store.save_config({'gitea_instances': [dict(instance(), enabled=False)]})
    assert finish(configured, state).status_code == 400


def test_refresh_keeps_instance(configured):
    finish(configured, start(configured))
    r = configured.post('/api/auth/refresh', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert verify_access_token(r.json()['token'])['login_instance'] == 'private_gitea'


def test_rejects_duplicate_and_unsafe_urls(configured):
    for items in ([instance(), instance()], [dict(instance(), base_url='ftp://gitea.test')]):
        with pytest.raises(ValueError):
            config_store.save_config({'gitea_instances': items})


def test_upstream_failure_does_not_leak_secret(configured):
    state = start(configured)
    with patch('requests.post', side_effect=RuntimeError('gitea-secret-value')):
        r = configured.get('/api/auth/callback/gitea', params={'state': state, 'code': 'code'}, follow_redirects=False)
    assert r.status_code == 302
    assert 'gitea-secret-value' not in r.headers['location']


# ===== Gitea PAT 登录 =====

def token_instance(**changes):
    inst = dict(instance('tok'), allow_token_login=True)
    inst.update(client_id='', client_secret='', redirect_uri='')
    inst.update(changes)
    return inst


def token_login(client, tok='gtpat-abcdefghij1234567890', inst='tok'):
    user = Mock(status_code=200)
    user.json.return_value = {'id': 9999, 'login': 'dev', 'username': 'dev', 'full_name': 'Dev User'}
    with patch('requests.get', return_value=user):
        return client.post('/api/auth/gitea/token-login', json={'instance': inst, 'token': tok})


def test_token_login_creates_session(configured):
    config_store.save_config({'gitea_default_instance': 'tok', 'gitea_instances': [token_instance()]})
    r = token_login(configured)
    assert r.status_code == 200
    claims = verify_access_token(r.json()['token'])
    assert claims['login_provider'] == 'gitea'
    assert claims['login_instance'] == 'tok'
    assert r.json()['user']['username'].startswith('gt_')


def test_token_only_instance_needs_no_oauth_app(configured):
    config_store.save_config({'gitea_default_instance': 'tok', 'gitea_instances': [token_instance()]})
    assert token_login(configured).status_code == 200


def test_token_login_requires_instance_opt_in(configured):
    config_store.save_config({'gitea_default_instance': 'tok',
                              'gitea_instances': [dict(instance('tok'), allow_token_login=False)]})
    assert token_login(configured).status_code == 403


def test_token_login_rejects_invalid_token(configured):
    config_store.save_config({'gitea_default_instance': 'tok', 'gitea_instances': [token_instance()]})
    user = Mock(status_code=401)
    with patch('requests.get', return_value=user):
        r = configured.post('/api/auth/gitea/token-login', json={'instance': 'tok', 'token': 'gtpat-abcdefghij1234567890'})
    assert r.status_code == 400
    assert '无效' in r.json()['detail']['message']


def test_token_login_enforces_whitelist(configured):
    config_store.save_config({'gitea_default_instance': 'tok',
                              'gitea_instances': [dict(token_instance(), allowed_usernames=['someone_else'])]})
    assert token_login(configured).status_code == 403

