# Feature Specification: Service-to-Service Authentication (Phase 1)

> **Owner**: Person C — `feature/c-service-auth` branch

**Feature Branch**: `018-service-to-service-auth`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Sidecar Rejects Unauthenticated Internal Traffic (Priority: P1)

A request lands on `model_server` or `guardrails_sidecar` without an `X-Service-Token` header — for example a misconfigured caller, a probe from a sibling container, or an attacker who has reached the Compose network. The sidecar refuses the request with `403 Forbidden` before any business logic runs. The reason for refusal is the missing or invalid credential, not network adjacency.

**Why this priority**: Sitting on the internal network MUST NOT be sufficient to invoke an LLM gate or call the classifier. Without this rail, every container in the Compose namespace — including any that an attacker has already pivoted into — is implicitly trusted. This is the foundation of every other security guarantee in the system; it has to land first.

**Independent Test**: With the stack running locally, `curl -X POST http://localhost:8002/guardrails/redact -d '{"text":"hi"}' -H 'Content-Type: application/json'`. Expect `403`. Repeat against the `model_server` `/predict-intent` endpoint. Expect `403`.

**Acceptance Scenarios**:

1. **Given** a sidecar endpoint that requires authentication, **When** a request arrives without an `X-Service-Token` header, **Then** the response is `403` with a structured error body and no business logic executes.
2. **Given** an `X-Service-Token` header with the wrong value, **When** the request is received, **Then** the response is `403` and the token comparison is constant-time (no length-distinguishable timing leak).
3. **Given** a malformed header (empty string, whitespace only, oversize), **When** the request is received, **Then** the response is `403` with the same generic reason as case 2 (no oracle behaviour).
4. **Given** repeated `403` responses from the same source, **When** observed externally, **Then** the body is identical — the response MUST NOT disclose whether the token was missing vs. wrong.

---

### User Story 2 — Authenticated Internal Traffic Is Allowed (Priority: P1)

The main `api` container makes outbound `httpx` calls to `guardrails_sidecar` and `model_server` during the chat flow. Each call carries the `X-Service-Token` header — set centrally in the shared client, not per call site. The sidecar validates the header and processes the request normally, returning `200` with a real payload.

**Why this priority**: Authentication that breaks the happy path is not authentication — it is a denial-of-service of our own product. The token wiring must be invisible to service-layer code and must produce green integration tests on the canonical chat flow.

**Independent Test**: Run the integration test `tests/integration/test_service_auth.py::test_authenticated_request_returns_200`. The test boots the sidecars, has the API send a real chat-flow request, and asserts every sidecar call returns 200 and a deserialisable payload.

**Acceptance Scenarios**:

1. **Given** the `api` client is configured with the correct service token, **When** it calls `POST /guardrails/check-input`, **Then** the sidecar returns `200` and the response matches the `CheckInputResponse` schema.
2. **Given** the same client, **When** it calls `POST /predict-intent` on `model_server`, **Then** the response is `200` and matches the `PredictResponse` schema.
3. **Given** any new outbound call added to a service in the future, **When** the call is made through the shared `httpx.AsyncClient`, **Then** the header is attached automatically without per-call configuration.

---

### User Story 3 — Service Credentials Originate in Vault, Not Environment Files (Priority: P1)

On startup, each of the three services (`api`, `model_server`, `guardrails_sidecar`) reads the shared `SERVICE_AUTH_SECRET` from HashiCorp Vault at a well-known path. If Vault is unreachable, the service refuses to start. A leaked `.env` file MUST NOT be sufficient to forge a service token, and rotating the token MUST NOT require redeploying a container.

**Why this priority**: The constitution (Principle III, "Security by Default") makes Vault the authoritative source for service credentials. `os.getenv("SERVICE_AUTH_SECRET")` directly in code is the current state and is explicitly rejected by the constitution. Until secrets move to Vault, the entire service-auth posture is theatre — a leaked `.env` defeats the whole rail.

