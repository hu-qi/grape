"""Validated Gitea login instances, shared by configuration and OAuth routes."""
import re
from urllib.parse import urlsplit, urlunsplit


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or '\\' in value or any(c.isspace() for c in value)):
        raise ValueError('Gitea 地址必须是 HTTP/HTTPS URL，不能包含凭据、查询参数或片段')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Gitea 地址端口无效') from exc
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    default_port = 443 if parsed.scheme == 'https' else 80
    netloc = host + (f':{port}' if port and port != default_port else '')
    path = parsed.path.rstrip('/')
    if any(part in ('.', '..') for part in path.split('/')):
        raise ValueError('Gitea 地址不能包含相对路径')
    return urlunsplit((parsed.scheme, netloc, path, '', ''))


def validate_config(data: dict, previous: dict) -> dict:
    mode = data.get('gitea_mode', 'single')
    if mode not in ('single', 'multi'):
        raise ValueError('Gitea 模式必须为 single 或 multi')
    old = {item['id']: item for item in previous.get('gitea_instances', [])}
    instances = []
    seen = set()
    origins = set()
    for item in data.get('gitea_instances', []):
        key = item.get('id', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', key) or key in seen:
            raise ValueError('Gitea 实例 ID 必须唯一，且仅含小写字母、数字、下划线和短横线')
        seen.add(key)
        base = normalize_url(item.get('base_url') or 'https://gitea.com')
        if base in origins:
            raise ValueError('同一个 Gitea 地址只能配置一次')
        origins.add(base)
        previous_item = old.get(key, {})
        if previous_item and previous_item['base_url'] != base:
            raise ValueError('已有实例不能修改地址，请删除后使用新的实例 ID 添加')
        redirect = item.get('redirect_uri', '').strip()
        if redirect:
            redirect = normalize_url(redirect)
            if not redirect.endswith('/api/auth/callback/gitea'):
                raise ValueError('Gitea 回调地址必须以 /api/auth/callback/gitea 结尾')
        entry = dict(id=key, name=item.get('name', '').strip() or key, base_url=base,
                     client_id=item.get('client_id', '').strip(),
                     client_secret=item.get('client_secret') or previous_item.get('client_secret', ''),
                     redirect_uri=redirect, enabled=item.get('enabled', True),
                     allow_token_login=bool(item.get('allow_token_login', False)),
                     allowed_usernames=item.get('allowed_usernames', []))
        if not isinstance(entry['enabled'], bool):
            raise ValueError('Gitea enabled 必须为布尔值')
        if not isinstance(entry['allowed_usernames'], list) or not all(isinstance(x, str) for x in entry['allowed_usernames']):
            raise ValueError('Gitea 用户白名单必须为字符串列表')
        if entry['enabled'] and not entry['allow_token_login'] \
                and not all(entry[k] for k in ('client_id', 'client_secret', 'redirect_uri')):
            raise ValueError('启用 Gitea 登录前必须填写 Client ID、Secret 和回调地址（或开启 Token 登录）')
        instances.append(entry)
    default = data.get('gitea_default_instance', '')
    if instances and default not in seen:
        raise ValueError('请选择有效的默认 Gitea 实例')
    return dict(gitea_mode=mode, gitea_default_instance=default, gitea_instances=instances)


def public_config(data: dict) -> dict:
    return dict(gitea_mode=data.get('gitea_mode', 'single'),
                gitea_default_instance=data.get('gitea_default_instance', ''),
                gitea_instances=[{**{k: v for k, v in item.items() if k != 'client_secret'},
                                   'client_secret_present': bool(item.get('client_secret'))}
                                  for item in data.get('gitea_instances', [])])


def login_instances() -> tuple[str, list[dict]]:
    from src.admin.config_store import load_config
    data = load_config()
    mode = data.get('gitea_mode', 'single')
    entries = [item for item in data.get('gitea_instances', [])
               if item.get('enabled')
               and (all(item.get(k) for k in ('client_id', 'client_secret', 'redirect_uri'))
                    or item.get('allow_token_login'))]
    if mode == 'single':
        entries = [item for item in entries if item['id'] == data.get('gitea_default_instance')]
    return mode, entries


def get_login_instance(key: str = '') -> dict:
    from src.admin.config_store import load_config
    key = key or load_config().get('gitea_default_instance', '')
    for item in login_instances()[1]:
        if item['id'] == key:
            return item
    raise ValueError('Gitea 实例不存在、未启用或尚未配置完整')
