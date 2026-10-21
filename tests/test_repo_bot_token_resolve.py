"""仓库 bot token 解析（多平台）回归测试。

背景（2026-10-21）：dependency_check 对 vod/issue/coding/qa/sre 硬性要求
agent_tasks.repo_gitcode_token 非空（src/runtime/dependency_check.py），该值来自
派发时的 get_gitcode_token(repo_key)。旧实现有两处缺陷，导致"配了还是空"：

1. get_bot_token_by_repo 用 to_repo_key() 拼 key，而该函数默认 provider='gitcode'
   → 写入时 repo_key 带真实前缀（gitea:/gitlab:）的仓库永远查不到仓库级 token；
2. provider 分流只有 github: / 其它 两条分支，gitea/gitlab 完全没有兜底。

本文件锁定修复后的行为：跨平台候选命中 + 实例级兜底 + token 不会被静默清空。
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("DEVOPS_DB_PATH", os.path.join(ROOT, "data", "test_sessions.db"))


def _clean(repo_keys):
    from src.session_manager import db, db_lock
    with db_lock:
        c = db.cursor()
        for rk in repo_keys:
            c.execute("DELETE FROM managed_repos WHERE repo_key=?", (rk,))
            c.execute("DELETE FROM devops_agent_session WHERE repo_key=?", (rk,))
        db.commit()


def _seed_repo(repo_key, bot_token=""):
    from src.session_manager import db, db_lock
    owner_repo = repo_key.split(":", 1)[-1]
    owner, _, name = owner_repo.partition("/")
    with db_lock:
        c = db.cursor()
        c.execute(
            "INSERT OR REPLACE INTO managed_repos "
            "(repo_key, repo_owner, repo_name, full_name, bot_gitcode_token) VALUES (?,?,?,?,?)",
            (repo_key, owner, name, owner_repo, bot_token),
        )
        db.commit()


def _seed_session(repo_key, agent_type, token):
    from src.session_manager import db, db_lock
    with db_lock:
        c = db.cursor()
        c.execute(
            "INSERT OR REPLACE INTO devops_agent_session "
            "(id, repo_key, agent_type, repo_gitcode_token, status) VALUES (?,?,?,?, 'idle')",
            (f"sess-{agent_type}-{repo_key}", repo_key, agent_type, token),
        )
        db.commit()


# --------------------------- get_bot_token_by_repo ---------------------------

def test_bot_token_by_repo_hits_gitea_prefix():
    """gitea: 前缀的仓库级 token 必须能查到（旧实现拼 gitcode: 前缀查不到）。"""
    from src.user.db import get_bot_token_by_repo
    _seed_repo("gitea:org/tokgitea", "T_GITEA_REPO")
    try:
        assert get_bot_token_by_repo("org", "tokgitea") == "T_GITEA_REPO"
        assert get_bot_token_by_repo("org", "tokgitea", provider="gitea") == "T_GITEA_REPO"
    finally:
        _clean(["gitea:org/tokgitea"])


def test_bot_token_by_repo_hits_gitlab_prefix():
    from src.user.db import get_bot_token_by_repo
    _seed_repo("gitlab:org/tokgitlab", "T_GITLAB_REPO")
    try:
        assert get_bot_token_by_repo("org", "tokgitlab") == "T_GITLAB_REPO"
    finally:
        _clean(["gitlab:org/tokgitlab"])


def test_bot_token_by_repo_empty_when_absent():
    from src.user.db import get_bot_token_by_repo
    assert get_bot_token_by_repo("org", "no-such-repo-xyz") == ""


# ---------------------------- get_repo_bot_token ----------------------------

def test_repo_bot_token_prefers_agent_session():
    """Agent 配置（devops_agent_session）优先于仓库级配置。"""
    from src.user.db import get_repo_bot_token
    _seed_repo("gitea:org/tokprio", "T_REPO")
    _seed_session("gitea:org/tokprio", "vod", "T_SESSION")
    try:
        assert get_repo_bot_token("gitea:org/tokprio") == "T_SESSION"
    finally:
        _clean(["gitea:org/tokprio"])


def test_repo_bot_token_matches_without_prefix():
    """调用方传无前缀 owner/repo 也要命中同一行（跨平台候选）。"""
    from src.user.db import get_repo_bot_token
    _seed_repo("gitea:org/toknoprefix", "T_REPO")
    try:
        assert get_repo_bot_token("org/toknoprefix") == "T_REPO"
    finally:
        _clean(["gitea:org/toknoprefix"])


def test_repo_bot_token_falls_back_to_gitea_instance(monkeypatch):
    """无仓库配置时，回落 gitea 实例级系统 token（旧实现恒空）。"""
    import src.user.db as udb
    monkeypatch.setattr(udb, "_load_global_bot_config", lambda: {
        "gitea_default_instance": "private_gitea",
        "gitea_instances": [
            {"id": "other", "token": "T_OTHER", "enabled": True},
            {"id": "private_gitea", "token": "T_GITEA_INSTANCE", "enabled": True},
        ],
    })
    assert udb.get_repo_bot_token("gitea:org/notoken") == "T_GITEA_INSTANCE"


def test_repo_bot_token_falls_back_to_gitlab_instance(monkeypatch):
    import src.user.db as udb
    monkeypatch.setattr(udb, "_load_global_bot_config", lambda: {
        "gitlab_instances": [{"id": "cndatacom", "token": "T_GITLAB_INSTANCE", "enabled": True}],
    })
    assert udb.get_repo_bot_token("gitlab:org/notoken") == "T_GITLAB_INSTANCE"


def test_repo_bot_token_gitcode_global(monkeypatch):
    import src.user.db as udb
    monkeypatch.setattr(udb, "_load_global_bot_config", lambda: {"gitcode_api_token": "T_GLOBAL"})
    assert udb.get_repo_bot_token("gitcode:org/notoken") == "T_GLOBAL"


def test_repo_bot_token_github_global(monkeypatch):
    import src.user.db as udb
    monkeypatch.setattr(udb, "_load_global_bot_config", lambda: {"github_bot_token": "T_GH"})
    assert udb.get_repo_bot_token("github:org/notoken") == "T_GH"


def test_get_gitcode_token_delegates_to_repo_resolver(monkeypatch):
    """get_gitcode_token(repo_key=...) 必须与统一入口同源（不再只看 gitcode）。"""
    import src.user.db as udb
    monkeypatch.setattr(udb, "_load_global_bot_config", lambda: {})
    _seed_repo("gitea:org/tokdelegate", "T_REPO")
    try:
        assert udb.get_gitcode_token(repo_key="gitea:org/tokdelegate") == "T_REPO"
    finally:
        _clean(["gitea:org/tokdelegate"])


# --------------------------- upsert 保留语义 ---------------------------

def test_upsert_preserves_token_when_empty():
    """不传 token（None/空串）时不得清空既有配置。"""
    from src.api.session_store import upsert_agent_session
    _seed_session("gitea:org/toku", "vod", "T_KEEP")
    try:
        upsert_agent_session(repo_key="gitea:org/toku", agent_type="vod", skill="vod")
        upsert_agent_session(repo_key="gitea:org/toku", agent_type="vod",
                             skill="vod", repo_gitcode_token="")
        from src.user.db import get_repo_bot_token
        assert get_repo_bot_token("gitea:org/toku") == "T_KEEP"
    finally:
        _clean(["gitea:org/toku"])


def test_upsert_overwrites_token_when_given():
    from src.api.session_store import upsert_agent_session
    _seed_session("gitea:org/toku2", "vod", "T_OLD")
    try:
        upsert_agent_session(repo_key="gitea:org/toku2", agent_type="vod",
                             skill="vod", repo_gitcode_token="T_NEW")
        from src.user.db import get_repo_bot_token
        assert get_repo_bot_token("gitea:org/toku2") == "T_NEW"
    finally:
        _clean(["gitea:org/toku2"])


def test_gitea_env_token_fallback(monkeypatch):
    """当无仓库配置和会话配置时，支持从环境变量 GITEA_BOT_TOKEN 兜底。"""
    import src.user.db as udb
    monkeypatch.setenv("GITEA_BOT_TOKEN", "T_GITEA_ENV")
    monkeypatch.setattr(udb, "_load_global_bot_config", lambda: {})
    assert udb.get_repo_bot_token("gitea:org/envtest") == "T_GITEA_ENV"


def test_meta_table_instance_token_persistence(monkeypatch):
    """当文件中 token 为空时，能从 meta 表读取之前同步的实例 token。"""
    import json
    from src.user.db import set_meta_value, get_repo_bot_token
    from src.admin.config_store import load_config
    insts = [{
        "id": "private_gitea",
        "base_url": "http://172.16.145.202:30344",
        "token": "T_META_GITEA",
        "enabled": True,
    }]
    set_meta_value("config_gitea_instances", json.dumps(insts))
    monkeypatch.delenv("GITEA_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GITEA_TOKEN", raising=False)
    # mock 文件里没有 token
    monkeypatch.setattr("src.admin.config_store._GBT_PATH", "/tmp/non_existent_gbt.json")
    monkeypatch.setattr("src.admin.config_store._SEED_PATH", "/tmp/non_existent_seed.json")
    loaded = load_config()
    assert loaded.get("gitea_instances", [{}])[0].get("token") == "T_META_GITEA"
    assert get_repo_bot_token("gitea:org/metatest") == "T_META_GITEA"


def test_repo_bot_token_falls_back_to_gitea_user_credential():
    """当无 agent session、无仓库 token、无全局配置时，自动从仓库 owner 的 Gitea 登录凭证兜底并自愈。"""
    from src.session_manager import db, db_lock
    import src.user.db as udb

    repo_key = "gitea:dev/snake_test_cred"
    _clean([repo_key])
    _seed_repo(repo_key, bot_token="")

    with db_lock:
        c = db.cursor()
        c.execute("DELETE FROM user_accounts WHERE username='dev_test_user'")
        c.execute("INSERT INTO user_accounts (username, password_hash) VALUES ('dev_test_user', 'hash')")
        uid = c.lastrowid
        db.commit()

    try:
        # 给该用户写入 Gitea 登录凭证
        udb.set_credential(uid, "gitea_login", {"token": "T_GITEA_USER_PAT", "instance": "private_gitea"})
        # 将仓库绑定至 user_repos，设为管理员
        udb.mark_user_repo_join(uid, repo_key, is_join=True, is_admin=True)

        tok, src = udb.resolve_repo_bot_token(repo_key)
        assert tok == "T_GITEA_USER_PAT"
        assert src == "user_credential"

        # 检查自愈：managed_repos 表里已被自动补齐
        with db_lock:
            c = db.cursor()
            c.execute("SELECT bot_gitcode_token FROM managed_repos WHERE repo_key=?", (repo_key,))
            row = c.fetchone()
            assert row and row[0] == "T_GITEA_USER_PAT"
    finally:
        _clean([repo_key])
        with db_lock:
            c = db.cursor()
            c.execute("DELETE FROM user_credentials WHERE user_id=?", (uid,))
            c.execute("DELETE FROM user_repos WHERE repo_key=?", (repo_key,))
            c.execute("DELETE FROM user_accounts WHERE id=?", (uid,))
            db.commit()


