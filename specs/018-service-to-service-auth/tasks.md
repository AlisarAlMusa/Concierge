---
description: "Task list for Service-to-Service Authentication (Phase 1) — Owner C"
---

# Tasks: Service-to-Service Authentication (Phase 1)

**Input**: Design documents from `specs/018-service-to-service-auth/`

**Owner**: Person C (`feature/c-service-auth` branch)

**Tests**: Mandatory — FR-009 makes integration tests an acceptance gate.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with sibling tasks (different files, no dependencies)
- **[Story]**: Maps to user stories in [spec.md](./spec.md) (US1, US2, US3)
- Each task includes the exact file path to edit or create

---

## Phase 1: Setup — Dependencies & Skeleton

**Purpose**: Add Vault SDK to every service that needs it and create the directory skeletons. Nothing in this phase changes runtime behaviour.

- [ ] **T001** [P] [US3] Add `hvac>=2.3` and `pydantic-settings>=2.2` to `backend/pyproject.toml` (already has pydantic-settings — just add `hvac`). Run `uv lock` in `backend/`.
- [ ] **T002** [P] [US3] Add `hvac>=2.3` and `pydantic-settings>=2.2` to `model_server/pyproject.toml`. Run `uv lock` in `model_server/`.
- [ ] **T003** [P] [US3] Add `hvac>=2.3` and `pydantic-settings>=2.2` to `guardrails_sidecar/pyproject.toml`. Run `uv lock` in `guardrails_sidecar/`.
- [ ] **T004** [P] [US3] Create empty `model_server/app/core/__init__.py` and `guardrails_sidecar/app/core/__init__.py`.
- [ ] **T005** [P] [US3] Add `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_SERVICE_AUTH_PATH` to `.env.example`. Mark `SERVICE_AUTH_SECRET` as "local-mode fallback only".

**Checkpoint**: All three services have their lockfiles refreshed; no behaviour change yet.

---

## Phase 2: Foundational — Vault Client + Settings Wiring

**Purpose**: Get the credential out of Vault and into each service's settings singleton. No endpoint behaviour changes yet — this is plumbing.

**⚠️ Blocks Phase 3+**: User-story work cannot begin until startup can fetch from Vault.

- [ ] **T006** [US3] Create `backend/app/core/vault.py` with `fetch_service_token() -> str`. Use `hvac.Client` with a 2-second timeout, KV v2 read at `kv/<VAULT_SERVICE_AUTH_PATH>`, key `token`. Raise `VaultUnavailable` (new exception in this file) on auth failure, missing path, or short (`< 32` byte) value. **Do not log the token value** anywhere — log path + addr only.
- [ ] **T007** [US3] Modify `backend/app/core/config.py`:
  - Add `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_SERVICE_AUTH_PATH` fields with sane defaults for local (`http://vault:8200`, `dev-root-token`, `concierge/service-auth`).
  - In `get_settings()` (after the `@lru_cache` instantiation), if `settings.APP_ENV != "local"`, call `fetch_service_token()` and overwrite `settings.SERVICE_AUTH_SECRET`. If `APP_ENV == "local"` and `SERVICE_AUTH_SECRET` is empty, log a `structlog.warning` and continue.
- [ ] **T008** [P] [US3] Copy `core/vault.py` into `model_server/app/core/vault.py` (identical contents — see "Structure Decision" in [plan.md](./plan.md)).
- [ ] **T009** [P] [US3] Copy `core/vault.py` into `guardrails_sidecar/app/core/vault.py`.
- [ ] **T010** [P] [US3] Create `model_server/app/core/config.py` — minimal `pydantic-settings` instance with `APP_ENV`, `SERVICE_AUTH_SECRET`, `VAULT_*` fields, and the same post-init Vault fetch.
- [ ] **T011** [P] [US3] Create `guardrails_sidecar/app/core/config.py` — same as T010.
- [ ] **T012** [US3] Update `docker-compose.yml`:
  - `api`, `model_server`, `guardrails_sidecar` services receive env vars `VAULT_ADDR=http://vault:8200`, `VAULT_TOKEN=dev-root-token`, `VAULT_SERVICE_AUTH_PATH=concierge/service-auth`.
  - Each declares `depends_on: vault: condition: service_started`.
  - Add a new one-shot service `vault-init` (image `hashicorp/vault:latest`, command writes the initial secret idempotently). It runs once on stack up, then exits 0.
