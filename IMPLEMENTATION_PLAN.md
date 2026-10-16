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

## Stage 10: Full cross-provider repo_key support & 403 dashboard permission fix
**Goal**: Resolve the 403 Forbidden error ("No permission to access this repository") on `/api/dashboard/{owner}/{repo}` when accessing self-hosted Gitea and GitLab repositories, and eliminate hardcoded dual-platform (`gitcode:`/`github:`) assumptions across all backend endpoints.
**Success Criteria**:
- `src/repo_key.py`: Abstract `ALL_PROVIDERS = ("gitcode", "github", "gitea", "gitlab")` and `cross_provider_keys(repo_key)` to uniformly generate candidate keys across all 4 supported providers. Export `strip_repo_prefix` as alias to `from_repo_key`.
- `src/api/deps.py`: `check_repo_access` verifies access against all 4 provider candidate keys (`{p}:{base}` for `p` in gitcode, github, gitea, gitlab) rather than only gitcode and github.
- `src/api/routes/dashboard.py`: Refactor `_repo_keys`, `get_dashboard`, `get_service_logs`, `get_event_chains`, metrics calculation, and scene options queries to dynamically bind all provider candidates via `cross_provider_keys`.
- All routes updated with unified 4-platform candidates: `scenes.py`, `skills.py`, `settings.py`, `knowledge.py`, `chat.py`, `events.py`, `agents.py`, `pipeline.py`, `issues.py`, `runtime.py`, `autopilot.py`, `repos.py`, and `user/db.py`.
- `src/runtime/cleanup.py` and `src/session_manager.py`: Replace hardcoded string replacement/checks with `cross_provider_keys` and `strip_repo_prefix`.
- Frontend `app.js`: Update `stripRepoPrefix` regex to match `/^(gitcode|github|gitlab|gitea):/`.
**Tests**: Regression suites `test_gitlab_login.py`, `test_gitea_login.py`, `test_gitea_gitlab_repos.py`, `test_gitea_gitlab_issues.py`, `test_git_provider.py`, `test_host_status.py` (74 passed); JS syntax check via `node --check`.
**Status**: Completed

## Stage 11: Fix Gitea/GitLab instance selection modal closing & login cleanup
**Goal**: Fix the bug where "Select Gitea/GitLab Instance" modal remains stuck or unclosed after login or token prompt transition.
**Success Criteria**:
- Entering Token login from the multi-instance modal immediately removes the instance selection modal (`gtInstanceModal` / `glInstanceModal`).
- Triggering OAuth redirection immediately removes the instance selection modal to prevent stale dialogs upon navigation or back/restore.
- Successfully completing login (both OAuth token restoration and Token modal submit) invokes `_closeAllLoginModals()` in `showApp()`, ensuring all login-related overlays are cleanly unmounted.
- Clicking the backdrop overlay outside the dialog content closes the instance and token modals cleanly.
**Tests**: `node -c src/api/static/js/app.js` syntax check passed; regression test suite `test_gitlab_login.py`, `test_gitea_login.py`, `test_gitea_gitlab_repos.py`, `test_gitea_gitlab_issues.py`, `test_git_provider.py`, `test_host_status.py` (74 passed).
**Status**: Completed




