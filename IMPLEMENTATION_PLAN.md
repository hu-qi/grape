## Stage 1: Login contracts and regression tests
**Goal**: Define GitLab single/multi-instance OAuth configuration and isolated identity behavior.
**Success Criteria**: Tests describe masked config, instance selection, state binding and login isolation.
**Tests**: GitLab configuration and OAuth tests (mock upstream only).
**Status**: Completed

## Stage 2: Configuration and authentication
**Goal**: Implement GitLab instance configuration, OAuth callbacks and instance-aware sessions.
**Success Criteria**: Login uses configured endpoints; identity cannot collide across instances; secrets stay server-side.
**Tests**: Successful login, replay/invalid state, disabled instance, upstream errors and refresh claims.
**Status**: Completed

## Stage 3: Admin and login UI
**Goal**: Add editable instance settings and selectable GitLab login entries.
**Success Criteria**: Single mode shows one entry; multi mode selects configured instances; secrets remain masked.
**Tests**: JavaScript syntax and focused UI behavior tests.
**Status**: Completed

## Stage 4: Verification and deployment guidance
**Goal**: Run relevant regressions and document GitLab application setup and live acceptance boundaries.
**Success Criteria**: Focused tests pass, existing auth remains compatible, deployment requirements documented.
**Tests**: Auth/config/session regressions, compile and diff checks; real OAuth requires operator application credentials.
**Status**: Completed

## Stage 5: Gitea and GitLab PAT/Token authentication support
**Goal**: Enable Personal Access Token (PAT) authentication for both GitLab and Gitea instances when OAuth applications are unavailable or restricted.
**Success Criteria**:
- Gitea configuration supports `allow_token_login` option.
- Authentication endpoints `/api/auth/gitlab/token-login` and `/api/auth/gitea/token-login` verify upstream user identity via API tokens without storing tokens persistently.
- Login page dynamically prompts Token modal dialog when `allow_token_login` is enabled and OAuth is not configured.
- Admin dashboard supports configuring, toggling, and persisting `allow_token_login` for GitLab and Gitea instances.
**Tests**: Focused unit tests in `test_gitlab_login.py` and `test_gitea_login.py` covering token verification, whitelisting, session creation, and invalid tokens.
**Status**: Completed