**Independent Test**: Boot the stack with Vault running but the secret unwritten. The three services MUST fail their readiness check with a clear "Vault secret missing" log line. Write the secret to Vault; restart the services; they boot and `/ready` returns `200`.

**Acceptance Scenarios**:

1. **Given** Vault is running and the secret is written at the expected path, **When** any service starts, **Then** the service fetches the secret once during startup, caches it in a settings singleton, and proceeds to serve traffic.
2. **Given** Vault is unreachable at startup, **When** the service tries to fetch the secret, **Then** the service refuses to start (non-zero exit) and logs the Vault error with no token value exposed.
3. **Given** the secret has been rotated in Vault, **When** the services are restarted (one-by-one or together), **Then** the new value is picked up and continues to authenticate inter-service traffic. *(Hot reload without restart is out of scope for Phase 1.)*
4. **Given** a developer attempts to set `SERVICE_AUTH_SECRET` in `.env` only (no Vault), **When** the service starts in non-`local` mode, **Then** the service refuses to start; in `local` mode an `.env` fallback is allowed and a warning is logged.

---

### Edge Cases

- **Vault token expiry mid-startup**: surfaced as a startup failure — the bootstrap must use a short-lived Vault token and not silently fall back to env.
- **Two services have desynchronised secrets** (one rotated, others not yet restarted): every call from the un-rotated caller fails closed with `403`. There MUST NOT be a "grace period" with both old and new tokens accepted in Phase 1 (that arrives in Phase 2: token rotation).
- **Constant-time comparison**: the equality check MUST use `hmac.compare_digest` (or equivalent). A naive `==` check on long strings can leak prefix information.
- **Header value with whitespace or non-ASCII**: rejected as invalid before constant-time compare; no oracle.
- **Test pollution**: integration tests MUST run against an isolated Vault dev instance, not the developer's local Vault. The test fixture writes a known token, the test runs, the fixture tears down.
- **Health endpoints**: `GET /health` MUST remain unauthenticated on all services so the Docker healthcheck can reach them; only business endpoints require the token.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A shared service credential, identified as `SERVICE_AUTH_SECRET`, MUST be stored in HashiCorp Vault under a documented path (e.g. `kv/concierge/service-auth`).
- **FR-002**: The `api`, `model_server`, and `guardrails_sidecar` services MUST fetch `SERVICE_AUTH_SECRET` from Vault during application startup (lifespan), cache it in the pydantic-settings singleton, and never read it from a header, query string, or per-request location.
- **FR-003**: If Vault is unreachable or the secret is missing at the documented path, services running in any non-`local` environment MUST fail their startup with a non-zero exit code. In `local` mode, a documented `.env` fallback is permitted and MUST log a warning.
- **FR-004**: The `api` service MUST attach the `X-Service-Token` header to every outbound `httpx` request to `model_server` and `guardrails_sidecar`. The wiring MUST happen once, on the shared async client at construction time, not per call site.
- **FR-005**: `model_server` and `guardrails_sidecar` MUST expose a FastAPI `Depends()` dependency that validates `X-Service-Token` on every business endpoint. `GET /health` MUST remain exempt to preserve Docker healthchecks.
- **FR-006**: Token comparison MUST be constant-time (`hmac.compare_digest` or equivalent). A naive `==` check is rejected.
- **FR-007**: A missing token, an empty token, and a wrong token MUST all return the identical `403` response body — no oracle that distinguishes the two cases.
- **FR-008**: The response body for a `403` MUST follow the project's error format (`{"detail": str, "code": str}`) — see SPEC.md §9.
- **FR-009**: Integration tests under `backend/tests/integration/test_service_auth.py` MUST prove: (a) sidecar without token returns `403`; (b) sidecar with correct token returns `200`; (c) `api` outbound calls attach the header automatically.
- **FR-010**: The Vault fetch logic MUST live in `backend/app/core/vault.py` (and equivalents in the sidecars) and MUST be the only place that talks to Vault. Service code MUST read the secret through `get_settings()`, never by importing Vault primitives directly.
- **FR-011**: No log line, exception message, or trace span MAY contain the raw `SERVICE_AUTH_SECRET` value. The PII-redaction span processor from spec 017 is the second line of defence; the first line is structured logging that never includes the token.
- **FR-012**: Token rotation is out of scope for Phase 1 and MUST be tracked as a follow-up. Phase 1 supports rotation only via service restart.

