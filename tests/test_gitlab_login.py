"""GitLab OAuth: configured instances, browser-bound state and isolated identities."""
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.admin import config_store
from src.api.jwt_utils import verify_access_token


def instance(key='private', **changes):
    return dict(id=key, name=key, base_url='https://gitlab.example.test',
                client_id='app-id', client_secret='secret-value',
                redirect_uri='https://grape.test/api/auth/callback/gitlab',
                enabled=True, **changes)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from src.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, 'COOKIE_SECURE', False)
    monkeypatch.setattr(config_store, '_GBT_PATH', str(tmp_path / 'config.json'))
    config_store.save_config({'gitlab_mode': 'single', 'gitlab_default_instance': 'private',
                              'gitlab_instances': [instance()]})
    return TestClient(app)


def start(client, instance_id='private'):
    r = client.get('/api/auth/login/gitlab', params={'instance': instance_id})
    assert r.status_code == 200
    return parse_qs(urlsplit(r.json()['redirect_url']).query)['state'][0]


def upstream():
    token = Mock()
    token.json.return_value = {'access_token': 'upstream-secret', 'refresh_token': 'refresh-secret'}
    user = Mock()
    user.json.return_value = {'id': 4242, 'username': 'alice', 'name': 'Alice'}
    return patch('requests.post', return_value=token), patch('requests.get', return_value=user)


def finish(client, state):
    post, get = upstream()
    with post, get:
        return client.get('/api/auth/callback/gitlab', params={'state': state, 'code': 'code'},
                          follow_redirects=False)


def test_masks_secret_and_preserves_on_blank_update(configured):
    public = config_store.read_public_config()['gitlab_instances'][0]
    assert 'client_secret' not in public
    assert public['client_secret_present']
    config_store.save_config({'gitlab_instances': [dict(public, client_secret='')]})
    assert config_store.load_config()['gitlab_instances'][0]['client_secret'] == 'secret-value'


def test_single_and_multi_selection(configured):
    config_store.save_config({'gitlab_instances': [instance(), dict(instance('public'), base_url='https://gitlab.com')]})
    assert len(configured.get('/api/auth/gitlab/instances').json()['instances']) == 1
    assert configured.get('/api/auth/login/gitlab?instance=public').status_code == 400
    config_store.save_config({'gitlab_mode': 'multi'})
    assert len(configured.get('/api/auth/gitlab/instances').json()['instances']) == 2


def test_seed_token_mode_enables_login_without_legacy_flag(configured):
    from src.user.gitlab_config import get_login_instance
    seed = {'gitlab_mode': 'single', 'gitlab_default_instance': 'private',
            'gitlab_instances': [{'id': 'private', 'name': 'Private',
                                  'base_url': 'https://gitlab.example.test', 'auth_mode': 'token'}]}
    with open(config_store._GBT_PATH, 'w', encoding='utf-8') as stream:
        json.dump(seed, stream)
    assert get_login_instance('private')['allow_token_login'] is True
    assert configured.get('/api/auth/gitlab/instances').json()['instances'][0]['allow_token_login'] is True


def test_authorize_uses_instance_and_pkce(configured):
    r = configured.get('/api/auth/login/gitlab?instance=private')
    parsed = urlsplit(r.json()['redirect_url'])
    assert parsed.netloc == 'gitlab.example.test'
    assert parse_qs(parsed.query)['code_challenge_method'] == ['S256']
    assert 'secret-value' not in r.text


def test_login_and_replay(configured):
    state = start(configured)
    r = finish(configured, state)
    assert r.status_code == 302
    token = parse_qs(urlsplit(r.headers['location']).query)['token'][0]
    claims = verify_access_token(token)
    assert claims['login_provider'] == 'gitlab'
    assert claims['login_instance'] == 'private'
    assert 'upstream-secret' not in r.headers['location']
    assert finish(configured, state).status_code == 400


def test_state_bound_to_browser(configured):
    state = start(configured)
    with TestClient(app) as other:
        assert finish(other, state).status_code == 400


def test_identity_isolated_by_instance_origin(configured):
    first = finish(configured, start(configured))
    first_claims = verify_access_token(parse_qs(urlsplit(first.headers['location']).query)['token'][0])
    config_store.save_config({'gitlab_mode': 'multi', 'gitlab_instances': [instance(),
        dict(instance('second'), base_url='https://other.gitlab.test')]})
    second = finish(configured, start(configured, 'second'))
    second_claims = verify_access_token(parse_qs(urlsplit(second.headers['location']).query)['token'][0])
    assert first_claims['sub'] != second_claims['sub']


def test_disabled_instance_cannot_finish(configured):
    state = start(configured)
    config_store.save_config({'gitlab_instances': [dict(instance(), enabled=False)]})
    assert finish(configured, state).status_code == 400


