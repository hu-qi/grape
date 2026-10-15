import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from src.api.main import app
from src.user import db as user_db
from src.api.routes.repos import _get_user_provider_and_token, _fetch_and_cache
from src.user.repo_manager import fetch_gitea_repos_with_role, fetch_gitlab_repos_with_role


client = TestClient(app)


def test_get_user_provider_and_token_gitea():
    username = "test_gitea_user"
    user = user_db.create_user(username, "hash")
    uid = user["id"]
    cred_data = {
        "base_url": "http://172.16.145.202:30344",
        "gitea_user_id": "123",
        "username": username,
        "instance": "private_gitea",
        "token": "88a5b674e52108f861ebb35ea1312ea020c2c1af",
    }
    user_db.set_credential(uid, "gitea_login", cred_data)
    user_db.set_credential(uid, "gitea_login_private_gitea", cred_data)

    provider, token = _get_user_provider_and_token(username, "gitea", "private_gitea")
    assert provider == "gitea"
    assert token == "88a5b674e52108f861ebb35ea1312ea020c2c1af"


def test_get_user_provider_and_token_gitlab():
    username = "test_gitlab_user"
    user = user_db.create_user(username, "hash")
    uid = user["id"]
    cred_data = {
        "base_url": "https://gitlab.devops.cndatacom.com:18443",
        "gitlab_user_id": "9527",
        "username": username,
        "instance": "internal_gitlab",
        "token": "rzdwQymCGB9nDsgEcyyo",
    }
    user_db.set_credential(uid, "gitlab_login", cred_data)
    user_db.set_credential(uid, "gitlab_login_internal_gitlab", cred_data)

    provider, token = _get_user_provider_and_token(username, "gitlab", "internal_gitlab")
    assert provider == "gitlab"
    assert token == "rzdwQymCGB9nDsgEcyyo"


def test_fetch_gitea_repos_with_role_mock():
    mock_repos_page = [
        {
            "id": 1,
            "name": "grape-test",
            "full_name": "dev/grape-test",
            "owner": {"login": "dev"},
            "permissions": {"admin": True, "push": True, "pull": True},
            "created_at": "2026-03-01T00:00:00Z",
            "description": "Test Repo",
            "html_url": "http://172.16.145.202:30344/dev/grape-test",
        }
    ]
    with patch("requests.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_repos_page
        mock_req.return_value = mock_resp

        repos = fetch_gitea_repos_with_role("fake_token", "http://172.16.145.202:30344")
        assert len(repos) == 1
        assert repos[0]["full_name"] == "dev/grape-test"
        assert repos[0]["repo_key"] == "gitea:dev/grape-test"
        assert repos[0]["is_admin"] is True


def test_fetch_gitlab_repos_with_role_mock():
    mock_projects_page = [
        {
            "id": 101,
            "name": "grape-ci",
            "path": "grape-ci",
            "path_with_namespace": "huqi/grape-ci",
            "permissions": {"project_access": {"access_level": 40}},
            "created_at": "2026-03-01T00:00:00Z",
            "description": "GitLab CI Project",
            "web_url": "https://gitlab.devops.cndatacom.com:18443/huqi/grape-ci",
        }
    ]
    with patch("requests.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_projects_page
        mock_req.return_value = mock_resp

        repos = fetch_gitlab_repos_with_role("fake_token", "https://gitlab.devops.cndatacom.com:18443")
        assert len(repos) == 1
        assert repos[0]["full_name"] == "huqi/grape-ci"
        assert repos[0]["repo_key"] == "gitlab:huqi/grape-ci"
        assert repos[0]["is_admin"] is True


def test_admin_token_fallback():
    """测试当用户没有个人 PAT 时，系统能回退使用 Admin 配置的实例 Token"""
    mock_config = {
        "gitea_instances": [
            {
                "id": "my_gitea",
                "auth_mode": "token",
                "base_url": "http://172.16.145.202:30344",
                "token": "admin_gitea_token_secret"
            }
        ],
        "gitlab_instances": [
            {
                "id": "my_gitlab",
                "auth_mode": "token",
                "base_url": "https://gitlab.devops.cndatacom.com:18443",
                "token": "admin_gitlab_token_secret"
            }
        ]
    }
    with patch("src.admin.config_store.load_config", return_value=mock_config):
        # 用户无凭证，入参指定 gitea / my_gitea
        prov, tok = _get_user_provider_and_token("new_user_no_cred", "gitea", "my_gitea")
        assert prov == "gitea"
        assert tok == "admin_gitea_token_secret"

        # 用户无凭证，入参指定 gitlab / my_gitlab
        prov, tok = _get_user_provider_and_token("new_user_no_cred", "gitlab", "my_gitlab")
        assert prov == "gitlab"
        assert tok == "admin_gitlab_token_secret"

