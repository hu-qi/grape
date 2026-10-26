"""GitLab credentials: encrypted user bindings and instance-scoped token resolution."""
from src.vcs.gitlab_config import get_instance, is_gitlab


def save_user_token(user_id, provider, token, username=''):
    from src.auth.credential_crypto import encrypt
    from src.user.db import set_credential
    get_instance(provider)
    if not set_credential(user_id, provider, {'token': encrypt(token), 'username': username}):
        raise RuntimeError('Cannot save GitLab credential')


def user_token(user_id, provider, include_expired=False):
    from src.auth.credential_crypto import decrypt
    from src.user.db import get_credential
    get_instance(provider)
    config = get_credential(user_id, provider) or {}
    if config.get('expired') and not include_expired:
        return ''
    return decrypt(config.get('token', ''))


def bound_providers(user_id):
    from src.vcs.gitlab_config import configured_providers
    return [p for p in configured_providers() if user_token(user_id, p)]


def repo_token(repo_key):
    from src.token_resolver import TokenResolver, create_provider_from_repo_key
    from src.repo_key import parse_repo_key
    if not is_gitlab(parse_repo_key(repo_key)[0]):
        raise ValueError('Not a GitLab repository')
    return TokenResolver(create_provider_from_repo_key(repo_key)).get_token(repo_key)
