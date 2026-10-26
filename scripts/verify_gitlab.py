#!/usr/bin/env python3
"""Live adapter verification. Credentials are read from environment variables only.

Default: read-only. --write creates a NEW private project and removes ONLY that
project in finally. Existing projects are never modified. JSON output is sanitized.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def verify(provider, write=False):
    from src.vcs.gitlab_config import get_instance
    from src.vcs.gitlab_http import request, GitLabError
    from src.vcs.providers.gitlab import Adapter, translate
    from src.vcs.api_client import VcsApiClient
    from src.runtime.repogit import _run_git

    instance = get_instance(provider)
    token = instance.token
    if not token:
        return {'provider': provider, 'ok': False, 'error': 'token environment variable is empty'}
    result = {'provider': provider, 'read': {}, 'write': {}, 'cleanup': 'not_needed'}
    project_id = None
    name = 'grape-adapter-smoke-' + uuid.uuid4().hex[:12]
    adapter = Adapter(provider)

    def call(method, path, body=None, params=None):
        return request(provider, method, path, token, body, params)

    def git(args, repo_key):
        response = _run_git(args, token=token, repo_key=repo_key, timeout=60)
        if response is None or response.returncode:
            # Never print stderr/command env; remote servers may echo credentials.
            raise RuntimeError('git operation failed')
        return response

    try:
        user = call('GET', '/user')
        result['read']['authenticated'] = bool(user.get('id'))
        result['read']['version'] = call('GET', '/version').get('version')
        projects = call('GET', '/projects', params={'membership': 'true', 'per_page': 5})
        result['read']['visible_sample_count'] = len(projects)
        if projects:
            path = projects[0]['path_with_namespace']
            owner, _, repo = path.rpartition('/')
            metadata = adapter.request('GET', adapter.url(owner, repo, ''), token)
            result['read']['metadata'] = metadata.get('full_name') == path
            git(['ls-remote', '--heads', instance.web_url(path) + '.git'], provider + ':' + path)
            result['read']['git_ls_remote'] = True
            for resource in ('branches', 'issues', 'pulls'):
                data = adapter.request('GET', adapter.url(owner, repo, '/' + resource, {'per_page': 1}), token)
                result['read'][resource] = isinstance(data, list)
        if write:
            project = call('POST', '/projects', {'name': name, 'path': name, 'visibility': 'private',
                                                'initialize_with_readme': True, 'default_branch': 'main'})
            if not project.get('id') or project.get('path') != name:
                raise RuntimeError('new project identity mismatch')
            project_id = project['id']
            path = project['path_with_namespace']
            owner, _, repo = path.rpartition('/')
            repo_key = provider + ':' + path
            result['write']['private_project_created'] = project.get('visibility') == 'private'
            body = translate('issues', 'create', {'title': 'GitLab adapter smoke test', 'body': 'Temporary adapter validation.'}, {})
            issue = adapter.request('POST', adapter.url(owner, repo, '/issues'), token, body)
            issue_number = int(issue['number'])
            result['write']['issue_created'] = True
            client = VcsApiClient(provider, token, owner, repo)
            client._ledger = lambda *args, **kwargs: None  # This standalone probe must not write an application DB.
            comment = client.post_issue_comment(issue_number, 'Adapter comment validation.')
            if comment.get('error') or not comment.get('id'):
                raise RuntimeError('issue comment failed')
            result['write']['issue_comment'] = True
            with tempfile.TemporaryDirectory(prefix='grape-gitlab-smoke-') as folder:
                checkout = str(Path(folder) / 'repo')
                git(['clone', '--quiet', '--depth', '1', instance.web_url(path) + '.git', checkout], repo_key)
                result['write']['clone'] = True
                git(['-C', checkout, 'checkout', '-b', 'adapter-smoke'], repo_key)
                Path(checkout, 'adapter-smoke.txt').write_text('GitLab adapter live verification\n')
                git(['-C', checkout, 'add', 'adapter-smoke.txt'], repo_key)
                git(['-C', checkout, '-c', 'user.name=Grape Adapter Test', '-c', 'user.email=adapter-test@example.invalid',
                     'commit', '--quiet', '-m', 'test: validate GitLab adapter'], repo_key)
                git(['-C', checkout, 'push', '--quiet', 'origin', 'adapter-smoke'], repo_key)
                result['write']['push'] = True
                config = Path(checkout, '.git', 'config').read_text()
                if token in config:
                    raise RuntimeError('credential unexpectedly persisted in git config')
                result['write']['git_config_has_no_token'] = True
            mr = client.create_pr('Adapter smoke MR', 'adapter-smoke', 'main', f'Closes #{issue_number}')
            if mr.get('error') or not mr.get('number'):
                raise RuntimeError('merge request creation failed')
            result['write']['mr_created'] = True
            # GitLab computes mergeability asynchronously. Retry only the documented
            # transient HTTP 405 response, with a maximum of three attempts.
            merged = None
            for attempt in range(3):
                merged = client.merge_pr(mr['number'])
                if not merged.get('error'):
                    break
                if '(405)' not in merged['error'] or attempt == 2:
                    raise RuntimeError('merge request merge failed')
                time.sleep(2)
            result['write']['mr_merged'] = merged.get('state') == 'merged'
            closed = client.close_issue(issue_number)
            if closed.get('error') or closed.get('state') != 'closed':
                raise RuntimeError('issue close failed')
            result['write']['issue_closed'] = True
        result['ok'] = True
    except GitLabError as exc:
        result.update(ok=False, error='GitLab API rejected verification', http_status=exc.status)
    except Exception as exc:
        result.update(ok=False, error_type=type(exc).__name__)
    finally:
        if project_id is not None:
            try:
                # Confirm the disposable identity again before deleting anything.
                current = call('GET', '/projects/' + str(project_id))
                if current.get('path') != name:
                    raise RuntimeError('cleanup identity mismatch')
                call('DELETE', '/projects/' + str(project_id))
                result['cleanup'] = 'deletion_accepted'
            except Exception:
                result['cleanup'] = 'failed'
                result['cleanup_project_id'] = project_id
                result['ok'] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', action='append', default=[])
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--output', type=Path, help='Save sanitized JSON evidence')
    args = parser.parse_args()
    providers = args.provider or ['gitlab']
    # Configuration imports initialize storage in this application. Keep probes
    # isolated from the operator's application database and workspace.
    with tempfile.TemporaryDirectory(prefix='grape-gitlab-probe-') as isolated:
        os.environ['DEVOPS_DB_PATH'] = str(Path(isolated) / 'sessions.db')
        os.environ['DEVOPS_DB_BACKEND'] = 'sqlite'
        os.environ['DEVOPS_WORKSPACE_DIR'] = str(Path(isolated) / 'workspaces')
        results = [verify(provider, args.write) for provider in providers]
    import subprocess
    commit = subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parents[1]), 'rev-parse', 'HEAD'], text=True).strip()
    evidence = {'implementation_commit': commit, 'mode': 'temporary_project_write' if args.write else 'read_only', 'results': results}
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n')
    print(encoded)
    return 0 if all(result.get('ok') for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
