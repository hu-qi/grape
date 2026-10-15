"""认证路由：OAuth 登录、密码登录、用户信息、登出

JWT 双 token 机制：
  - access_token（15分钟）——存 localStorage，JS 读取加到 header
  - refresh_token（7天滚动续期）——存 HttpOnly cookie，JS 不可读
  - token_version——改密码/logout 时 +1，旧 token 失效
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from src.user import db as user_db
from src.api.deps import require_no_session_token
from src.config import log, COOKIE_SECURE
import hashlib, os, time

router = APIRouter(prefix="/api/auth", tags=["auth"])

# HttpOnly cookie 配置
_COOKIE_NAME = "refresh_token"
_COOKIE_PATH = "/api/auth"
_COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 天


def _app_base() -> str:
    """平台部署在子路径时的 URL 前缀（如生产 www.grapedev.cn/app → APP_BASE_PATH=/app）。

    默认空字符串，保持「平台部署在根路径」的行为完全不变。
    用于 OAuth 回调后的 302 跳转目标：若平台挂在 /app/ 下，必须跳到
    {prefix}/app/... 而不是根路径 /，否则会落到同域的其它站点（如官网首页）。
    """
    return (os.environ.get("APP_BASE_PATH", "") or "").rstrip("/")


def _set_refresh_cookie(response: Response, refresh_token: str):
    """设置 refresh_token HttpOnly cookie——JS 不可读，防 XSS。"""
    response.set_cookie(
        key=_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,  # 环境变量控制：生产 HTTPS true / 开发 HTTP false
        samesite="strict",
        path=_COOKIE_PATH,
        max_age=_COOKIE_MAX_AGE,
    )


def _clear_refresh_cookie(response: Response):
    """清除 refresh_token cookie——logout 时调用。"""
    response.delete_cookie(key=_COOKIE_NAME, path=_COOKIE_PATH)


class PasswordLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def password_login(body: PasswordLogin, response: Response):
    user = user_db.verify_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "用户名或密码错误"})
    # JWT 双 token——access（15分钟）+ refresh（7天滚动续期）
    from src.api.jwt_utils import create_access_token, create_refresh_token
    token_version = user.get("token_version", 0)
    access_token = create_access_token(user["id"], user["username"], token_version)
    refresh_token = create_refresh_token(user["id"], user["username"], token_version)
    # refresh_token 存 HttpOnly cookie——防 XSS
    _set_refresh_cookie(response, refresh_token)
    return {
        "token": access_token,
        "user": {"id": user["id"], "username": user["username"]}
    }


@router.post("/refresh")
def refresh_token(request: Request, response: Response):
    """用 refresh_token 换新的 access_token + refresh_token（滚动续期）。

    refresh_token 从 HttpOnly cookie 读取（不从 body 读——防 XSS）。
    CSRF 保护：要求 X-Requested-With header（同源 JS 自动加，跨站伪造无法加）。
    前端 access_token 过期（401）时自动调这个接口。
    refresh_token 也过期则返回 401——前端跳登录页。
    """
    # CSRF 保护——跨站请求伪造不会带自定义 header
    xrw = request.headers.get("X-Requested-With", "")
    if xrw != "XMLHttpRequest":
        raise HTTPException(status_code=403, detail={"code": 403, "message": "缺少 CSRF header"})
    # refresh_token 从 HttpOnly cookie 读
    refresh = request.cookies.get(_COOKIE_NAME, "")
    if not refresh:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "缺少 refresh_token"})
    from src.api.jwt_utils import verify_refresh_token, create_access_token, create_refresh_token
    payload = verify_refresh_token(refresh)
    if not payload:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "refresh_token 过期或无效"})
    uid = int(payload.get("sub", 0))
    username = payload.get("username", "")
    token_ver = int(payload.get("ver", 0))
    if not uid:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "用户不存在"})
    # token_version 校验——改密码/logout 后 +1，旧 refresh_token 失效
    db_ver = user_db.get_token_version(uid)
    if token_ver != db_ver:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "会话已失效"})
    # 滚动续期——新 access_token + 新 refresh_token
    # 新 refresh_token 继承旧 max_exp（不重新生成——30 天绝对上限）
    # login_provider 也从旧 refresh_token 继承——保证刷新后仍按登录平台选择 provider
    old_max_exp = int(payload.get("max_exp", 0))
    old_login_provider = payload.get("login_provider", "")
    old_login_instance = payload.get("login_instance", "")
    new_access_token = create_access_token(uid, username, db_ver, login_provider=old_login_provider,
                                           login_instance=old_login_instance)
    new_refresh_token = create_refresh_token(uid, username, db_ver, max_exp=old_max_exp,
                                             login_provider=old_login_provider, login_instance=old_login_instance)
    _set_refresh_cookie(response, new_refresh_token)
    return {"token": new_access_token}


@router.get("/login/gitcode")
def gitcode_oauth_login(request: Request):
    from src.user.db import get_meta_value
    client_id = get_meta_value("oauth_gitcode_client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "GitCode OAuth 未配置"})

    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI") or get_meta_value("oauth_gitcode_redirect_uri")
    if not redirect_uri:
        scheme = "https" if request.headers.get("X-Forwarded-Proto") == "https" else "http"
        host = request.headers.get("Host", "localhost:80").split(":")[0]
        redirect_uri = f"{scheme}://{host}:80/api/auth/callback/gitcode"

    state = hashlib.sha256(os.urandom(32)).hexdigest()
    user_db.save_oauth_state(state, "gitcode", {})

    import urllib.parse
    auth_url = (f"https://gitcode.com/oauth/authorize"
                f"?client_id={urllib.parse.quote(client_id)}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
                f"&response_type=code"
                f"&scope=all"
                f"&state={urllib.parse.quote(state)}")
    return {"redirect_url": auth_url}


@router.get("/callback/gitcode")
def gitcode_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(status_code=400, detail={"code": 400, "message": f"GitCode 授权失败: {error}"})

    state_data = user_db.validate_oauth_state(state, "gitcode")
    if state_data is None:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "state 无效或已过期"})

    from src.user.db import get_meta_value
    client_id = get_meta_value("oauth_gitcode_client_id")
    client_secret = get_meta_value("oauth_gitcode_client_secret")

    import urllib.request, urllib.parse, json
    token_data = None
    try:
        token_req = urllib.request.Request(
            "https://gitcode.com/oauth/token",
            data=urllib.parse.urlencode({
                "grant_type": "authorization_code", "code": code,
                "client_id": client_id, "client_secret": client_secret,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )
        token_resp = urllib.request.urlopen(token_req, timeout=10)
        token_data = json.loads(token_resp.read())
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": 502, "message": f"获取 access_token 失败: {e}"})

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    if not access_token:
        raise HTTPException(status_code=502, detail={"code": 502, "message": "获取 access_token 失败"})

    gitcode_user = None
    try:
        import requests as _req
        _user_resp = _req.get(
            "https://gitcode.com/api/v5/user",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        gitcode_user = _user_resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": 502, "message": f"获取用户信息失败: {e}"})

    gitcode_id = str(gitcode_user.get("id", ""))
    gitcode_login = gitcode_user.get("login", "")
    # 昵称：优先 nickname，次选 name（每次 OAuth 登录刷新）
    gitcode_nickname = gitcode_user.get("nickname") or gitcode_user.get("name") or ""
    if not gitcode_id:
        raise HTTPException(status_code=502, detail={"code": 502, "message": "无法获取用户信息"})

    # 按平台 ID 精确匹配，不跨平台 fallback username（同用户名不代表同一人）
    local_user = user_db.get_user_by_gitcode_id(gitcode_id)

    # 白名单校验：所有用户（含已存在）都必须在白名单内，踢出白名单后立即无法登录
    from src.config import is_gitcode_user_allowed
    if not is_gitcode_user_allowed(gitcode_login):
        return _oauth_error_redirect(request, f"WHITELIST:gitcode:{gitcode_login}")

    # 新用户才创建
    if not local_user:
        result = user_db.create_user(gitcode_login, hashlib.sha256(os.urandom(16)).hexdigest(), 0)
        if not result:
            # username 冲突（已有同用户名的 GitHub 账号）→ 加前缀创建独立 GitCode 账号
            result = user_db.create_user(f"gc_{gitcode_login}", hashlib.sha256(os.urandom(16)).hexdigest(), 0)
            if not result:
                raise HTTPException(status_code=500, detail={"code": 500, "message": "创建用户失败"})
        local_user = user_db.get_user(result["id"])

    user_db.link_gitcode_user(local_user["id"], gitcode_id, access_token, refresh_token)

    user_acc = user_db.get_user_account(local_user["username"]) or {}
    user_acc["gitcode_api_token"] = access_token
    if gitcode_nickname:
        user_acc["nickname"] = gitcode_nickname
    user_db.save_user_account(local_user["username"], user_acc)

    # 登录成功即建立 GitCode↔平台 user 映射（unified_user_mapping），
    # 使 /api/tasks/notify 能解析 assignee→user_id（tasks.py:52）；幂等，每次登录刷新。
    # 传空 open_id：update_unified_user_info 会保留已有 feishu_open_id（防覆盖飞书确认的绑定）。
    try:
        from src.session_manager import update_unified_user_info
        update_unified_user_info(gitcode_login, open_id="", gitcode_nickname=gitcode_nickname)
    except Exception as e:
        log.warning(f"OAuth login: write user mapping failed: {e}")

    # JWT 双 token——OAuth 也签发 refresh_token
    from src.api.jwt_utils import create_access_token, create_refresh_token
    token_version = user_db.get_token_version(local_user["id"])
    jwt_access_token = create_access_token(local_user["id"], local_user["username"], token_version,
                                               login_provider="gitcode")
    jwt_refresh_token = create_refresh_token(local_user["id"], local_user["username"], token_version,
                                                 login_provider="gitcode")
    # 仅真正首次登录（无任何托管仓库）才 first_login=1（前端清 currentRepo 进仓库选择页）；
    # 已有托管仓库则保留上次选择的仓库（first_login=0），恢复上次的 repo
    has_repos = bool(user_db.get_managed_repos(local_user["id"]))
    # 从 cookie 读语言偏好（i18n.js setLang 时双写 localStorage + cookie）
    lang = request.cookies.get("grapeLang", "zh")
    prefix = f"/{lang}" if lang != "zh" else ""
    # refresh_token 通过 cookie 传递（HttpOnly）——URL 只带 access_token
    resp = RedirectResponse(url=f"{_app_base()}{prefix}/?token={jwt_access_token}&first_login={'1' if not has_repos else '0'}", status_code=302)
    _set_refresh_cookie(resp, jwt_refresh_token)
    return resp


# ====== GitHub OAuth ======

def _oauth_error_redirect(request: Request, message: str):
    """OAuth 回调失败 → 重定向到前端登录页并带错误信息（前端 toast 提示）。"""
    import urllib.parse
    scheme = request.url.scheme
    host = request.url.hostname
    port = request.url.port
    # 前端 SPA 路由：/login?oauth_error=xxx
    base = f"{scheme}://{host}"
    if port and port not in (80, 443):
        base += f":{port}"
    _encoded = urllib.parse.quote(message)
    return RedirectResponse(url=f"{base}{_app_base()}/login?oauth_error={_encoded}", status_code=302)


@router.get("/login/github")
def github_oauth_login(request: Request):
    """GitHub OAuth 第一步：跳转 GitHub 授权页。"""
    from src.user.db import get_meta_value
    client_id = get_meta_value("oauth_github_client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "GitHub OAuth 未配置"})

    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI") or get_meta_value("oauth_github_redirect_uri")
    if not redirect_uri:
        scheme = "https" if request.headers.get("X-Forwarded-Proto") == "https" else "http"
        host = request.headers.get("Host", "localhost:80").split(":")[0]
        redirect_uri = f"{scheme}://{host}:80/api/auth/callback/github"

    state = hashlib.sha256(os.urandom(32)).hexdigest()
    user_db.save_oauth_state(state, "github", {})

    import urllib.parse
    auth_url = (f"https://github.com/login/oauth/authorize"
                f"?client_id={urllib.parse.quote(client_id)}"
                f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
                f"&response_type=code"
                f"&scope=repo%20user%20admin:repo_hook%20offline_access"
                f"&state={urllib.parse.quote(state)}")
    return {"redirect_url": auth_url}


@router.get("/callback/github")
def github_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """GitHub OAuth 回调：用 code 换 access_token → 获取用户信息 → 创建/绑定本地用户 → 签发 JWT。"""
    if error:
        raise HTTPException(status_code=400, detail={"code": 400, "message": f"GitHub 授权失败: {error}"})

    state_data = user_db.validate_oauth_state(state, "github")
    if state_data is None:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "state 无效或已过期"})

    from src.user.db import get_meta_value
    client_id = get_meta_value("oauth_github_client_id")
    client_secret = get_meta_value("oauth_github_client_secret")

    import urllib.parse, json
    _proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""

    def _github_post(url, data, headers, timeout=15):
        """向 github.com 发 POST 请求。有代理走代理，否则直连。"""
        import requests as _req
        if _proxy:
            return _req.post(url, data=data, headers=headers, timeout=timeout, proxies={"https": _proxy, "http": _proxy})
        return _req.post(url, data=data, headers=headers, timeout=timeout)

    token_data = None
    try:
        _token_body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "code": code,
        }).encode()
        _token_headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        _resp = _github_post("https://github.com/login/oauth/access_token", data=_token_body, headers=_token_headers)
        token_data = _resp.json()
    except Exception as e:
        _msg = str(e)
        if "timed out" in _msg or "ConnectTimeout" in _msg or "Max retries" in _msg:
            _user_msg = "GitHub 登录失败：服务器无法连接 github.com（网络超时）。请稍后重试，或联系管理员配置代理。"
        else:
            _user_msg = f"GitHub 登录失败：{_msg[:120]}"
        return _oauth_error_redirect(request, _user_msg)

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    if not access_token:
        return _oauth_error_redirect(request, "GitHub 登录失败：未获取到 access_token，请重试。")

    github_user = None
    try:
        import requests as _req
        _user_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
        _proxies = {"https": _proxy, "http": _proxy} if _proxy else None
        _user_resp = _req.get("https://api.github.com/user", headers=_user_headers, timeout=10, proxies=_proxies)
        _user_resp.raise_for_status()
        github_user = _user_resp.json()
    except Exception as e:
        return _oauth_error_redirect(request, f"GitHub 登录失败：获取用户信息失败（{str(e)[:80]}）")

    github_id = str(github_user.get("id", ""))
    github_login = github_user.get("login", "")
    github_name = github_user.get("name") or github_login
    if not github_id:
        return _oauth_error_redirect(request, "GitHub 登录失败：无法获取用户信息。")

    # 按平台 ID 精确匹配，不跨平台 fallback username（同用户名不代表同一人）
    local_user = user_db.get_user_by_github_id(github_id)

    # 白名单校验
    from src.config import is_github_user_allowed
    if not is_github_user_allowed(github_login):
        return _oauth_error_redirect(request, f"WHITELIST:github:{github_login}")

    # 新用户才创建
    if not local_user:
        result = user_db.create_user(github_login, hashlib.sha256(os.urandom(16)).hexdigest(), 0)
        if not result:
            # username 冲突（已有同用户名的 GitCode 账号）→ 加前缀创建独立 GitHub 账号
            result = user_db.create_user(f"gh_{github_login}", hashlib.sha256(os.urandom(16)).hexdigest(), 0)
            if not result:
                raise HTTPException(status_code=500, detail={"code": 500, "message": "创建用户失败"})
        local_user = user_db.get_user(result["id"])

    user_db.link_github_user(local_user["id"], github_id, access_token, refresh_token)

    user_acc = user_db.get_user_account(local_user["username"]) or {}
    user_acc["github_api_token"] = access_token
    if github_name:
        user_acc["nickname"] = github_name
    user_db.save_user_account(local_user["username"], user_acc)
    # WAL 模式下确保写入持久化到主 DB 文件（防止进程退出后丢失）
    try:
        from src.session_manager import db as _db, db_lock as _db_lock
        with _db_lock:
            _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    log.info(f"GitHub OAuth: user={local_user['username']} github_id={github_id} token={access_token[:10]}...{access_token[-5:]} saved+checkpointed")

    # 签发 JWT
    from src.api.jwt_utils import create_access_token, create_refresh_token
    token_version = user_db.get_token_version(local_user["id"])
    jwt_access_token = create_access_token(local_user["id"], local_user["username"], token_version,
                                               login_provider="github")
    jwt_refresh_token = create_refresh_token(local_user["id"], local_user["username"], token_version,
                                                 login_provider="github")

    # OAuth App 不需要 installation 检查（scope 在授权 URL 里已指定）
    _gh_perm_warning = ""

    try:
        from src.session_manager import update_unified_user_info
        update_unified_user_info(github_login)
    except Exception as e:
        log.warning(f"GitHub OAuth login: write user mapping failed: {e}")

    has_repos = bool(user_db.get_managed_repos(local_user["id"]))
    lang = request.cookies.get("grapeLang", "zh")
    prefix = f"/{lang}" if lang != "zh" else ""
    _warn_param = f"&gh_perm_warning={urllib.parse.quote(_gh_perm_warning)}" if _gh_perm_warning else ""
    resp = RedirectResponse(url=f"{_app_base()}{prefix}/?token={jwt_access_token}&first_login={'1' if not has_repos else '0'}{_warn_param}", status_code=302)
    _set_refresh_cookie(resp, jwt_refresh_token)
    return resp


@router.get("/profile", operation_id="auth_profile", summary="Get current user profile", description="Returns the current user profile. Supports JWT session, user API Key (grape_) or session token (tk_).")
def get_profile(user: dict = Depends(require_no_session_token)):
    """用户信息——支持 JWT 会话 / grape_ API Key / tk_ 会话 token（Issue #69）。"""
    acc = user_db.get_user_account(user["username"]) or {}
    managed_repos_raw = user_db.get_managed_repos(user["id"])
    managed = [{"key": r["full_name"], "full_name": r["full_name"], "is_admin": r["is_admin"]}
               for r in managed_repos_raw[:10]]
    return {
        "username": user["username"],
        "nickname": acc.get("nickname", "") or user["username"],
        "status": "online",
        "managed_repos": managed,
        "gitcode_bound": bool(user_db.get_credential(user["id"], "gitcode_oauth")),
        "gitlab_bound": bool(user_db.get_credential(user["id"], "gitlab_login")),
        "login_provider": user.get("login_provider", ""),
        "login_instance": user.get("login_instance", ""),
        "github_bound": bool(user_db.get_credential(user["id"], "github_oauth")),
    }


