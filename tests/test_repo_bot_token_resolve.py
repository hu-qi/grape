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
