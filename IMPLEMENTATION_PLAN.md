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