@router.post("/logout", operation_id="auth_logout", summary="Logout (revoke all tokens)", description="Logs out: increments token_version to revoke all tokens and clears the refresh cookie.")
def logout(user: dict = Depends(require_no_session_token), response: Response = None):
    """登出——token_version+1 撤销所有 token + 清 cookie。"""
    user_db.increment_token_version(user["id"])
    if response:
        _clear_refresh_cookie(response)
    return {"message": "已登出"}


@router.get('/gitlab/instances')
def gitlab_login_instances():
    """Public login choices contain no OAuth credentials."""
    from src.user.gitlab_config import login_instances
    mode, instances = login_instances()
    return {'mode': mode, 'instances': [dict(
        id=item.get('id', ''),
        name=item.get('name', ''),
        base_url=item.get('base_url', ''),
        allow_token_login=bool(item.get('allow_token_login', False)),
        oauth_ready=bool(item.get('client_id') and item.get('client_secret') and item.get('redirect_uri'))
    ) for item in instances]}


class GitlabTokenLogin(BaseModel):
    instance: str = ''
    token: str


@router.post('/gitlab/token-login')
def gitlab_token_login(body: GitlabTokenLogin, response: Response):
    """GitLab PAT 登录：实例禁用「添加新应用」时，用个人访问令牌代替 OAuth 应用。

    流程与 OAuth 回调同构：PRIVATE-TOKEN 调 /api/v4/user 验证身份 → 白名单 →
    复用 gitlab_identities 映射（同一 GitLab 账号与 OAuth 登录落同一 Grape 账号）。
    Token 仅用于本次验证，不落库（login-only，与 OAuth 口径一致）。
    """
    import re
    import requests
    from src.user.gitlab_config import get_login_instance
    from src.user.gitlab_identity import resolve_user
    supplied = body.token.strip()
    if not re.fullmatch(r'[A-Za-z0-9_\-]{20,}', supplied):
        raise HTTPException(400, detail={'message': 'GitLab Token 格式不正确'})
    try:
        config = get_login_instance(body.instance)
    except ValueError as exc:
        raise HTTPException(400, detail={'message': str(exc)}) from exc
    if not config.get('allow_token_login'):
        raise HTTPException(403, detail={'message': '该 GitLab 实例未开启 Token 登录'})
    try:
        user_response = requests.get(config['base_url'] + '/api/v4/user',
                                     headers={'PRIVATE-TOKEN': supplied},
                                     timeout=15, allow_redirects=False)
        if user_response.status_code in (401, 403):
            # 400 而非 401：前端 API 层对 401 触发 JWT refresh/跳登录，会掩盖真实错误
            raise HTTPException(400, detail={'message': 'GitLab Token 无效或已过期'})
        user_response.raise_for_status()
        profile = user_response.json()
        if not profile.get('id') or not profile.get('username'):
            raise ValueError('Missing user identity')
    except HTTPException:
        raise
    except Exception:
        # Upstream errors may contain codes/tokens; never put them in logs or URLs.
        log.warning('GitLab token login upstream failed for instance=%s', config['id'])
        raise HTTPException(502, detail={'message': 'GitLab 实例连接失败，请检查实例地址与网络后重试'})
    allowed = config.get('allowed_usernames', [])
    if allowed and profile['username'] not in allowed:
        raise HTTPException(403, detail={'message': '此 GitLab 账号不在该实例的登录白名单内'})
    user = resolve_user(config['base_url'], profile)
    # Login-only scope: do not retain the token; same identity payload as OAuth.
    if not user_db.set_credential(user['id'], 'gitlab_login', {
        'base_url': config['base_url'], 'gitlab_user_id': str(profile['id']),
        'username': profile['username'], 'instance': config['id'],
    }):
        raise HTTPException(500, detail={'message': '保存 GitLab 登录身份失败'})
    account = user_db.get_user_account(user['username']) or {}
    account['nickname'] = profile.get('name') or profile['username']
    user_db.save_user_account(user['username'], account)
    from src.api.jwt_utils import create_access_token, create_refresh_token
    version = user_db.get_token_version(user['id'])
    jwt_access = create_access_token(user['id'], user['username'], version,
                                     login_provider='gitlab', login_instance=config['id'])
    jwt_refresh = create_refresh_token(user['id'], user['username'], version,
                                       login_provider='gitlab', login_instance=config['id'])
    _set_refresh_cookie(response, jwt_refresh)
    return {'token': jwt_access, 'user': {'id': user['id'], 'username': user['username']}}


