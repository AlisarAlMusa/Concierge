# Implementation Plan: Service-to-Service Authentication (Phase 1)

**Branch**: `feature/c-service-auth` | **Date**: 2026-05-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/018-service-to-service-auth/spec.md`

---

## Summary

Phase 1 closes the gap called out in [`docs/SPEC.md`](../../docs/SPEC.md) §7 and constitution Principle III: the shared `SERVICE_AUTH_SECRET` MUST be sourced from HashiCorp Vault, attached automatically to every outbound `httpx` request from `api`, and validated by a FastAPI `Depends()` on every business endpoint in both sidecars. The current state is a static settings string read from env in `api`, a duplicate `os.getenv` lookup in `guardrails_sidecar`, and no token check at all in `model_server` — three different implementations of the same concern. This plan unifies them on a single Vault-backed credential and a single dependency.

Token rotation, per-pair tokens, and mTLS are explicitly Phase 2.

---

## Technical Context

**Language/Version**: Python 3.11 (pinned in CI), 3.12 in containers — matches the existing backend.

**Primary Dependencies**: FastAPI, `httpx` (async), `hvac` (Vault SDK, new), pydantic-settings, structlog, pytest, pytest-asyncio.

**Storage**: HashiCorp Vault (dev mode container, KV v2 secret engine at `kv/concierge/service-auth`). No Postgres or Redis state involved.

**Testing**: pytest + pytest-asyncio. Integration tests boot the sidecar `FastAPI` apps via `httpx.AsyncClient(transport=ASGITransport(app=...))` — no live Compose stack required for the assertion-level tests. A separate `tests/integration/test_service_auth_compose.py` does a live-stack smoke test.

**Target Platform**: Linux containers (Docker Compose v2+). Three services participate: `api` (caller), `model_server` + `guardrails_sidecar` (callees).

**Project Type**: Multi-service web application — the existing `backend/`, `model_server/`, `guardrails_sidecar/` layout from spec 001.

**Performance Goals**: Vault fetch ≤ 250 ms during service startup (single round-trip, cached for the process lifetime). Token validation overhead ≤ 50 µs per request (constant-time compare on a ≤ 64-byte string).

**Constraints**: 
- No `os.getenv("SERVICE_AUTH_SECRET")` outside `core/config.py` and `core/vault.py` (and the sidecar equivalents).
- The token MUST NOT appear in logs, span attributes, or error responses.
- `GET /health` MUST remain unauthenticated on every service.
- Constant-time comparison only (`hmac.compare_digest`).

**Scale/Scope**: Three services, one shared secret, one Vault path, ~10 integration tests. ~150 LoC of new code total.

---

## Constitution Check

*Gate evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.0*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Tenant Isolation | ✅ N/A | Service-auth is below the tenant layer; no `tenant_id` involved. |
| II. Clean Layered Architecture | ✅ Pass | Vault client confined to `core/vault.py`. Token validation in `core/security.py`. Routes call `Depends(require_service_token)` — no leak across layers. |
| III. Security by Default | ✅ Pass | This feature *is* the implementation of Principle III's "Service-to-service auth: API → guardrails sidecar → model-server calls MUST use a service credential resolved from Vault." Constant-time compare; identical 403 responses; no token in logs. |
| IV. Async All the Way Down | ✅ Pass | `hvac` exposes a sync client but is called exactly once during the FastAPI lifespan — startup blocking is acceptable. Per-request validation is synchronous CPU work, ≤ 50 µs. Outbound `httpx` calls remain async. |
| V. Lean Containers — No Torch | ✅ Pass | `hvac` is ~200 KB pure Python with no native deps. No torch. |
| VI. Evals Are the Grade | ✅ Pass | SC-001 through SC-006 are measurable gates; SC-001 and SC-006 are CI-enforced. |

**Post-design re-check**: No new violations.

---

## Project Structure

### Documentation (this feature)

```text
specs/018-service-to-service-auth/
├── plan.md              # This file
├── spec.md              # Feature spec
├── tasks.md             # Granular task checklist
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── core/
│   │   ├── config.py              # MODIFY: post-init Vault fetch when APP_ENV != "local"
│   │   ├── security.py            # MODIFY: keep verify_service_token; keep require_service_token; no Vault logic here
│   │   └── vault.py               # NEW: fetch_service_token() — single entry point to Vault
│   ├── dependencies.py            # MODIFY: add get_service_http_client() returning the shared AsyncClient
│   ├── services/
│   │   └── http_clients.py        # NEW (optional): construct the authenticated httpx.AsyncClient in lifespan
│   └── main.py                    # MODIFY: build authenticated AsyncClient in lifespan; close on shutdown
├── tests/
│   └── integration/
│       ├── __init__.py            # NEW
│       ├── conftest.py            # NEW: vault-token fixture, authenticated client fixture
│       ├── test_service_auth.py   # NEW: 403 / 200 assertions per FR-009
│       └── test_outbound_headers.py  # NEW: ASGI transport assertion per SC-002

