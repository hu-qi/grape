"""GitLab HTTP transport: verified TLS, bounded requests and credential-free errors."""
import requests
from .gitlab_config import get_instance


class GitLabError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'GitLab API failed ({status})')


def request(provider, method, path, token='', body=None, params=None):
    instance = get_instance(provider)
    if not path.startswith('/') or path.startswith('//') or '://' in path:
        raise ValueError('GitLab request requires an instance-relative API path')
    try:
        response = requests.request(method, instance.api_base + path,
                                    headers={'PRIVATE-TOKEN': token, 'Accept': 'application/json'},
                                    json=body, params=params, timeout=30,
                                    verify=instance.ca_bundle or True, allow_redirects=False)
    except requests.RequestException:
        # Request exceptions can contain authenticated URLs, proxy credentials or headers.
        raise RuntimeError('Cannot reach GitLab API') from None
    if not 200 <= response.status_code < 300:
        raise GitLabError(response.status_code)
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        raise RuntimeError('GitLab API returned invalid JSON') from None