@router.get('/login/gitlab')
def gitlab_oauth_login(request: Request, response: Response, instance: str = ''):
    import base64
    import secrets
    from urllib.parse import urlencode
    from src.user.gitlab_config import get_login_instance
    try:
        config = get_login_instance(instance)
    except ValueError as exc:
        raise HTTPException(400, detail={'message': str(exc)}) from exc
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    user_db.save_oauth_state(state, 'gitlab', {
        'instance': config['id'], 'base_url': config['base_url'],
        'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'], 'verifier': verifier,
    })
    # Per-state cookie supports concurrent login tabs and binds callback to the
    # browser that initiated it. Lax is required for the cross-site GET callback.
    is_secure = COOKIE_SECURE and (request.url.scheme == 'https')
    response.set_cookie('gitlab_oauth_' + state, state, httponly=True, secure=is_secure,
                        samesite='lax', max_age=600, path=_app_base() + _COOKIE_PATH)
    query = urlencode(dict(client_id=config['client_id'], redirect_uri=config['redirect_uri'],
                           response_type='code', scope='read_user', state=state,
                           code_challenge=challenge, code_challenge_method='S256'))
    return {'redirect_url': config['base_url'] + '/oauth/authorize?' + query}


@router.get('/callback/gitlab')
def gitlab_oauth_callback(request: Request, code: str = '', state: str = '', error: str = ''):
    import hmac
    import re
    import requests
    from urllib.parse import urlencode
    from src.user.gitlab_config import get_login_instance
    from src.user.gitlab_identity import resolve_user
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', state) or not hmac.compare_digest(
            request.cookies.get('gitlab_oauth_' + state, ''), state):
        raise HTTPException(400, detail={'message': 'GitLab 登录 state 无效或浏览器不匹配'})
    saved = user_db.validate_oauth_state(state, 'gitlab')
    if not saved:
        raise HTTPException(400, detail={'message': 'GitLab 登录已过期或已使用'})
    try:
        config = get_login_instance(saved['instance'])
        if any(config[k] != saved[k] for k in ('base_url', 'client_id', 'redirect_uri')):
            raise ValueError('GitLab 登录配置已变更，请重新登录')
    except ValueError as exc:
        raise HTTPException(400, detail={'message': str(exc)}) from exc
    if error or not code:
        return _oauth_error_redirect(request, 'GitLab 授权已取消或未返回授权码')
    try:
        token_response = requests.post(config['base_url'] + '/oauth/token', data={
            'grant_type': 'authorization_code', 'client_id': config['client_id'],
            'client_secret': config['client_secret'], 'redirect_uri': saved['redirect_uri'],
            'code': code, 'code_verifier': saved['verifier'],
        }, timeout=15, allow_redirects=False)
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get('access_token', '')
        if not access_token:
            raise ValueError('Missing access token')
        user_response = requests.get(config['base_url'] + '/api/v4/user',
                                     headers={'Authorization': 'Bearer ' + access_token},
                                     timeout=15, allow_redirects=False)
        user_response.raise_for_status()
        profile = user_response.json()
        if not profile.get('id') or not profile.get('username'):
            raise ValueError('Missing user identity')
    except Exception:
        # Upstream errors may contain codes/tokens; never put them in logs or URLs.
        log.warning('GitLab OAuth upstream exchange failed for instance=%s', config['id'])
        return _oauth_error_redirect(request, 'GitLab 登录失败，请检查应用配置、证书与网络后重试')
    allowed = config.get('allowed_usernames', [])
    if allowed and profile['username'] not in allowed:
        return _oauth_error_redirect(request, '此 GitLab 账号不在该实例的登录白名单内')
    user = resolve_user(config['base_url'], profile)
    # Login-only scope: do not retain upstream tokens or inject them into agents.
    if not user_db.set_credential(user['id'], 'gitlab_login', {
        'base_url': config['base_url'], 'gitlab_user_id': str(profile['id']),
        'username': profile['username'], 'instance': config['id'],
    }):
        raise HTTPException(500, detail={'message': '保存 GitLab 登录身份失败'})
    account = user_db.get_user_account(user['username']) or {}
    account['nickname'] = profile.get('name') or profile['username']
    user_db.save_user_account(user['username'], account)
    from src.api.jwt_utils import create_access_token, create_refresh_token
    version = user_db.get_token_version(user['id'])
    jwt_access = create_access_token(user['id'], user['username'], version,
                                     login_provider='gitlab', login_instance=config['id'])
    jwt_refresh = create_refresh_token(user['id'], user['username'], version,
                                       login_provider='gitlab', login_instance=config['id'])
    resp = RedirectResponse(_app_base() + '/?' + urlencode({'token': jwt_access}), status_code=302)
    _set_refresh_cookie(resp, jwt_refresh)
    resp.delete_cookie('gitlab_oauth_' + state, path=_app_base() + _COOKIE_PATH)
    return resp


