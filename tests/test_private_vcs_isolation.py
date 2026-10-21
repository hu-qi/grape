"""Private VCS credentials must stay bound to their selected instance."""
from unittest.mock import MagicMock, patch

import pytest

from src.api.routes.repos import _get_user_provider_and_token
from src.user import db as user_db
from src.user import repo_manager


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_missing_instance_does_not_return_other_instance_credential(provider):
    getter = getattr(user_db, f'get_user_{provider}_credential')
    with patch.object(user_db, 'get_credential', side_effect=[None, {
        'token': 'other-instance-token', 'instance': 'other',
    }]):
        assert getter(123, instance_id='selected') is None


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_legacy_same_instance_credential_remains_supported(provider):
    credential = {'token': 'selected-token', 'instance': 'selected'}
    with patch.object(user_db, 'get_credential', side_effect=[None, credential]):
        assert getattr(user_db, f'get_user_{provider}_credential')(123, 'selected') == credential


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
@pytest.mark.parametrize('selected', [None, {'enabled': False}, {'auth_mode': 'none'}])
def test_explicit_private_session_never_falls_back_to_other_credentials(provider, selected):
    config = {
        'gitea_instances': [{'id': 'other', 'token': 'other-admin-token'}],
        'gitlab_instances': [],
    }
    if selected is not None:
        config[f'{provider}_instances'].append({
            'id': 'selected', 'token': 'disabled-token', **selected,
        })
    with patch(f'src.api.deps.get_user_{provider}_token', return_value=''), \
         patch('src.api.routes.repos.get_user_github_token', return_value='github-token'), \
         patch('src.admin.config_store.load_config', return_value=config):
        assert _get_user_provider_and_token('alice', provider, 'selected') == (provider, '')


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_repository_sync_rejects_html_proxy_response(provider):
    response = MagicMock(status_code=200)
    response.json.side_effect = ValueError('HTML response')
    with patch('requests.request', return_value=response):
        with pytest.raises(RuntimeError, match='JSON'):
            getattr(repo_manager, f'fetch_{provider}_repos_with_role')('test-token', 'https://vcs.example')


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_repository_sync_rejects_unexpected_object(provider):
    with patch.object(repo_manager, f'_{provider}_req', return_value={'message': 'proxy failure'}):
        with pytest.raises(RuntimeError, match='列表'):
            getattr(repo_manager, f'fetch_{provider}_repos_with_role')('test-token', 'https://vcs.example')


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_valid_empty_repository_list(provider):
    with patch.object(repo_manager, f'_{provider}_req', return_value=[]):
        assert getattr(repo_manager, f'fetch_{provider}_repos_with_role')('test-token', 'https://vcs.example') == []


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_selected_admin_token_uses_selected_instance_base(provider):
    from src.api.routes.repos import _private_instance_base
    config = {
        f'{provider}_default_instance': 'default',
        f'{provider}_instances': [
            {'id': 'default', 'base_url': 'https://default.example'},
            {'id': 'selected', 'base_url': 'https://selected.example/vcs/'},
        ],
    }
    with patch('src.admin.config_store.load_config', return_value=config):
        assert _private_instance_base(provider, 'selected') == 'https://selected.example/vcs'


@pytest.mark.parametrize('provider', ['gitea', 'gitlab'])
def test_missing_selected_instance_has_no_public_fallback(provider):
    from src.api.routes.repos import _private_instance_base
    with patch('src.admin.config_store.load_config', return_value={}):
        with pytest.raises(RuntimeError, match='实例地址'):
            _private_instance_base(provider, 'missing')
