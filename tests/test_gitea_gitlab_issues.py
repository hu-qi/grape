"""Gitea/GitLab Issue 创建分流单测（Stage 9：私有化 Gitea 需求中心支持）。

覆盖：
- _find_open_issue_by_title 按 provider 分流调用对应平台 API
- _post_gitea_issue / _post_gitlab_issue 请求地址与参数
- _gitea_issue_base_url / _gitlab_issue_base_url 实例地址解析与兜底
- _resolve_issue_user_token 用户 token / Admin 实例 token 兜底
"""

import pytest
from unittest.mock import patch


# ================= _find_open_issue_by_title: gitea =================

def test_find_open_issue_by_title_gitea_hit():
    from src.api.issues_center import _find_open_issue_by_title
    with patch("src.user.repo_manager._gitea_req") as mock_req:
        mock_req.return_value = [
            {"number": 3, "title": "修复登录", "state": "open"},
            {"number": 4, "title": "其他任务", "state": "open"},
        ]
        n = _find_open_issue_by_title("tok", "dev", "demo", "修复登录",
                                      provider="gitea", base_url="http://gitea.internal")
    assert n == 3
    url = mock_req.call_args[0][1]
    assert url.startswith("http://gitea.internal/api/v1/repos/dev/demo/issues")


def test_find_open_issue_by_title_gitea_miss():
    from src.api.issues_center import _find_open_issue_by_title
    with patch("src.user.repo_manager._gitea_req") as mock_req:
        mock_req.return_value = [{"number": 3, "title": "别的", "state": "open"}]
        n = _find_open_issue_by_title("tok", "dev", "demo", "不存在",
                                      provider="gitea", base_url="http://x")
    assert n is None


# ================= _find_open_issue_by_title: gitlab =================

def test_find_open_issue_by_title_gitlab_hit():
    from src.api.issues_center import _find_open_issue_by_title
    with patch("src.user.repo_manager._gitlab_req") as mock_req:
        mock_req.return_value = [
            {"iid": 7, "title": "部署失败", "state": "opened"},
        ]
        n = _find_open_issue_by_title("tok", "grp", "svc", "部署失败",
                                      provider="gitlab", base_url="https://gitlab.internal")
    assert n == 7
    url = mock_req.call_args[0][1]
    # 项目路径需 URL 编码
    assert url.startswith("https://gitlab.internal/api/v4/projects/grp%2Fsvc/issues")


def test_find_open_issue_by_title_gitlab_miss():
    from src.api.issues_center import _find_open_issue_by_title
    with patch("src.user.repo_manager._gitlab_req") as mock_req:
        mock_req.return_value = [{"iid": 7, "title": "别的", "state": "closed"}]
        n = _find_open_issue_by_title("tok", "grp", "svc", "部署失败",
                                      provider="gitlab", base_url="https://x")
    assert n is None


# ================= _post_gitea_issue / _post_gitlab_issue =================

def test_post_gitea_issue_uses_base_url_and_title_body():
    from src.api.issues_center import _post_gitea_issue
    with patch("src.user.repo_manager._gitea_req") as mock_req:
        mock_req.return_value = {"number": 5}
        resp = _post_gitea_issue("tok", "dev", "demo", "标题T", "内容B",
                                 base_url="http://gitea.internal/")
    assert resp == {"number": 5}
    method, url, token, body = mock_req.call_args[0]
    assert method == "POST"
    assert url == "http://gitea.internal/api/v1/repos/dev/demo/issues"
    assert body == {"title": "标题T", "body": "内容B"}


def test_post_gitlab_issue_uses_urlencoded_path_and_description():
    from src.api.issues_center import _post_gitlab_issue
    with patch("src.user.repo_manager._gitlab_req") as mock_req:
        mock_req.return_value = {"iid": 9}
        resp = _post_gitlab_issue("tok", "a b", "svc", "标题G", "内容G",
                                  base_url="https://gitlab.internal")
    assert resp == {"iid": 9}
    method, url, token, body = mock_req.call_args[0]
    assert method == "POST"
    assert url == "https://gitlab.internal/api/v4/projects/a%20b%2Fsvc/issues"
    # GitLab 用 description 而非 body
    assert body == {"title": "标题G", "description": "内容G"}


# ================= base_url 解析与兜底 =================

class _FakeCfg:
    def __init__(self, cfg):
        self._cfg = cfg

    def get(self, k, d=""):
        return self._cfg.get(k, d)


def test_gitea_issue_base_url_default_instance():
    from src.api.issues_center import _gitea_issue_base_url
    cfg = {"gitea_default_instance": "priv",
           "gitea_instances": [{"id": "priv", "base_url": "http://gitea.internal"},
                               {"id": "official", "base_url": "https://gitea.com"}]}
    with patch("src.admin.config_store.load_config", return_value=cfg):
        assert _gitea_issue_base_url() == "http://gitea.internal"


def test_gitea_issue_base_url_fallback_official():
    from src.api.issues_center import _gitea_issue_base_url
    with patch("src.admin.config_store.load_config", return_value={}):
        assert _gitea_issue_base_url() == "https://gitea.com"