### Key Entities

- **Service Token**: A high-entropy shared secret (≥ 32 bytes, base64-encoded). Stored at a single Vault path. Identical for `api`, `model_server`, `guardrails_sidecar` in Phase 1. Distinct per-pair tokens are a Phase 2 concern.
- **Vault Client**: A thin wrapper (`backend/app/core/vault.py`) around the official `hvac` (or equivalent) SDK. Exposes one function: `fetch_service_token() -> str`. Failure modes are surfaced as exceptions that propagate to the startup path.
- **Settings Singleton**: The pydantic-settings instance from `core/config.py`. Phase 1 adds a post-init step that populates `SERVICE_AUTH_SECRET` from `fetch_service_token()` when not running in `local` mode.
- **Authenticated HTTPX Client**: A shared `httpx.AsyncClient` constructed once in lifespan, configured with `headers={"X-Service-Token": settings.SERVICE_AUTH_SECRET}` so every call automatically carries the token.
- **`require_service_token` Dependency**: The FastAPI `Depends()` callable already drafted in `backend/app/core/security.py`. Mirrored into each sidecar.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of business endpoints on `model_server` and `guardrails_sidecar` reject unauthenticated requests with `403`. A CI test enumerates the OpenAPI route list and asserts each non-`/health` route requires the dependency.
- **SC-002**: 100% of outbound calls from `api` to either sidecar carry the `X-Service-Token` header. A test captures `httpx` traffic via a mock transport and asserts the header presence on every request.
- **SC-003**: Zero occurrences of `os.getenv("SERVICE_AUTH_SECRET")` in service code outside `core/config.py` and `core/vault.py`. Enforced by a grep-based lint check in CI.
- **SC-004**: Service startup adds ≤ 250 ms latency for the Vault fetch step in local mode (single round-trip to the dev Vault container).
- **SC-005**: A leaked `.env` file (with no Vault access) MUST NOT allow a service to forge a token outside `local` mode. Verified by an integration test that boots a service with a fake `.env` and confirms the service refuses to start when `APP_ENV != "local"`.
- **SC-006**: Zero log lines or span attributes contain the raw token value. Verified by a redaction CI test that searches all log output and exported spans for the known test token value.

---

## Assumptions

- Vault runs in dev mode inside Compose (`hashicorp/vault:latest`, root token `dev-root-token`) for Phase 1. Production-grade Vault (sealed, auto-unseal, AppRole authentication) is out of scope for this spec and is tracked as a follow-up.
- Authentication between `api`, `model_server`, and `guardrails_sidecar` is symmetric in Phase 1 — one shared token across all three. Per-pair tokens, mutual TLS, and OIDC-style service identity are Phase 2+ concerns.
- The Docker Compose network remains the runtime boundary. This spec hardens the boundary; it does not remove it.
- `WIDGET_TOKEN_SECRET` is a different secret with a different lifecycle and is not in scope here (see SPEC.md §8).
- The PII-redaction span processor from spec 017 already prevents raw tokens from leaking through OTel exports. This spec must not regress that property.
- The legacy in-code stub in `guardrails_sidecar/app/main.py` (`_verify_service_token` reading directly from `os.getenv`) is removed by this feature.
- The CI grep-based lint check from SC-003 is a low-cost guardrail; it does not replace code review for sensitive paths.
