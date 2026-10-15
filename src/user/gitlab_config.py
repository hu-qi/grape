"""Validated GitLab login instances, shared by configuration and OAuth routes."""
import re
from urllib.parse import urlsplit, urlunsplit


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or '\\' in value or any(c.isspace() for c in value)):
        raise ValueError('GitLab 地址必须是 HTTP/HTTPS URL，不能包含凭据、查询参数或片段')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('GitLab 地址端口无效') from exc
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    default_port = 443 if parsed.scheme == 'https' else 80
    netloc = host + (f':{port}' if port and port != default_port else '')
    path = parsed.path.rstrip('/')
    if any(part in ('.', '..') for part in path.split('/')):
        raise ValueError('GitLab 地址不能包含相对路径')
    return urlunsplit((parsed.scheme, netloc, path, '', ''))


def validate_config(data: dict, previous: dict) -> dict:
    mode = data.get('gitlab_mode', 'single')
    if mode not in ('single', 'multi'):
        raise ValueError('GitLab 模式必须为 single 或 multi')
    old = {item['id']: item for item in previous.get('gitlab_instances', [])}
    instances = []
    seen = set()
    origins = set()
    for item in data.get('gitlab_instances', []):
        key = item.get('id', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', key) or key in seen:
            raise ValueError('GitLab 实例 ID 必须唯一，且仅含小写字母、数字、下划线和短横线')
        seen.add(key)
        base = normalize_url(item.get('base_url') or 'https://gitlab.com')
        if base in origins:
            raise ValueError('同一个 GitLab 地址只能配置一次')
        origins.add(base)
        previous_item = old.get(key, {})
        if previous_item and previous_item['base_url'] != base:
            raise ValueError('已有实例不能修改地址，请删除后使用新的实例 ID 添加')
        redirect = item.get('redirect_uri', '').strip()
        if redirect:
            redirect = normalize_url(redirect)
            if not redirect.endswith('/api/auth/callback/gitlab'):
                raise ValueError('GitLab 回调地址必须以 /api/auth/callback/gitlab 结尾')

        # 认证模式支持：auth_mode = 'none' | 'oauth' | 'token'
        # 兼容旧字段 enabled(bool) 和 allow_token_login(bool)
        auth_mode = item.get('auth_mode')
        if not auth_mode:
            if not item.get('enabled', True):
                auth_mode = 'none'
            elif item.get('allow_token_login'):
                auth_mode = 'token'
            else:
                auth_mode = 'oauth'

        if auth_mode not in ('none', 'oauth', 'token'):
            raise ValueError('GitLab 认证模式必须为 none、oauth 或 token')

        client_secret = item.get('client_secret') or previous_item.get('client_secret', '')
        token = item.get('token') or previous_item.get('token', '')

        entry = dict(
            id=key,
            name=item.get('name', '').strip() or key,
            base_url=base,
            auth_mode=auth_mode,
            enabled=(auth_mode != 'none'),
            allow_token_login=(auth_mode == 'token'),
            client_id=item.get('client_id', '').strip(),
            client_secret=client_secret,
            redirect_uri=redirect,
            token=token,
            allowed_usernames=item.get('allowed_usernames', [])
        )

        if not isinstance(entry['allowed_usernames'], list) or not all(isinstance(x, str) for x in entry['allowed_usernames']):
            raise ValueError('GitLab 用户白名单必须为字符串列表')

        if entry['auth_mode'] == 'oauth':
            if not all(entry[k] for k in ('client_id', 'client_secret', 'redirect_uri')):
                raise ValueError('OAuth 模式下必须填写 Client ID、Client Secret 和回调地址')
        elif entry['auth_mode'] == 'token':
            # token 模式下可配置系统级 token（供公共兜底拉取），也可由普通用户自行输入 PAT
            pass

        instances.append(entry)
    default = data.get('gitlab_default_instance', '')
    if instances and default not in seen:
        raise ValueError('请选择有效的默认 GitLab 实例')
    return dict(gitlab_mode=mode, gitlab_default_instance=default, gitlab_instances=instances)


def public_config(data: dict) -> dict:
    return dict(gitlab_mode=data.get('gitlab_mode', 'single'),
                gitlab_default_instance=data.get('gitlab_default_instance', ''),
                gitlab_instances=[{**{k: v for k, v in item.items() if k not in ('client_secret', 'token')},
                                   'client_secret_present': bool(item.get('client_secret')),
                                   'token_present': bool(item.get('token'))}
                                  for item in data.get('gitlab_instances', [])])


def login_instances() -> tuple[str, list[dict]]:
    from src.admin.config_store import load_config
    data = load_config()
    mode = data.get('gitlab_mode', 'single')
    entries = []
    for item in data.get('gitlab_instances', []):
        auth_mode = item.get('auth_mode')
        if not auth_mode:
            # 兼容旧结构
            if not item.get('enabled', True):
                auth_mode = 'none'
            elif item.get('allow_token_login'):
                auth_mode = 'token'
            else:
                auth_mode = 'oauth'
        if auth_mode == 'none':
            continue
        if auth_mode == 'oauth' and all(item.get(k) for k in ('client_id', 'client_secret', 'redirect_uri')):
            entries.append(item)
        elif auth_mode == 'token':
            entries.append(item)

    if mode == 'single':
        entries = [item for item in entries if item['id'] == data.get('gitlab_default_instance')]
    return mode, entries


def get_login_instance(key: str = '') -> dict:
    from src.admin.config_store import load_config
    key = key or load_config().get('gitlab_default_instance', '')
    for item in login_instances()[1]:
        if item['id'] == key:
            return item
    raise ValueError('GitLab 实例不存在、未启用或尚未配置完整')