def test_gitlab_issue_base_url_default_instance():
    from src.api.issues_center import _gitlab_issue_base_url
    cfg = {"gitlab_default_instance": "corp",
           "gitlab_instances": [{"id": "corp", "base_url": "https://gitlab.corp"}]}
    with patch("src.admin.config_store.load_config", return_value=cfg):
        assert _gitlab_issue_base_url() == "https://gitlab.corp"


def test_gitlab_issue_base_url_fallback_official():
    from src.api.issues_center import _gitlab_issue_base_url
    with patch("src.admin.config_store.load_config", return_value={}):
        assert _gitlab_issue_base_url() == "https://gitlab.com"


# ================= _resolve_issue_user_token =================

def _mk_user(uid=9, username="u1", provider="", instance=""):
    return {"id": uid, "username": username, "login_provider": provider,
            "login_instance": instance}


def test_resolve_token_gitea_user_token_and_cred_base_url():
    from src.api.issues_center import _resolve_issue_user_token
    with patch("src.api.deps.get_user_gitea_token", return_value="ptok"), \
         patch("src.api.deps.get_user_gitea_credential",
               return_value={"base_url": "http://gitea.internal"}):
        token, base = _resolve_issue_user_token(_mk_user(), "gitea")
    assert token == "ptok"
    assert base == "http://gitea.internal"


def test_resolve_token_gitea_admin_fallback():
    from src.api.issues_center import _resolve_issue_user_token
    cfg = {"gitea_default_instance": "priv",
           "gitea_instances": [{"id": "priv", "token": "atok",
                                "base_url": "http://gitea.internal"}]}
    with patch("src.api.deps.get_user_gitea_token", return_value=""), \
         patch("src.api.deps.get_user_gitea_credential", return_value=None), \
         patch("src.admin.config_store.load_config", return_value=cfg):
        token, base = _resolve_issue_user_token(_mk_user(instance="priv"), "gitea")
    assert token == "atok"
    assert base == "http://gitea.internal"


def test_resolve_token_gitea_missing_raises_400():
    from src.api.issues_center import _resolve_issue_user_token
    with patch("src.api.deps.get_user_gitea_token", return_value=""), \
         patch("src.api.deps.get_user_gitea_credential", return_value=None), \
         patch("src.admin.config_store.load_config", return_value={}):
        with pytest.raises(Exception) as ei:
            _resolve_issue_user_token(_mk_user(), "gitea")
    assert getattr(ei.value, "status_code", 0) == 400
    assert "Gitea" in str(ei.value.detail)


def test_resolve_token_gitlab_user_token_and_cred_base_url():
    from src.api.issues_center import _resolve_issue_user_token
    with patch("src.api.deps.get_user_gitlab_token", return_value="gltok"), \
         patch("src.api.deps.get_user_gitlab_credential",
               return_value={"base_url": "https://gitlab.corp"}):
        token, base = _resolve_issue_user_token(_mk_user(), "gitlab")
    assert token == "gltok"
    assert base == "https://gitlab.corp"


def test_resolve_token_github_missing_raises_400():
    from src.api.issues_center import _resolve_issue_user_token
    with patch("src.user.db.get_user_github_token", return_value=""):
        with pytest.raises(Exception) as ei:
            _resolve_issue_user_token(_mk_user(), "github")
    assert getattr(ei.value, "status_code", 0) == 400
    assert "GitHub" in str(ei.value.detail)


def test_resolve_token_gitcode():
    from src.api.issues_center import _resolve_issue_user_token
    with patch("src.api.deps.get_user_token_with_refresh", return_value="gtok"):
        token, base = _resolve_issue_user_token(_mk_user(), "gitcode")
    assert token == "gtok"
    assert base == ""


# ================= seed 引导（镜像重建后实例配置自动恢复） =================

def test_load_config_seed_bootstrap(tmp_path, monkeypatch):
    """global-bot-token.json 缺失时从 seed 引导初始化实例结构。"""
    import json as _json
    import importlib
    import src.admin.config_store as cs
    importlib.reload(cs)
    gbt = tmp_path / "global-bot-token.json"
    seed = tmp_path / "global-bot-token.seed.json"
    seed.write_text(_json.dumps({
        "gitea_mode": "multi", "gitea_default_instance": "priv",
        "gitea_instances": [{"id": "priv", "name": "P", "base_url": "http://g",
                             "auth_mode": "token", "token": ""}],
    }), encoding="utf-8")
    monkeypatch.setattr(cs, "_GBT_PATH", str(gbt))
    monkeypatch.setattr(cs, "_SEED_PATH", str(seed))
    cfg = cs.load_config()
    assert cfg.get("gitea_default_instance") == "priv"
    assert [i["id"] for i in cfg.get("gitea_instances", [])] == ["priv"]
    assert gbt.exists()  # seed 已物化为正式配置文件
    # 二次读取：直接读已物化文件，不再覆盖
    cfg2 = cs.load_config()
    assert cfg2 == cfg


def test_load_config_no_seed_returns_empty(tmp_path, monkeypatch):
    """无 seed 且无配置文件时返回空 dict（原行为不变）。"""
    import importlib
    import src.admin.config_store as cs
    importlib.reload(cs)
    monkeypatch.setattr(cs, "_GBT_PATH", str(tmp_path / "gbt.json"))
    monkeypatch.setattr(cs, "_SEED_PATH", str(tmp_path / "seed.json"))
    assert cs.load_config() == {}
