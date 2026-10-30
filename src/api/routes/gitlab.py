"""Connect and host public/private GitLab projects using an existing Grape account."""
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, SecretStr

from src.api.deps import require_no_session_token, require_org_member
from src.vcs.gitlab_config import configured_providers, get_instance, validate_project
from src.vcs.gitlab_http import request as api_request, GitLabError
from src.vcs.gitlab_credentials import save_user_token, user_token
from src.vcs.gitlab_manager import fetch_repos, project_path, setup_webhook, remove_webhook

router = APIRouter(prefix='/api/gitlab', tags=['gitlab'])


class ConnectionBody(BaseModel):
    provider: str = 'gitlab'
    token: SecretStr


class ProjectBody(BaseModel):
    provider: str = 'gitlab'
    project: str


class BotTokenBody(ProjectBody):
    token: SecretStr


def require_gitlab_user(user: dict = Depends(require_org_member)):
    if user.get('auth_type') == 'session_token':
        raise HTTPException(403, 'GitLab hosting requires a user credential')
    return user


def _instance(provider):
    try:
        return get_instance(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _token(user, provider):
    _instance(provider)
    token = user_token(user['id'], provider)
    if not token:
        raise HTTPException(400, 'Bind a GitLab personal access token first')
    return token


def _api(provider, method, path, token, body=None):
    try:
        return api_request(provider, method, path, token, body)
    except GitLabError as exc:
        raise HTTPException(401 if exc.status == 401 else 403 if exc.status == 403 else 502,
                            str(exc)) from None
    except RuntimeError:
        raise HTTPException(502, 'Cannot reach GitLab API') from None


@router.get('/instances')
def instances(user: dict = Depends(require_no_session_token)):
    return {'instances': [{'provider': p, 'url': get_instance(p).url,
                           'connected': bool(user_token(user['id'], p))} for p in configured_providers()]}


@router.put('/connection')
def connect(body: ConnectionBody, user: dict = Depends(require_no_session_token)):
    _instance(body.provider)
    token = body.token.get_secret_value().strip()
    if not token:
        raise HTTPException(400, 'Token must not be empty')
    identity = _api(body.provider, 'GET', '/user', token)
    if not identity.get('id') or identity.get('state') != 'active':
        raise HTTPException(400, 'GitLab account is not active')
    save_user_token(user['id'], body.provider, token, identity.get('username', ''))
    return {'provider': body.provider, 'connected': True, 'username': identity.get('username', '')}


@router.delete('/connection')
def disconnect(provider: str = 'gitlab', user: dict = Depends(require_no_session_token)):
    _instance(provider)
    from src.session_manager import db, db_lock
    # Removing a binding would break hosted projects using this user's credential.
    with db_lock:
        row = db.execute('SELECT 1 FROM managed_repos WHERE host_user_id=? AND repo_key LIKE ? LIMIT 1',
                         (user['id'], provider + ':%')).fetchone()
        if row:
            raise HTTPException(409, 'Remove hosted GitLab projects before disconnecting')
        db.execute('DELETE FROM user_credentials WHERE user_id=? AND provider=?', (user['id'], provider))
        db.commit()
    return {'connected': False}


@router.get('/projects')
def projects(provider: str = 'gitlab', user: dict = Depends(require_gitlab_user)):
    token = _token(user, provider)
    try:
        repos = fetch_repos(token, provider)
    except RuntimeError:
        raise HTTPException(502, 'Cannot list GitLab projects') from None
    from src.api.routes.repos import _filter_repos
    return {'repos': _filter_repos(user['id'], repos, user.get('org_id', 0))}


def _validate_body(body):
    _instance(body.provider)
    try:
        validate_project(body.project)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return f'{body.provider}:{body.project}'


def _check_host_owner(user, repo_key):
    from src.session_manager import db, db_lock
    with db_lock:
        row = db.execute('SELECT host_user_id, org_id, webhook_id FROM managed_repos WHERE repo_key=?', (repo_key,)).fetchone()
    if row and (int(row[0] or 0) != user['id'] or int(row[1] or 0) != int(user.get('org_id', 0))):
        raise HTTPException(403, 'Repository is hosted by another account or organization')
    return row


def _reserve_host(user, repo_key, project):
    """Keep in-flight reservations separate from successfully hosted repositories."""
    from src.session_manager import db, db_lock
    with db_lock:
        existing = _check_host_owner(user, repo_key)
        db.execute('CREATE TABLE IF NOT EXISTS gitlab_host_reservations ('
                   'repo_key VARCHAR(512) PRIMARY KEY, user_id INTEGER NOT NULL, '
                   'org_id INTEGER NOT NULL, secret VARCHAR(128) NOT NULL, expires_at DOUBLE NOT NULL)')
        db.execute('DELETE FROM gitlab_host_reservations WHERE expires_at<?', (time.time(),))
        row = db.execute('SELECT user_id, org_id, secret FROM gitlab_host_reservations WHERE repo_key=?', (repo_key,)).fetchone()
        if row:
            if int(row[0]) != user['id'] or int(row[1]) != int(user.get('org_id', 0)):
                raise HTTPException(403, 'Repository hosting is reserved by another account or organization')
            raise HTTPException(409, 'GitLab hosting is already in progress; retry shortly')
        secret = ''
        if existing:
            row = db.execute('SELECT webhook_secret FROM managed_repos WHERE repo_key=?', (repo_key,)).fetchone()
            secret = row[0] if row else ''
        secret = secret or secrets.token_urlsafe(32)
        try:
            db.execute('INSERT INTO gitlab_host_reservations (repo_key,user_id,org_id,secret,expires_at) VALUES (?,?,?,?,?)',
                       (repo_key, user['id'], user.get('org_id', 0), secret, time.time() + 300))
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(409, 'Concurrent GitLab hosting; retry shortly') from None
        return secret


def _release_host_reservation(repo_key, secret):
    from src.session_manager import db, db_lock
    with db_lock:
        db.execute('DELETE FROM gitlab_host_reservations WHERE repo_key=? AND secret=?', (repo_key, secret))
        db.commit()


@router.post('/host')
def host(body: ProjectBody, request: Request, user: dict = Depends(require_gitlab_user)):
    repo_key = _validate_body(body)
    _check_host_owner(user, repo_key)
    token = _token(user, body.provider)
    identity = _api(body.provider, 'GET', '/user', token)
    project = _api(body.provider, 'GET', project_path(body.project), token)
    canonical = project.get('path_with_namespace', '')
    if canonical != body.project:
        raise HTTPException(409, 'Project path changed; refresh project list')
    member = _api(body.provider, 'GET', project_path(body.project) + '/members/all/' + str(identity['id']), token)
    if member.get('access_level', 0) < 40:
        raise HTTPException(403, 'GitLab Maintainer permission is required for hosting')
    from src.user.db import get_meta_value, add_managed_repo, mark_user_repo_join, sync_host_vcs_username
    base = (get_meta_value('server_base_url') or (str(request.base_url) if request else '')).rstrip('/')
    if urlsplit(base).scheme != 'https':
        raise HTTPException(400, 'Configure a reachable HTTPS server_base_url before hosting')
    secret = _reserve_host(user, repo_key, body.project)
    from src.vcs.gitlab_config import instance_name
    hook_url = base + '/webhook/gitlab/' + instance_name(body.provider)
    try:
        try:
            hook_id = setup_webhook(token, body.project, hook_url, secret, body.provider)
        except RuntimeError:
            raise HTTPException(502, 'Cannot configure GitLab webhook; retry hosting') from None
        owner, _, name = body.project.rpartition('/')
        if not add_managed_repo(repo_key, owner, name, webhook_id=hook_id, host_user_id=user['id'],
                                webhook_secret=secret, org_id=user.get('org_id', 0)):
            raise HTTPException(500, 'Webhook configured but local save failed; retry hosting to reconcile')
    finally:
        _release_host_reservation(repo_key, secret)
    sync_host_vcs_username(repo_key, identity.get('username', ''))
    mark_user_repo_join(user['id'], repo_key, True, True)
    return {'status': 'ok', 'repo_key': repo_key, 'webhook_id': hook_id}


@router.post('/remove')
def remove(body: ProjectBody, user: dict = Depends(require_gitlab_user)):
    repo_key = _validate_body(body)
    existing = _check_host_owner(user, repo_key)
    if not existing:
        raise HTTPException(404, 'Repository is not hosted')
    token = _token(user, body.provider)
    if existing[2]:
        try:
            remove_webhook(token, body.project, existing[2], body.provider)
        except RuntimeError:
            raise HTTPException(502, 'Cannot remove GitLab webhook; local hosting preserved') from None
    from src.user.db import remove_managed_repo
    from src.repo_cleanup import cascade_cleanup_repo_data
    owner, _, name = body.project.rpartition('/')
    cascade_cleanup_repo_data(repo_key, owner, name)
    remove_managed_repo(repo_key)
    return {'status': 'ok'}


@router.put('/bot-token')
def set_bot_token(body: BotTokenBody, user: dict = Depends(require_gitlab_user)):
    repo_key = _validate_body(body)
    if not _check_host_owner(user, repo_key):
        raise HTTPException(404, 'Repository is not hosted')
    token = body.token.get_secret_value().strip()
    username = ''
    if token:
        identity = _api(body.provider, 'GET', '/user', token)
        member = _api(body.provider, 'GET', project_path(body.project) + '/members/all/' + str(identity['id']), token)
        if member.get('access_level', 0) < 40:
            raise HTTPException(403, 'GitLab bot requires Maintainer permission')
        username = identity.get('username', '')
    from src.auth.credential_crypto import encrypt
    from src.session_manager import db, db_lock
    with db_lock:
        db.execute("UPDATE managed_repos SET bot_gitlab_token=?, bot_username=?, bot_token_status='' WHERE repo_key=?",
                   (encrypt(token), username, repo_key))
        db.commit()
    return {'configured': bool(token), 'bot_username': username}
