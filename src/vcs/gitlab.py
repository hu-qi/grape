"""GitLab VCS 后端实现 — 支持 GitLab Webhook 事件归一化"""
from src.config import log
from .base import VcsBackend
from .event_types import (
    ISSUE_OPENED, ISSUE_UPDATED, ISSUE_CLOSED, ISSUE_REOPENED,
    ISSUE_ASSIGNED, ISSUE_LABELED, ISSUE_COMMENT,
    PR_OPENED, PR_UPDATED, PR_CLOSED, PR_MERGED, PR_COMMENT,
    PUSH, TAG_CREATED, RELEASE_CREATED, BUILD_START,
    normalize_changes,
)


class GitLabBackend(VcsBackend):
    def __init__(self, provider="gitlab"):
        self.provider = provider

    name = "gitlab"
    webhook_path = "/webhook/gitlab"
    event_header_key = "X-Gitlab-Event"

    def normalize(self, raw, event_header) -> dict:
        """GitLab Webhook → 统一事件格式"""
        payload = self.decode_payload(raw)

        object_kind = payload.get("object_kind", "")
        obj = payload.get("object_attributes", {})
        action = (obj.get("action") or "").lower() if isinstance(obj, dict) else ""

        raw_changes = payload.get("changes", {})
        changes = normalize_changes(raw_changes) if isinstance(raw_changes, dict) else {}

        evt_type = self._map_event_type(object_kind, action, changes, payload)

        repo = self._extract_repo(payload)
        actor = self._extract_actor(payload)
        branch = self._extract_branch(payload)
        title = obj.get("title", "") if isinstance(obj, dict) else ""

        from src.vcs.providers.gitlab import normalize_response
        for key in ("user", "assignee", "assignees"):
            if key in payload:
                payload[key] = normalize_response(payload[key])
        payload["_raw_action"] = action
        payload["_changes"] = changes

        # Keep native fields, and expose the neutral fields consumed by task prompts,
        # issue persistence and self-loop detection.
        if isinstance(obj, dict):
            neutral = dict(obj)
            neutral["number"] = obj.get("iid", obj.get("id"))
            neutral["body"] = obj.get("description", "")
            neutral["html_url"] = obj.get("url", "")
            neutral["user"] = payload.get("user", {})
            if object_kind == "issue":
                payload["issue"] = neutral
            elif object_kind == "merge_request":
                neutral["head"] = {"ref": obj.get("source_branch", "")}
                neutral["base"] = {"ref": obj.get("target_branch", "")}
                neutral["merged"] = obj.get("state") == "merged" or action == "merge"
                payload["pull_request"] = neutral
            elif object_kind == "note":
                payload["comment"] = {"id": obj.get("id"), "body": obj.get("note", ""),
                                      "user": payload.get("user", {}), "html_url": obj.get("url", "")}
        for key in ("issue", "merge_request"):
            if isinstance(payload.get(key), dict):
                value = payload[key]
                value.setdefault("number", value.get("iid"))
                value.setdefault("body", value.get("description", ""))
        if object_kind == "note" and isinstance(payload.get("merge_request"), dict):
            mr = payload["merge_request"]
            payload["pull_request"] = {**mr, "head": {"ref": mr.get("source_branch", "")},
                                       "base": {"ref": mr.get("target_branch", "")}}

        return {
            "source": "gitlab",
            "type": evt_type,
            "repository": repo,
            "branch": branch,
            "actor": actor,
            "payload": payload,
            "title": title,
        }

    def _map_event_type(self, object_kind: str, action: str, changes: dict, payload: dict) -> str:
        # Issue events
        if object_kind == "issue":
            if action == "open": return ISSUE_OPENED
            if action == "close": return ISSUE_CLOSED
            if action == "reopen": return ISSUE_REOPENED
            if action == "update":
                if "assignees" in changes or "assignee_id" in changes:
                    return ISSUE_ASSIGNED
                if "labels" in changes:
                    return ISSUE_LABELED
                return ISSUE_UPDATED
            return ISSUE_UPDATED

        # Note (comment) events
        if object_kind == "note":
            obj = payload.get("object_attributes", {})
            noteable_type = obj.get("noteable_type", "") if isinstance(obj, dict) else ""
            if noteable_type == "MergeRequest":
                return PR_COMMENT
            return ISSUE_COMMENT if noteable_type == "Issue" else "note"

        # Merge request events
        if object_kind == "merge_request":
            if action == "merge": return PR_MERGED
            if action == "open": return PR_OPENED
            if action == "update": return PR_UPDATED
            if action == "close": return PR_CLOSED
            if action == "reopen": return PR_OPENED
            return PR_OPENED

        # Other events
        if object_kind == "push": return PUSH
        if object_kind == "tag_push": return TAG_CREATED
        if object_kind == "release": return RELEASE_CREATED
        if object_kind == "pipeline": return BUILD_START

        return object_kind

    def _extract_repo(self, payload: dict) -> str:
        project = payload.get("project", {})
        if isinstance(project, dict):
            return project.get("path_with_namespace", project.get("full_name", ""))
        repo = payload.get("repository", {})
        if isinstance(repo, dict):
            return repo.get("full_name", repo.get("name", ""))
        return ""

    def _extract_actor(self, payload: dict) -> str:
        user = payload.get("user", {})
        if isinstance(user, dict):
            return user.get("username", user.get("name", ""))
        obj = payload.get("object_attributes", {})
        if isinstance(obj, dict):
            author = obj.get("author", {})
            if isinstance(author, dict):
                return author.get("username", author.get("name", ""))
        return ""

    def _extract_branch(self, payload: dict) -> str:
        ref = payload.get("ref", "")
        if isinstance(ref, str) and ref.startswith("refs/heads/"):
            return ref[11:]
        obj = payload.get("object_attributes") or {}
        if payload.get("object_kind") == "merge_request":
            return obj.get("source_branch", "")
        return ""

    def get_collaborators_url(self, owner: str, repo: str) -> str:
        from src.vcs.gitlab_config import get_instance
        return get_instance(self.provider).project_url(f"{owner}/{repo}") + "/members/all"