- [ ] **T013** [US3] Write `scripts/seed_vault.sh` invoked by `vault-init`: enables the KV v2 engine at `kv/`, writes `kv/concierge/service-auth token=<random>` if not already present. Use `vault kv get` to detect existence — do not overwrite. Make the script executable and idempotent across `docker compose up` cycles.

**Checkpoint**: `docker compose up` produces three services whose `get_settings().SERVICE_AUTH_SECRET` is populated from Vault. Run `docker compose exec api python -c "from app.core.config import get_settings; print(bool(get_settings().SERVICE_AUTH_SECRET))"` — expect `True`.

---

## Phase 3: User Story 1 — Sidecar Rejects Unauthenticated Traffic (P1) 🎯 MVP

**Goal**: Every business endpoint on `model_server` and `guardrails_sidecar` returns `403` without a valid `X-Service-Token`.

- [ ] **T014** [P] [US1] Create `model_server/app/core/security.py` mirroring `backend/app/core/security.py` — `verify_service_token` + `require_service_token` reading from the local settings singleton.
- [ ] **T015** [P] [US1] Create `guardrails_sidecar/app/core/security.py` (identical to T014, against its own settings).
- [ ] **T016** [US1] Modify `model_server/app/main.py`:
  - Add `from app.core.security import require_service_token`.
  - Add `dependencies=[Depends(require_service_token)]` to the `/predict-intent` route (and `/predict-lead-score` if present).
  - **Do not** add it to `/health`.
- [ ] **T017** [US1] Modify `guardrails_sidecar/app/main.py`:
  - Delete the existing inline `_verify_service_token` stub.
  - Apply `Depends(require_service_token)` to every business route (`/guardrails/check-input`, `/guardrails/check-output`, `/guardrails/redact`).
  - Leave `/health` open.
- [ ] **T018** [US1] Create `backend/tests/integration/__init__.py` and `backend/tests/integration/conftest.py` with two fixtures: `authenticated_client(app)` and `unauthenticated_client(app)`, both wired via `httpx.AsyncClient(transport=ASGITransport(app=app))`.
- [ ] **T019** [US1] Create `backend/tests/integration/test_service_auth.py` with at minimum:
  - `test_guardrails_redact_without_token_returns_403` — POST `/guardrails/redact` via `unauthenticated_client`, assert `403` and body shape `{"detail": ..., "code": ...}`.
  - `test_guardrails_redact_wrong_token_returns_403` — same as above with `headers={"X-Service-Token": "wrong"}`.
  - `test_model_server_predict_without_token_returns_403`.
  - `test_health_remains_open` — `GET /health` via `unauthenticated_client` on both sidecars returns `200`.

**Checkpoint** (Acceptance Scenario verification for US1): All four tests pass. Manual `curl` to a sidecar without the header returns `403`.

---

## Phase 4: User Story 2 — Authenticated Outbound Calls (P1)

**Goal**: Every outbound `httpx` call from `api` attaches `X-Service-Token` exactly once, set on the shared client.

- [ ] **T020** [US2] Modify `backend/app/main.py`'s `lifespan`:
  - After `setup_tracing(app)` and before `get_engine()`, construct `app.state.service_client = httpx.AsyncClient(headers={"X-Service-Token": settings.SERVICE_AUTH_SECRET}, timeout=10.0)`.
  - On shutdown (after `await close_engine()`), call `await app.state.service_client.aclose()`.
- [ ] **T021** [US2] Add a `Depends()` accessor in `backend/app/dependencies.py`:
  ```python
  def get_service_client(request: Request) -> httpx.AsyncClient:
      return request.app.state.service_client
  ```
- [ ] **T022** [US2] Audit every existing service that calls a sidecar (currently RouterService, GuardrailService stubs) and convert any per-call `httpx.AsyncClient(...)` construction to use the shared `get_service_client` dependency. **Do not** add the header at the call site — it must come from the shared client.
- [ ] **T023** [US2] Create `backend/tests/integration/test_outbound_headers.py`:
  - Use `httpx.MockTransport` to intercept outbound requests from the shared client.
  - Assert that every captured request carries `X-Service-Token` matching the test fixture's token.
  - Cover at least one call to each sidecar (model_server, guardrails_sidecar) — drive them through a representative service-layer call, not a hand-built `client.post(...)`.
- [ ] **T024** [US2] Add a positive-path integration test `test_authenticated_request_returns_200` — using `authenticated_client` against an in-process sidecar app, assert `200` and a schema-valid response body.

