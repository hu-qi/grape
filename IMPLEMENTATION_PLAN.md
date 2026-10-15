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
- Configured instances (internal GitLab `https://gitlab.devops.cndatacom.com`, private Gitea `http://172.16.145.202:30344`, official Gitea `https://gitea.com`) correctly display on login page.
- Direct Token login succeeds for internal GitLab (`9527` / 胡琦) and private Gitea (`dev`).
**Status**: Completed