## Stage 6: Multi-instance configuration and live deployment verification
**Goal**: Deploy changes to test environment (http://172.16.145.207:18899/kubernetes/gpditest/namespace/aidev) and verify live login flows.
**Success Criteria**:
- Configured instances (internal GitLab `https://gitlab.devops.cndatacom.com:18443`, private Gitea `http://172.16.145.202:30344`, official Gitea `https://gitea.com`) correctly display on login page without mutual interference.
- Fixed 502 connection issue to internal GitLab by using correct port (`18443`), `hostAliases`, and SSL `verify=False` for enterprise internal environments.
- Direct Token login succeeds for internal GitLab (`9527` / 胡琦) and private Gitea (`dev`).
**Status**: Completed

## Stage 7: Gitea & GitLab API Token persistence and repository management
**Goal**: Persist upstream API tokens upon login and bridge repository endpoints (`/api/repos/member`, `/api/repos/sync`, `/api/repos/check-name`, `/api/repos/create`, `/api/repos/host`) for Gitea and GitLab instances.
**Success Criteria**:
- Gitea and GitLab login flows (both OAuth and Token login) securely store the upstream API token in user credentials keyed by user and instance.
- `_get_user_provider_and_token` resolves active Gitea / GitLab tokens based on `login_provider` and `login_instance`.
- Resolves the 400 error (`API Token 未配置`) when calling `/api/repos/member` after logging in with Gitea or GitLab.
- `src/user/repo_manager.py` implements upstream repository listing, creation, and webhook configuration for Gitea and GitLab with SSL bypass for internal networks.
- Frontend repository list displays proper platform icons, badges, and external repository links.
**Tests**: Unit tests in `test_gitlab_login.py`, `test_gitea_login.py`, and `test_gitea_gitlab_repos.py`.
**Status**: Completed

## Stage 8: Mutually exclusive auth mode (none/oauth/token), Admin-level Token configuration, and Login UI multi-instance recognition
**Goal**: Standardize GitLab and Gitea instance configuration into three mutually exclusive authentication modes (`none`, `oauth`, `token`), allow configuring system-level tokens in Admin for platform-wide repository synchronization fallback, and enhance the login modal to intelligently distinguish official vs. self-hosted instances with corresponding authentication badges.
**Success Criteria**:
- Backend configuration schemas (`gitlab_config.py`, `gitea_config.py`) support `auth_mode: 'none' | 'oauth' | 'token'` and `token` storage with backward compatibility.
- Admin dashboard (`admin.js`, `admin.py`, `config_store.py`) provides radio selection for authentication mode, dynamic section display, and on-the-fly instance token verification.
- `_get_user_provider_and_token` and `_fetch_and_cache` fall back to the Admin-configured instance token when a user has no personal PAT bound.
- Login page (`app.js`) multi-instance modal displays "Official / 私有实例" and "OAuth / Token" badges for clear operator and user guidance.
**Tests**: Regression test suite `tests/test_gitlab_login.py`, `tests/test_gitea_login.py`, `tests/test_gitea_gitlab_repos.py` (40 passed).
**Status**: Completed

## Stage 9: Self-hosted Gitea/GitLab external links and Issue creation
**Goal**: Fix the three self-hosted Gitea defects — `#repos` external links jumping to `gitea.com`, `#dash` top project link jumping to `gitcode.com`, and issue creation returning 400 (`GitCode API Token 未配置`) — so Gitea and GitLab instances work end to end for repository browsing and requirement creation.
**Success Criteria**:
- Data layer: `user_repos` cache persists `html_url` (real instance web URL) written by `fetch_gitea_repos_with_role` / `fetch_gitlab_repos_with_role`; repo list and managed endpoints pass through `html_url` and `repo_key`.
- Frontend: `repos.js` `_repoUrl` and `app.js` `_vcsUrl` resolve real instance base URLs (captured from `/auth/gitea/instances` / `/auth/gitlab/instances` and current repo `htmlUrl`) instead of hardcoding `gitea.com` / `gitcode.com`; `#dash` top project link and platform icons support `gitea:` / `gitlab:` prefixes; `selectRepo` / `switchRepo` persist `htmlUrl` in `currentRepo` session state; `_openPRDetail` reuses the unified `_vcsUrl`.
- Backend: `issues_center.py` adds `_post_gitea_issue`, `_post_gitlab_issue`, `_patch_gitea_issue_body`, `_patch_gitlab_issue_body`, `_gitea_issue_base_url`, `_gitlab_issue_base_url`, and `_resolve_issue_user_token` (personal PAT → Admin instance token fallback, platform-specific 400 messages); `create_task_via_issue`, `create_standard_task` and `_find_open_issue_by_title` dispatch by `repo_key` prefix (`github:` / `gitea:` / `gitlab:` / `gitcode:`); GitLab project paths are URL-encoded and issues use `description` (not `body`); multi-task-mode marker PATCH covers Gitea/GitLab.
- Resolves the 400 error when creating an issue on `gitea:dev/devops-bot` via `POST /api/issues/dev/devops-bot`.
**Tests**: New unit tests `tests/test_gitea_gitlab_issues.py` (16 passed); regressions `test_gitlab_login.py`, `test_gitea_login.py`, `test_gitea_gitlab_repos.py`, `test_issues_api.py`, `test_issue_dedupe.py` (75 passed, 1 pre-existing unrelated failure on clean main); JS checks `test_issue_290_view_issue.js` (7), `test_issue_202` (4), `test_issue_203` (7), `test_issue_12_host_confirm` (6) all passed; `py_compile` and `node --check` clean.
**Live verification (2026-10-15)**: Commit `89ddc031` deployed to test env (`http://172.16.145.202:30404`, NodePort 30404, namespace `aidev`). Restored Gitea/GitLab instance config via Admin API (image rebuild resets git-ignored `configs/global-bot-token.json`). E2E: Gitea PAT token-login as `private_gitea` → `POST /api/issues/dev/devops-bot` with `repo_key: gitea:dev/devops-bot` → Issue #56 created (`ok: true`); `/api/repos/member` returns `html_url` pointing to `http://172.16.145.202:30344/dev/...` for all 8 Gitea repos. Test PAT revoked after verification.
**Status**: Completed