# ====== Gitea OAuth ======

@router.get('/gitea/instances')
def gitea_instances():
    """返回启用的 Gitea 实例列表（不含 secret）。"""
    from src.user.gitea_config import login_instances
    mode, instances = login_instances()
    return {
        'mode': mode,
        'instances': [dict(
            id=x.get('id', ''),
            name=x.get('name', ''),
            base_url=x.get('base_url', ''),
            allow_token_login=bool(x.get('allow_token_login', False)),
            oauth_ready=bool(x.get('client_id') and x.get('client_secret') and x.get('redirect_uri'))
        ) for x in instances]
    }


class GiteaTokenLogin(BaseModel):
    instance: str = ""
    token: str = ""


@router.post('/gitea/token-login')
def gitea_token_login(body: GiteaTokenLogin, response: Response):
    """Gitea Token 登录：当未配置 OAuth 应用时，使用 Personal Access Token 直接验证登录。

    验证步骤：GET /api/v1/user (Authorization: token <token>) → 检查白名单 → 映射或创建用户。
    """
    import re
    import requests
    from src.user.gitea_config import get_login_instance
    from src.user.gitea_identity import resolve_user
    supplied = body.token.strip()
    if not supplied:
        raise HTTPException(400, detail={'message': 'Token 不能为空'})
    try:
        config = get_login_instance(body.instance)
    except ValueError as exc:
        raise HTTPException(400, detail={'message': str(exc)}) from exc
    if not config.get('allow_token_login'):
        raise HTTPException(403, detail={'message': '该 Gitea 实例未开启 Token 登录'})
    try:
        user_response = requests.get(
            config['base_url'].rstrip('/') + '/api/v1/user',
            headers={'Authorization': f'token {supplied}', 'Accept': 'application/json'},
            timeout=15,
            allow_redirects=False
        )
        if user_response.status_code in (401, 403):
            raise HTTPException(400, detail={'message': 'Gitea Token 无效或已过期'})
        user_response.raise_for_status()
        profile = user_response.json()
        if not profile.get('id') or not profile.get('login'):
            raise ValueError('Missing user identity')
    except HTTPException:
        raise
    except Exception:
        log.warning('Gitea token login upstream failed for instance=%s', config['id'])
        raise HTTPException(502, detail={'message': 'Gitea 实例连接失败，请检查实例地址与网络后重试'})

    allowed = config.get('allowed_usernames', [])
    if allowed and profile['login'] not in allowed:
        raise HTTPException(403, detail={'message': '此 Gitea 账号不在该实例的登录白名单内'})

    user = resolve_user(config['base_url'], profile)
    if not user_db.set_credential(user['id'], 'gitea_login', {
        'base_url': config['base_url'], 'gitea_user_id': str(profile['id']),
        'username': profile['login'], 'instance': config['id'],
    }):
        raise HTTPException(500, detail={'message': '保存 Gitea 登录身份失败'})
    account = user_db.get_user_account(user['username']) or {}
    account['nickname'] = profile.get('full_name') or profile.get('name') or profile['login']
    user_db.save_user_account(user['username'], account)
    from src.api.jwt_utils import create_access_token, create_refresh_token
    version = user_db.get_token_version(user['id'])
    jwt_access = create_access_token(user['id'], user['username'], version,
                                     login_provider='gitea', login_instance=config['id'])
    jwt_refresh = create_refresh_token(user['id'], user['username'], version,
                                       login_provider='gitea', login_instance=config['id'])
    _set_refresh_cookie(response, jwt_refresh)
    return {
        'token': jwt_access,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'nickname': account.get('nickname') or user['username'],
            'avatar_url': user.get('avatar_url') or '',
            'login_provider': 'gitea',
            'login_instance': config['id']
        }
    }