model_server/
├── app/
│   ├── main.py                    # MODIFY: add Depends(require_service_token) to /predict-intent (and any other business routes); leave /health open
│   ├── core/
│   │   ├── __init__.py            # NEW
│   │   ├── security.py            # NEW: require_service_token mirror (reads from settings)
│   │   ├── config.py              # NEW: pydantic-settings instance with Vault-backed SERVICE_AUTH_SECRET
│   │   └── vault.py               # NEW: identical fetch_service_token()
│   └── pyproject.toml             # MODIFY: add hvac, pydantic-settings

guardrails_sidecar/
├── app/
│   ├── main.py                    # MODIFY: delete _verify_service_token stub; apply Depends() to all business routes
│   ├── core/
│   │   ├── __init__.py            # NEW
│   │   ├── security.py            # NEW: require_service_token mirror
│   │   ├── config.py              # NEW
│   │   └── vault.py               # NEW
│   └── pyproject.toml             # MODIFY: add hvac, pydantic-settings

docker-compose.yml                 # MODIFY: api/model_server/guardrails_sidecar depend on vault healthy; pass VAULT_ADDR + VAULT_TOKEN env
.env.example                       # MODIFY: document VAULT_ADDR, VAULT_TOKEN, VAULT_SERVICE_AUTH_PATH; mark SERVICE_AUTH_SECRET as local-only fallback
.github/
└── workflows/
    └── ci.yml                     # MODIFY: add the grep-based "no direct getenv" lint step (SC-003)
```

**Structure Decision**: The Vault fetch logic is duplicated across `backend/`, `model_server/`, and `guardrails_sidecar/` rather than extracted into a shared library, because each service has its own image and `pyproject.toml`. The duplication is ~30 lines per service and matches the existing architectural choice (the three services are deliberately not sharing a Python package). Promoting `core/vault.py` to a shared package is a Phase 2 candidate but not justified by ~90 lines of regex-stable code.

---

## Phase 0: Research

No open research questions. The decisions below were taken at spec time and reflect existing project conventions:

| Decision | Choice | Why |
|---|---|---|
| Vault auth method (Phase 1) | Dev-mode root token via `VAULT_TOKEN` env | The Compose stack already runs `vault server -dev`. AppRole auth is Phase 2. |
| Vault SDK | `hvac` | The official Python client; ~200 KB; no native deps; constitution Principle V compliant. |
| Secret engine + path | KV v2 at `kv/concierge/service-auth`, key `token` | Convention in HashiCorp docs; one path keeps Phase 1 simple. |
| Comparison primitive | `hmac.compare_digest` | Already used in `backend/app/core/security.py`. Constant-time. |
| Header name | `X-Service-Token` | Already documented in [docs/SPEC.md](../../docs/SPEC.md) §7. Do not break the contract. |
| Per-call vs shared client header | Shared `httpx.AsyncClient(headers={...})` | Eliminates the "I forgot to attach the header" failure mode. |
| Local-mode fallback | `.env` allowed when `APP_ENV == "local"`, with a warning log | Developer ergonomics. Production paths refuse to start without Vault. |

---

## Phase 1: Design

### 1.1 Vault client — `core/vault.py`

```python
# Sketch — not the full implementation
import hvac
from app.core.config import get_settings

class VaultUnavailable(RuntimeError): ...

def fetch_service_token() -> str:
    settings = get_settings()
    client = hvac.Client(url=settings.VAULT_ADDR, token=settings.VAULT_TOKEN, timeout=2)
    if not client.is_authenticated():
        raise VaultUnavailable(f"Vault auth failed at {settings.VAULT_ADDR}")
    read = client.secrets.kv.v2.read_secret_version(
        path=settings.VAULT_SERVICE_AUTH_PATH,
        mount_point="kv",
    )
    token = read["data"]["data"]["token"]
    if not token or len(token) < 32:
        raise VaultUnavailable("service-auth secret is missing or too short")
    return token
```

- One function. No caching at module scope — the caller (config layer) caches in the settings singleton.
- Failure is loud and propagates: a bad Vault state crashes startup.
- Logging at this level uses `extra={"vault_path": ..., "vault_addr": ...}` — **never** the token value.

### 1.2 Settings post-init — `core/config.py`

```python
# In get_settings() (the @lru_cache singleton):
settings = Settings(...)
if settings.APP_ENV != "local":
    settings.SERVICE_AUTH_SECRET = fetch_service_token()
elif not settings.SERVICE_AUTH_SECRET:
    log.warning("APP_ENV=local: SERVICE_AUTH_SECRET unset and Vault not consulted")
