"""dependency_check 报错信息与门禁行为测试。

背景（2026-10-21）：vod/issue/coding/qa/sre 派发时若 agent_tasks.repo_gitcode_token
为空会直接抛 DependencyError。该字段是历史命名（语义=仓库 bot token，任意平台），
报错文案带上 repo 与真实 provider 才能区分"某平台没配 token"和"配置丢了"。
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("DEVOPS_DB_PATH", os.path.join(ROOT, "data", "test_sessions.db"))

import pytest

from src.runtime.dependency_check import DependencyError, validate_task_dependencies


def _task(**kw):
    t = {"id": "t_testdc", "agent_type": "vod", "prompt": "do it", "skill": "vod"}
    t.update(kw)
    return t


def test_empty_token_raises_with_repo_and_provider():
    """Gitea 仓库缺 token 时，报错要带 repo 与 provider=gitea（而非笼统 gitcode）。"""
    with pytest.raises(DependencyError) as ei:
        validate_task_dependencies(_task(repo_key="gitea:org/repo", repo_gitcode_token=""))
    msg = str(ei.value)
    assert "repo_gitcode_token is empty for git agent vod" in msg
    assert "repo=gitea:org/repo" in msg
    assert "provider=gitea" in msg


def test_empty_token_gitlab_provider():
    with pytest.raises(DependencyError) as ei:
        validate_task_dependencies(_task(repo_key="gitlab:org/repo", repo_gitcode_token=""))
    assert "provider=gitlab" in str(ei.value)


def test_empty_token_without_prefix_defaults_gitcode():
    with pytest.raises(DependencyError) as ei:
        validate_task_dependencies(_task(repo_key="org/repo", repo_gitcode_token=""))
    assert "provider=gitcode" in str(ei.value)


def test_token_present_passes():
    validate_task_dependencies(_task(repo_key="gitea:org/repo", repo_gitcode_token="tok"))


def test_chat_agent_without_token_passes():
    """chat 不属于 git agent，不要求 repo token。"""
    validate_task_dependencies({"id": "t_chat", "agent_type": "chat",
                                "prompt": "hi", "skill": "chat", "repo_gitcode_token": ""})


def test_empty_prompt_still_fails():
    with pytest.raises(DependencyError) as ei:
        validate_task_dependencies(_task(prompt="   ", repo_gitcode_token="tok"))
    assert "prompt is empty" in str(ei.value)