"""GitLab repository discovery and webhook lifecycle."""
from urllib.parse import quote
from .gitlab_http import request, GitLabError
from .providers.gitlab import normalize_response


def project_path(path):
    from .gitlab_config import validate_project
    return '/projects/' + quote(validate_project(path), safe='')


def fetch_repos(token, provider='gitlab'):
    result = []
    page = 1
    while True:
        projects = request(provider, 'GET', '/projects', token, params={'membership': 'true', 'per_page': 100, 'page': page})
        if not isinstance(projects, list):
            raise RuntimeError('Invalid GitLab project list')
        for project in projects:
            path = project['path_with_namespace']
            namespace, _, name = path.rpartition('/')
            permissions = project.get('permissions') or {}
            access = max((permissions.get('project_access') or {}).get('access_level', 0),
                         (permissions.get('group_access') or {}).get('access_level', 0))
            result.append({**normalize_response(project), 'repo_key': f'{provider}:{path}',
                           'owner': namespace, 'name': name, 'is_admin': access >= 40})
        if len(projects) < 100:
            return result
        page += 1


def setup_webhook(token, path, url, secret, provider='gitlab'):
    # Updating an existing hook rotates its secret explicitly; never delete first.
    page = 1
    existing = None
    while True:
        hooks = request(provider, 'GET', project_path(path) + '/hooks', token, params={'per_page': 100, 'page': page})
        existing = next((h for h in hooks if h.get('url') == url), None)
        if existing or len(hooks) < 100:
            break
        page += 1
    body = {'url': url, 'token': secret, 'issues_events': True, 'merge_requests_events': True,
            'note_events': True, 'push_events': True, 'tag_push_events': True,
            'pipeline_events': True, 'releases_events': True, 'enable_ssl_verification': True}
    endpoint = project_path(path) + '/hooks'
    if existing:
        endpoint += '/' + str(existing['id'])
    hook = request(provider, 'PUT' if existing else 'POST', endpoint, token, body)
    return hook['id']


def remove_webhook(token, path, hook_id, provider='gitlab'):
    try:
        request(provider, 'DELETE', project_path(path) + '/hooks/' + str(int(hook_id)), token)
    except GitLabError as exc:
        if exc.status != 404:
            raise
    return True


def create_project(token, name, description='', private=True, namespace='', auto_init=True, default_branch='main', provider='gitlab'):
    from .gitlab_config import validate_project
    validate_project('namespace/' + name)
    body = {'name': name, 'path': name, 'description': description,
            'visibility': 'private' if private else 'public',
            'initialize_with_readme': auto_init, 'default_branch': default_branch}
    if namespace:
        group = request(provider, 'GET', '/groups/' + quote(namespace, safe=''), token)
        body['namespace_id'] = group['id']
    return normalize_response(request(provider, 'POST', '/projects', token, body))
