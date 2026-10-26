"""Non-secret GitLab instance configuration shared by API, hooks and git transport."""
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit


def is_gitlab(provider: str) -> bool:
    return provider == 'gitlab' or provider.startswith('gitlab@')


def instance_name(provider: str) -> str:
    if provider == 'gitlab':
        return 'default'
    if not provider.startswith('gitlab@'):
        raise ValueError('Not a GitLab provider')
    name = provider[7:]
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', name) or name == 'default':
        raise ValueError('Invalid GitLab instance name')
    return name


def validate_project(path: str) -> str:
    parts = path.split('/')
    if len(parts) < 2 or any(not p or p in ('.', '..') or not re.fullmatch(r'[\w.-]+', p, re.ASCII) for p in parts):
        raise ValueError('Invalid GitLab project path')
    return path


@dataclass(frozen=True)
class GitLabInstance:
    provider: str
    url: str
    token_env: str = ''
    ca_bundle: str = ''

    @property
    def api_base(self):
        return self.url + '/api/v4'

    @property
    def token(self):
        return os.environ.get(self.token_env, '') if self.token_env else ''

    def project_url(self, path):
        return self.api_base + '/projects/' + quote(validate_project(path), safe='')

    def web_url(self, path):
        return self.url + '/' + quote(validate_project(path), safe='/')


def get_instance(provider: str = 'gitlab') -> GitLabInstance:
    name = instance_name(provider)
    try:
        configs = json.loads(os.environ.get('GRAPE_GITLAB_INSTANCES', '{}'))
    except (ValueError, TypeError):
        raise ValueError('GRAPE_GITLAB_INSTANCES must be a JSON object') from None
    if not isinstance(configs, dict):
        raise ValueError('GRAPE_GITLAB_INSTANCES must be a JSON object')
    config = configs.get(name, {'url': 'https://gitlab.com', 'token_env': 'GITLAB_TOKEN'} if name == 'default' else None)
    if not isinstance(config, dict):
        raise ValueError('GitLab instance is not configured')
    # Credentials belong in the existing credential store or the named environment variable.
    if set(config) - {'url', 'token_env', 'ca_bundle'}:
        raise ValueError('Unsupported GitLab configuration field')
    url = str(config.get('url', '')).rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('GitLab URL must be HTTPS without credentials, query or fragment')
    if any(p in ('.', '..') for p in parsed.path.split('/')) or '\\' in url or any(c.isspace() for c in url):
        raise ValueError('Invalid GitLab URL')
    try:
        parsed.port
    except ValueError:
        raise ValueError('Invalid GitLab port') from None
    env = str(config.get('token_env', ''))
    if env and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env):
        raise ValueError('Invalid GitLab token environment variable')
    return GitLabInstance(provider, url, env, str(config.get('ca_bundle', '')))


def configured_providers() -> list[str]:
    configs = json.loads(os.environ.get('GRAPE_GITLAB_INSTANCES', '{}'))
    providers = ['gitlab'] + ['gitlab@' + key for key in configs if key != 'default']
    for provider in providers:
        get_instance(provider)
    return providers