@router.get('/login/gitea')
def gitea_oauth_login(request: Request, response: Response, instance: str = ''):
    """发起 Gitea OAuth 登录，生成防重放 state 与 PKCE 码并在 Cookie 中存储校验状态。"""
    import base64
    import hashlib
    import secrets
    from urllib.parse import urlencode
    from src.user.gitea_config import get_login_instance
    try:
        config = get_login_instance(instance)
    except ValueError as exc:
        raise HTTPException(400, detail={'message': str(exc)}) from exc
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    user_db.save_oauth_state(state, 'gitea', {
        'instance': config['id'], 'base_url': config['base_url'],
        'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'], 'verifier': verifier,
    })
    is_secure = COOKIE_SECURE and (request.url.scheme == 'https')
    response.set_cookie('gitea_oauth_' + state, state, httponly=True, secure=is_secure,
                        samesite='lax', max_age=600, path=_app_base() + _COOKIE_PATH)
    query = urlencode(dict(client_id=config['client_id'], redirect_uri=config['redirect_uri'],
                           response_type='code', state=state,
                           code_challenge=challenge, code_challenge_method='S256'))
    return {'redirect_url': config['base_url'] + '/login/oauth/authorize?' + query}


@router.get('/callback/gitea')
def gitea_oauth_callback(request: Request, code: str = '', state: str = '', error: str = ''):
    """Gitea OAuth 回调处理：验证 state，换取 token，拉取个人信息，绑定本地身份并签发 JWT。"""
    import hmac
    import re
    import requests
    from urllib.parse import urlencode
    from src.user.gitea_config import get_login_instance
    from src.user.gitea_identity import resolve_user
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', state) or not hmac.compare_digest(
            request.cookies.get('gitea_oauth_' + state, ''), state):
        raise HTTPException(400, detail={'message': 'Gitea 登录 state 无效或浏览器不匹配'})
    saved = user_db.validate_oauth_state(state, 'gitea')
    if not saved:
        raise HTTPException(400, detail={'message': 'Gitea 登录已过期或已使用'})
    try:
        config = get_login_instance(saved['instance'])
        if any(config[k] != saved[k] for k in ('base_url', 'client_id', 'redirect_uri')):
            raise ValueError('Gitea 登录配置已变更，请重新登录')
    except ValueError as exc:
        raise HTTPException(400, detail={'message': str(exc)}) from exc
    if error or not code:
        return _oauth_error_redirect(request, 'Gitea 授权已取消或未返回授权码')
    try:
        token_response = requests.post(config['base_url'] + '/login/oauth/access_token', data={
            'grant_type': 'authorization_code', 'client_id': config['client_id'],
            'client_secret': config['client_secret'], 'redirect_uri': saved['redirect_uri'],
            'code': code, 'code_verifier': saved['verifier'],
        }, headers={'Accept': 'application/json'}, timeout=15, allow_redirects=False)
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get('access_token', '')
        if not access_token:
            raise ValueError('Missing access token')
        user_response = requests.get(config['base_url'] + '/api/v1/user',
                                     headers={'Authorization': 'token ' + access_token,
                                              'Accept': 'application/json'},
                                     timeout=15, allow_redirects=False)
        user_response.raise_for_status()
        profile = user_response.json()
        if not profile.get('id') or not (profile.get('username') or profile.get('login')):
            raise ValueError('Missing user identity')
        # Gitea 的 username 字段在 /api/v1/user 中通常为 username 或 login
        uname = profile.get('username') or profile.get('login')
        profile['username'] = uname
    except Exception:
        log.warning('Gitea OAuth upstream exchange failed for instance=%s', config['id'])
        return _oauth_error_redirect(request, 'Gitea 登录失败，请检查应用配置、证书与网络后重试')
    allowed = config.get('allowed_usernames', [])
    if allowed and profile['username'] not in allowed:
        return _oauth_error_redirect(request, '此 Gitea 账号不在该实例的登录白名单内')
    user = resolve_user(config['base_url'], profile)
    if not user_db.set_credential(user['id'], 'gitea_login', {
        'base_url': config['base_url'], 'gitea_user_id': str(profile['id']),
        'username': profile['username'], 'instance': config['id'],
    }):
        raise HTTPException(500, detail={'message': '保存 Gitea 登录身份失败'})
    account = user_db.get_user_account(user['username']) or {}
    account['nickname'] = profile.get('full_name') or profile['username']
    user_db.save_user_account(user['username'], account)
    from src.api.jwt_utils import create_access_token, create_refresh_token
    version = user_db.get_token_version(user['id'])
    jwt_access = create_access_token(user['id'], user['username'], version,
                                     login_provider='gitea', login_instance=config['id'])
    jwt_refresh = create_refresh_token(user['id'], user['username'], version,
                                       login_provider='gitea', login_instance=config['id'])
    resp = RedirectResponse(_app_base() + '/?' + urlencode({'token': jwt_access}), status_code=302)
    _set_refresh_cookie(resp, jwt_refresh)
    resp.delete_cookie('gitea_oauth_' + state, path=_app_base() + _COOKIE_PATH)
    return resp