**Checkpoint** (US2 Acceptance): `pytest backend/tests/integration/ -v` is green. Service-layer code contains zero hard-coded references to the token string.

---

## Phase 5: User Story 3 — Vault is the Source of Truth (P1)

**Goal**: Production startup refuses to boot without Vault; local startup falls back to `.env` with a warning.

- [ ] **T025** [US3] Add `test_startup_fails_without_vault_in_prod_mode` in `backend/tests/integration/test_service_auth.py`:
  - Sets `APP_ENV=staging` and `VAULT_ADDR=http://127.0.0.1:1` (unreachable).
  - Asserts `get_settings()` raises `VaultUnavailable`.
- [ ] **T026** [US3] Add `test_local_mode_allows_env_fallback`:
  - Sets `APP_ENV=local`, `SERVICE_AUTH_SECRET=fixture-token`, no Vault.
  - Asserts settings populate without raising and a warning is emitted (capture via `caplog` or structlog test capture).
- [ ] **T027** [US3] Add `test_token_rotation_via_restart`:
  - Writes token A to Vault, instantiates settings, asserts value A.
  - Writes token B to Vault, clears the `lru_cache` on `get_settings`, asserts value B.
  - (No hot-reload — that's Phase 2.)

**Checkpoint** (US3 Acceptance): The three Vault-source tests pass. Killing the Vault container and restarting `api` produces a non-zero exit (`docker compose ps` shows the api in `Exited (1)`).

---

## Phase 6: CI Gates & Polish

**Purpose**: Enforce the success criteria mechanically so the regressions can't sneak back in.

- [ ] **T028** [US3] Add a grep-based lint step to `.github/workflows/ci.yml` (SC-003):
  ```yaml
  - name: No direct getenv for service auth
    run: |
      ! grep -rn "os.getenv(\"SERVICE_AUTH_SECRET\"" backend/app guardrails_sidecar/app model_server/app \
        --exclude-dir=core || (echo "Found forbidden direct getenv; use settings"; exit 1)
  ```
- [ ] **T029** [US3] Add a CI step that runs the integration suite (SC-001, SC-002) — `uv run pytest backend/tests/integration/ -v`.
- [ ] **T030** [US3] Add a CI step for SC-006 (no token in logs): run `pytest` with `-s --tb=short 2>&1 | tee /tmp/pytest.log`, then `! grep -F "$TEST_SERVICE_TOKEN" /tmp/pytest.log`. Use a fixture-generated test token, not a fixed string.
- [ ] **T031** [P] Update [docs/SPEC.md](../../docs/SPEC.md) §7 if any contract details changed (header name remains `X-Service-Token`; do not change). Note in §7 that the secret originates in Vault, not env.
- [ ] **T032** [P] Update [docs/RUNBOOK.md](../../docs/RUNBOOK.md) with the new `vault-init` service, the seed script location, and the steps to rotate the token (write new value in Vault, `docker compose restart api model_server guardrails_sidecar`).

**Checkpoint**: CI green on a clean run of `feature/c-service-auth`. All six success criteria observable in the CI summary.

---

## Phase 7: Out-of-Scope (Track as Follow-ups)

These are explicitly **NOT** part of Phase 1. List here to keep them from being silently dropped:

- [ ] Vault AppRole auth (replace dev-mode root token).
- [ ] Per-pair tokens (api↔model_server distinct from api↔guardrails_sidecar).
- [ ] Token hot-reload without restart.
- [ ] mTLS in addition to bearer token.
- [ ] Promote `core/vault.py` to a shared internal package once a 4th service joins.

---

## Dependency Graph

```text
T001..T005  (Setup, all [P])
     │
     ▼
T006 ──► T007 ──► T012 ──► T013   (Vault wiring in backend, then compose)
 │         │
 ├──► T008 ─► T010
 └──► T009 ─► T011               (sidecar parity, can run [P])
                │
                ▼
            T014, T015 [P]        (security mirrors)
                │
                ▼
            T016, T017            (apply Depends to sidecar routes)
                │
                ▼
            T018 ──► T019         (US1 tests)
                │
                ▼
            T020 ─► T021 ─► T022  (shared httpx client in api)
                          │
                          ▼
                     T023, T024   (US2 tests, [P])
                          │
                          ▼
                     T025..T027   (US3 tests, [P] after T007)
                          │
                          ▼
                     T028..T032   (CI + docs)
```

**MVP cut**: T001–T019 alone deliver User Story 1 (sidecars reject unauth traffic from Vault-backed secret). Everything after T019 hardens and verifies.