return settings
```

- One branch. Local devs can keep working from `.env`; CI and any non-local env hits Vault.

### 1.3 FastAPI dependency — `core/security.py` (unchanged in api, mirrored in sidecars)

Already implemented in `backend/app/core/security.py`:
- `verify_service_token(token: str) -> bool` uses `hmac.compare_digest`.
- `require_service_token(x_service_token: str = Header(..., alias="X-Service-Token"))` raises `HTTPException(403, "Invalid service token")` on mismatch.

Plan applies the same dependency object to every business route on the sidecars.

### 1.4 Authenticated outbound client — `services/http_clients.py`

```python
# Built in lifespan, stored on app.state:
app.state.service_client = httpx.AsyncClient(
    headers={"X-Service-Token": settings.SERVICE_AUTH_SECRET},
    timeout=10.0,
)
# Service code:
async def call_guardrails(payload):
    client: httpx.AsyncClient = app.state.service_client
    return await client.post(f"{settings.GUARDRAILS_URL}/guardrails/check-input", json=payload)
```

- One client. One place where the header is set. Service code never sees the token.

### 1.5 Test fixtures — `tests/integration/conftest.py`

Two fixtures:
1. `service_token` — generates a fresh 32-byte token per test session, writes it to a dev Vault container, monkey-patches `get_settings()` to surface it.
2. `authenticated_client` / `unauthenticated_client` — two `httpx.AsyncClient` instances wired to the in-process sidecar via `ASGITransport`. One sets `X-Service-Token`, the other does not.

Tests never use the real Compose Vault; they use `vault server -dev` in an ephemeral subprocess or skip the live-Vault subset on CI.

---

## Phase 2: Implementation Order

This is the order in which a single developer (Owner C) should implement Phase 1 to keep the system bootable at every step. Each row produces a runnable state.

| # | Step | Output |
|---|---|---|
| 1 | Add `hvac` + `pydantic-settings` to `model_server/pyproject.toml` and `guardrails_sidecar/pyproject.toml`. | `uv lock` clean in both services. |
| 2 | Create `core/vault.py` in `backend/app/core/`. | `fetch_service_token()` can be unit-tested in isolation against a stub `hvac` client. |
| 3 | Modify `backend/app/core/config.py` to call `fetch_service_token()` post-init when `APP_ENV != "local"`. | `get_settings()` populates `SERVICE_AUTH_SECRET` from Vault in non-local mode. |
| 4 | Mirror `core/vault.py`, `core/config.py`, `core/security.py` into `model_server/app/core/` and `guardrails_sidecar/app/core/`. | Sidecars have parity with `api` for token handling. |
| 5 | Apply `Depends(require_service_token)` to every business route in `model_server/app/main.py` and `guardrails_sidecar/app/main.py`. Delete the legacy `_verify_service_token` stub. | Sidecars refuse unauthenticated traffic. |
| 6 | Construct authenticated `httpx.AsyncClient` in `backend/app/main.py` lifespan; expose via `app.state.service_client`. Add a `Depends()` accessor in `dependencies.py`. | `api` outbound calls auto-attach the header. |
| 7 | Convert any existing per-call header logic in service code to use the shared client. | Single source of truth for the header. |
| 8 | Update `docker-compose.yml`: `api`, `model_server`, `guardrails_sidecar` declare `depends_on: vault: condition: service_started`; receive `VAULT_ADDR=http://vault:8200`, `VAULT_TOKEN=dev-root-token`, `VAULT_SERVICE_AUTH_PATH=concierge/service-auth`. | Compose stack boots all three services with Vault wired. |
| 9 | Write the initial Vault secret in an idempotent boot script (e.g. `scripts/seed_vault.sh`) or as a one-shot `vault-init` Compose service. | Fresh `docker compose up` works without manual `vault kv put`. |
| 10 | Write integration tests under `backend/tests/integration/`. | SC-001, SC-002, SC-005 verified. |
| 11 | Add the grep-based "no direct getenv" lint step to `.github/workflows/ci.yml`. | SC-003 verified. |
| 12 | Add the "redacted log scan" CI step for SC-006 (greps test-run logs for the known test-token value). | SC-006 verified. |

---

## Complexity Tracking

No constitution violations requiring justification. The `core/vault.py` duplication across three services is a deliberate trade (see "Structure Decision" above) and is recorded as a known follow-up.

---

## Open Gaps (Phase 2+)

These are out of scope for this spec but are tracked so they don't get lost:

| Gap | Owner | Trigger to address |
|---|---|---|
| Vault AppRole auth (replace root token) | Person A | Before any non-local deployment. |
| Per-pair tokens (api↔model_server distinct from api↔guardrails) | Person C | When a sidecar is exposed to a different blast radius. |
| Token hot-reload without restart | Person C | When rotation cadence drops below the restart cost. |
| Mutual TLS in addition to bearer token | Person A | Once Vault PKI engine is operational. |
| Sharing `core/vault.py` as a small internal package | TBD | If a fourth service joins the trust circle. |
