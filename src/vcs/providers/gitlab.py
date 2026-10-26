"""GitLab neutral API adapter, bound to one public or self-managed instance."""
import re
from urllib.parse import quote, urlsplit, parse_qsl, urlencode

from src.vcs.gitlab_config import get_instance
from src.vcs.gitlab_http import request as api_request, GitLabError

API_BASE = 'https://gitlab.com/api/v4'


def query_state(state):
    return {'open': 'opened', 'close': 'closed'}.get(state, state)


def translate(resource, op, neutral, ctx):
    result = dict(neutral)
    if 'body' in result:
        result['description'] = result.pop('body')
    if 'state' in result:
        state = result.pop('state')
        if state:
            states = {'open': 'reopen', 'opened': 'reopen', 'reopen': 'reopen', 'closed': 'close', 'close': 'close'}
            if state not in states:
                raise ValueError('Unsupported GitLab state')
            result['state_event'] = states[state]
    for old, new in [('head', 'source_branch'), ('base', 'target_branch')]:
        if old in result:
            result[new] = result.pop(old)
    if result.get('assignee'):
        # Resolved against the same instance in Adapter.request.
        result['_assignee_username'] = result.pop('assignee')
    result.pop('assignee', None)
    nums = result.pop('related_issue_nums', [])
    if nums:
        result['description'] = (result.get('description', '').rstrip() + '\n\n' + ' '.join(f'Closes #{n}' for n in nums)).strip()
    result.pop('auto_close_issues', None)
    return {k: v for k, v in result.items() if v is not None and v != ''}


def normalize_response(data):
    if isinstance(data, list):
        return [normalize_response(item) for item in data]
    if not isinstance(data, dict):
        return data
    result = dict(data)
    for native, neutral in [('iid', 'number'), ('description', 'body'), ('web_url', 'html_url'),
                             ('http_url_to_repo', 'clone_url'), ('path_with_namespace', 'full_name'),
                             ('username', 'login'), ('file_path', 'path'), ('content_sha256', 'sha')]:
        if native in data:
            result[neutral] = data[native]
    if 'new_path' in data:
        result['filename'] = data['new_path']
        result['patch'] = data.get('diff', '')
        result['status'] = 'added' if data.get('new_file') else 'removed' if data.get('deleted_file') else 'renamed' if data.get('renamed_file') else 'modified'
    if 'merge_commit_sha' in data:
        result['sha'] = data['merge_commit_sha']
    if 'file_path' in data:
        result['type'] = 'file'
        result['name'] = data.get('file_name', data['file_path'].rsplit('/', 1)[-1])
        result['sha'] = data.get('blob_id', data.get('content_sha256', ''))
    if 'source_branch' in data:
        result['head'] = {'ref': data['source_branch']}
        result['base'] = {'ref': data.get('target_branch', '')}
        result['merged'] = data.get('state') == 'merged'
    for key in ('author', 'assignee', 'assignees', 'user'):
        if key in data:
            result[key] = normalize_response(data[key])
    if isinstance(data.get('author'), dict):
        result['user'] = normalize_response(data['author'])
    return result


class Adapter:
    translate = staticmethod(translate)
    query_state = staticmethod(query_state)

    def __init__(self, provider='gitlab'):
        self.provider = provider
        self.instance = get_instance(provider)
        self.API_BASE = self.instance.api_base

    def url(self, owner, repo, subpath, params=None):
        params = dict(params or {})
        path = subpath
        if path.startswith('/contents/'):
            file_path = path[len('/contents/'):]
            if file_path:
                path = '/repository/files/' + quote(file_path, safe='')
            else:
                path = '/repository/tree'
            params.setdefault('ref', 'HEAD')
        elif path.startswith('/git/trees/'):
            params['ref'] = path[len('/git/trees/'):]
            path = '/repository/tree'
        elif path.startswith(('/branches', '/commits', '/tags')):
            path = '/repository' + path
        path = re.sub(r'^/pulls(?=/|$)', '/merge_requests', path)
        path = re.sub(r'^/(issues|merge_requests)/(\d+)/comments$', r'/\1/\2/notes', path)
        path = re.sub(r'^/merge_requests/(\d+)/files$', r'/merge_requests/\1/diffs', path)
        if 'state' in params:
            params['state'] = query_state(params['state'])
        url = self.instance.project_url(f'{owner}/{repo}') + path
        query = urlencode({k: v for k, v in params.items() if v not in (None, '', [])})
        return url + ('?' + query if query else '')

    def request(self, method, url, token, body=None):
        if not url.startswith(self.API_BASE + '/'):
            raise ValueError('GitLab URL does not match credential instance')
        path = url[len(self.API_BASE):]
        payload = dict(body) if body is not None else None
        if payload is not None and not re.search(r'/notes(?:\?|$)', path):
            payload = translate('', '', payload, {})
        if payload and '_assignee_username' in payload:
            username = payload.pop('_assignee_username')
            users = api_request(self.provider, 'GET', '/users', token, params={'username': username})
            match = next((u for u in users if u.get('username', '').lower() == username.lower()), None)
            if not match:
                raise ValueError('GitLab assignee was not found')
            payload['assignee_ids'] = [match['id']]
        if method == 'PATCH':
            method = 'PUT'
        # GitLab files API requires ref even when callers omit it.
        split = urlsplit(path)
        if '/repository/files/' in split.path:
            params = dict(parse_qsl(split.query))
            params['ref'] = params.get('ref') or 'HEAD'
            path = split.path + '?' + urlencode(params)
        try:
            data = api_request(self.provider, method, path, token, payload)
        except GitLabError as exc:
            # The neutral contents endpoint accepts either a file or a directory.
            if exc.status != 404 or method != 'GET' or '/repository/files/' not in split.path:
                raise
            from urllib.parse import unquote
            base, file_path = split.path.split('/repository/files/', 1)
            data = api_request(self.provider, 'GET', base + '/repository/tree', token,
                               params={'path': unquote(file_path), 'ref': params['ref']})
        if method == 'GET' and '/repository/tree' in path and isinstance(data, list):
            # Directory/tree callers need the complete listing, not a truncated first page.
            parts = urlsplit(path)
            query = dict(parse_qsl(parts.query))
            page = int(query.get('page', 1))
            size = int(query.get('per_page', 20))
            batch = data
            while len(batch) == size:
                page += 1
                query['page'] = page
                batch = api_request(self.provider, 'GET', parts.path, token, params=query)
                data.extend(batch)
        return normalize_response(data)

    def bot_token(self, repo_key):
        from src.vcs.gitlab_credentials import repo_token
        return repo_token(repo_key)

    def user_token(self, username, user_id):
        from src.vcs.gitlab_credentials import user_token
        return user_token(user_id, self.provider)


def request(method, url, token, body=None):
    return Adapter().request(method, url, token, body)


def bot_token(repo_key):
    from src.vcs.gitlab_credentials import repo_token
    return repo_token(repo_key)


def user_token(username, user_id):
    from src.vcs.gitlab_credentials import user_token as get_token
    return get_token(user_id, 'gitlab')