def test_refresh_keeps_instance(configured):
    finish(configured, start(configured))
    r = configured.post('/api/auth/refresh', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert verify_access_token(r.json()['token'])['login_instance'] == 'private'


def test_rejects_duplicate_and_unsafe_urls(configured):
    for items in ([instance(), instance()], [dict(instance(), base_url='https://user:pass@gitlab.test')]):
        with pytest.raises(ValueError):
            config_store.save_config({'gitlab_instances': items})


def test_upstream_failure_does_not_leak_secret(configured):
    state = start(configured)
    with patch('requests.post', side_effect=RuntimeError('secret-value')):
        r = configured.get('/api/auth/callback/gitlab', params={'state': state, 'code': 'code'}, follow_redirects=False)
    assert r.status_code == 302
    assert 'secret-value' not in r.headers['location']


# ===== GitLab PAT 登录（实例禁用「添加新应用」时免建 OAuth 应用） =====

def token_instance(**changes):
    inst = dict(instance('tok'), allow_token_login=True)
    inst.update(client_id='', client_secret='', redirect_uri='')
    inst.update(changes)
    return inst


def token_login(client, tok='glpat-abcdefghij1234567890', inst='tok'):
    user = Mock(status_code=200)
    user.json.return_value = {'id': 4242, 'username': 'alice', 'name': 'Alice'}
    with patch('requests.get', return_value=user):
        return client.post('/api/auth/gitlab/token-login', json={'instance': inst, 'token': tok})


def test_token_login_creates_session(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance()]})
    r = token_login(configured)
    assert r.status_code == 200
    claims = verify_access_token(r.json()['token'])
    assert claims['login_provider'] == 'gitlab'
    assert claims['login_instance'] == 'tok'
    assert r.json()['user']['username'].startswith('gl_')


def test_token_only_instance_needs_no_oauth_app(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance()]})
    assert token_login(configured).status_code == 200


def test_token_login_requires_instance_opt_in(configured):
    config_store.save_config({'gitlab_default_instance': 'tok',
                              'gitlab_instances': [dict(instance('tok'), allow_token_login=False)]})
    assert token_login(configured).status_code == 403


def test_token_login_rejects_invalid_token(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance()]})
    user = Mock(status_code=401)
    with patch('requests.get', return_value=user):
        r = configured.post('/api/auth/gitlab/token-login',
                            json={'instance': 'tok', 'token': 'glpat-abcdefghij1234567890'})
    assert r.status_code == 400
    assert '无效' in r.json()['detail']['message']


def test_token_login_rejects_bad_format_without_upstream(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance()]})
    with patch('requests.get') as mock_get:
        r = configured.post('/api/auth/gitlab/token-login', json={'instance': 'tok', 'token': 'short'})
    assert r.status_code == 400
    mock_get.assert_not_called()


def test_token_login_enforces_whitelist(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance(allowed_usernames=['bob'])]})
    assert token_login(configured).status_code == 403


def test_token_login_shares_identity_with_oauth(configured):
    config_store.save_config({'gitlab_instances': [dict(instance(), allow_token_login=True)]})
    oauth = finish(configured, start(configured))
    oauth_claims = verify_access_token(parse_qs(urlsplit(oauth.headers['location']).query)['token'][0])
    token_claims = verify_access_token(token_login(configured, inst='private').json()['token'])
    assert oauth_claims['sub'] == token_claims['sub']


def test_disabled_token_instance_rejected(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance(enabled=False)]})
    assert token_login(configured).status_code == 400


def test_token_login_upstream_failure_is_sanitized(configured):
    config_store.save_config({'gitlab_default_instance': 'tok', 'gitlab_instances': [token_instance()]})
    with patch('requests.get', side_effect=RuntimeError('glpat-secret-value')):
        r = configured.post('/api/auth/gitlab/token-login',
                            json={'instance': 'tok', 'token': 'glpat-abcdefghij1234567890'})
    assert r.status_code == 502
    assert 'glpat-secret-value' not in r.text


def test_auth_mode_token_saves_and_persists_token(configured):
    config_store.save_config({
        'gitlab_mode': 'single',
        'gitlab_default_instance': 'tok_inst',
        'gitlab_instances': [dict(
            id='tok_inst',
            name='Token GitLab',
            base_url='https://gitlab.example.test',
            auth_mode='token',
            token='glpat-12345678901234567890',
            allowed_usernames=[]
        )]
    })
    cfg = config_store.load_config()
    inst = cfg['gitlab_instances'][0]
    assert inst['auth_mode'] == 'token'
    assert inst['allow_token_login'] is True
    assert inst['token'] == 'glpat-12345678901234567890'

    pub = config_store.read_public_config()
    pub_inst = pub['gitlab_instances'][0]
    assert pub_inst['token_present'] is True
    assert 'token' not in pub_inst
