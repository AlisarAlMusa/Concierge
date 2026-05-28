"""Tests for the admin escalations surface (GET/PATCH /escalations).

Validates:

* ``EscalationService.list_escalations`` returns ``(items, total)`` and
  rejects bad pagination args.
* ``EscalationService.get_escalation`` returns ``None`` for absent /
  cross-tenant ids.
* ``EscalationService.update_escalation`` flips status and returns
  ``None`` on miss.
* The HTTP surface honors the dual auth gate and round-trips through to
  the service.
* Cross-tenant access never leaks existence (returns 404, not 403/200).

No real Postgres — fake session + fake service. The service does not
expose DELETE per Spec 012 Assumptions; an unsupported method check
locks that contract in too.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_admin_escalation_service, get_admin_rls_session
from app.main import app
from app.models.escalation import Escalation, EscalationStatus
from app.services.conversation_service import ConversationService
from app.services.escalation_service import EscalationService

SERVICE_TOKEN = "service-secret"

TENANT_A = UUID("00000000-0000-0000-0000-00000000aaaa")
TENANT_B = UUID("00000000-0000-0000-0000-00000000bbbb")


# ─── Fake session + conversation service ─────────────────────────────────
class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0]

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(self) -> None:
        self.execute_results: list[list[Any]] = []
        self.execute_calls: list[Any] = []
        self.flushed = 0

    def enqueue(self, rows: list[Any]) -> None:
        self.execute_results.append(rows)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.execute_calls.append(stmt)
        rows = self.execute_results.pop(0) if self.execute_results else []
        return _FakeResult(rows)

    async def flush(self) -> None:
        self.flushed += 1


class _NoopConversations:
    """Stand-in for ``ConversationService`` — admin paths never call it.

    ``EscalationService`` requires one in its constructor because the same
    service also exposes ``create`` (which does use it). The admin
    list/update methods never invoke it; the contract test verifies that.
    """

    async def set_status(self, **_: Any) -> None:  # pragma: no cover - guard
        raise AssertionError("ConversationService.set_status must not be called from admin paths")


def _fixture_escalation(
    *,
    tenant_id: UUID = TENANT_A,
    status: EscalationStatus = EscalationStatus.open,
    reason: str = "visitor asked for a human",
) -> Escalation:
    escalation = Escalation(
        id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=uuid4(),
        reason=reason,
        context=None,
        status=status,
    )
    escalation.created_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    escalation.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    return escalation


def _svc(session: _FakeSession) -> EscalationService:
    return EscalationService(
        session=session,
        conversation_service=_NoopConversations(),  # type: ignore[arg-type]
    )


# ─── EscalationService.list_escalations ─────────────────────────────────
async def test_list_escalations_returns_items_and_total() -> None:
    session = _FakeSession()
    session.enqueue([5])  # count
    rows = [_fixture_escalation() for _ in range(3)]
    session.enqueue(rows)

    items, total = await _svc(session).list_escalations(tenant_id=TENANT_A)
    assert total == 5
    assert items == rows


async def test_list_escalations_rejects_bad_pagination() -> None:
    svc = _svc(_FakeSession())
    with pytest.raises(ValueError):
        await svc.list_escalations(tenant_id=TENANT_A, limit=0)
    with pytest.raises(ValueError):
        await svc.list_escalations(tenant_id=TENANT_A, limit=501)
    with pytest.raises(ValueError):
        await svc.list_escalations(tenant_id=TENANT_A, offset=-1)


# ─── EscalationService.get_escalation ───────────────────────────────────
async def test_get_escalation_returns_none_when_absent_or_cross_tenant() -> None:
    session = _FakeSession()
    session.enqueue([])
    assert await _svc(session).get_escalation(tenant_id=TENANT_A, escalation_id=uuid4()) is None


# ─── EscalationService.update_escalation ────────────────────────────────
async def test_update_escalation_returns_none_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])
    assert (
        await _svc(session).update_escalation(
            tenant_id=TENANT_A,
            escalation_id=uuid4(),
            status=EscalationStatus.resolved,
        )
        is None
    )


async def test_update_escalation_flips_status() -> None:
    escalation = _fixture_escalation(status=EscalationStatus.open)
    session = _FakeSession()
    session.enqueue([escalation])

    result = await _svc(session).update_escalation(
        tenant_id=TENANT_A,
        escalation_id=escalation.id,
        status=EscalationStatus.in_progress,
    )
    assert result is escalation
    assert escalation.status == EscalationStatus.in_progress
    assert session.flushed == 1


async def test_update_escalation_does_not_touch_conversation_service() -> None:
    """Admin update is intentionally decoupled from conversation status.

    Resolving an escalation must not flip ``Conversation.status`` back to
    ``active`` — that side effect would need explicit product design and
    is out of this PR's scope. The ``_NoopConversations`` raises if
    called, so a clean run proves the contract.
    """
    escalation = _fixture_escalation(status=EscalationStatus.in_progress)
    session = _FakeSession()
    session.enqueue([escalation])

    await _svc(session).update_escalation(
        tenant_id=TENANT_A,
        escalation_id=escalation.id,
        status=EscalationStatus.resolved,
    )  # _NoopConversations.set_status would have AssertionError'd otherwise


# ─── HTTP surface ────────────────────────────────────────────────────────
class _RouteFakeEscalationService:
    def __init__(self) -> None:
        self._by_tenant: dict[UUID, list[Escalation]] = {}
        self.list_calls: list[UUID] = []
        self.update_calls: list[dict[str, Any]] = []

    def seed(self, escalation: Escalation) -> None:
        self._by_tenant.setdefault(escalation.tenant_id, []).append(escalation)

    async def list_escalations(
        self, *, tenant_id: UUID, limit: int = 50, offset: int = 0
    ) -> tuple[list[Escalation], int]:
        self.list_calls.append(tenant_id)
        bucket = list(self._by_tenant.get(tenant_id, []))
        return bucket[offset : offset + limit], len(bucket)

    async def get_escalation(self, *, tenant_id: UUID, escalation_id: UUID) -> Escalation | None:
        for esc in self._by_tenant.get(tenant_id, []):
            if esc.id == escalation_id:
                return esc
        return None

    async def update_escalation(
        self,
        *,
        tenant_id: UUID,
        escalation_id: UUID,
        status: EscalationStatus,
    ) -> Escalation | None:
        self.update_calls.append(
            {
                "tenant_id": tenant_id,
                "escalation_id": escalation_id,
                "status": status,
            }
        )
        esc = await self.get_escalation(tenant_id=tenant_id, escalation_id=escalation_id)
        if esc is None:
            return None
        esc.status = status
        return esc


@pytest.fixture
def fake_service() -> _RouteFakeEscalationService:
    return _RouteFakeEscalationService()


@pytest.fixture
def client(fake_service: _RouteFakeEscalationService):
    app.dependency_overrides[get_admin_escalation_service] = lambda: fake_service

    async def _noop_session():
        yield None

    app.dependency_overrides[get_admin_rls_session] = _noop_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _admin_headers(tenant_id: UUID) -> dict[str, str]:
    return {
        "X-Service-Token": SERVICE_TOKEN,
        "X-Tenant-Id": str(tenant_id),
        "Content-Type": "application/json",
    }


# ─── GET /escalations ────────────────────────────────────────────────────
def test_get_escalations_lists_only_caller_tenant(
    client: TestClient, fake_service: _RouteFakeEscalationService
) -> None:
    fake_service.seed(_fixture_escalation(tenant_id=TENANT_A, reason="A1"))
    fake_service.seed(_fixture_escalation(tenant_id=TENANT_A, reason="A2"))
    fake_service.seed(_fixture_escalation(tenant_id=TENANT_B, reason="B1"))

    a_list = client.get("/escalations", headers=_admin_headers(TENANT_A)).json()
    b_list = client.get("/escalations", headers=_admin_headers(TENANT_B)).json()

    assert a_list["total"] == 2
    assert {item["reason"] for item in a_list["items"]} == {"A1", "A2"}
    assert all(item["tenant_id"] == str(TENANT_A) for item in a_list["items"])

    assert b_list["total"] == 1
    assert b_list["items"][0]["reason"] == "B1"


def test_get_escalations_pagination_args(
    client: TestClient, fake_service: _RouteFakeEscalationService
) -> None:
    for i in range(6):
        fake_service.seed(_fixture_escalation(reason=f"r{i}"))
    page = client.get("/escalations?limit=2&offset=4", headers=_admin_headers(TENANT_A)).json()
    assert page["total"] == 6
    assert len(page["items"]) == 2


def test_get_escalations_rejects_bad_query_args(client: TestClient) -> None:
    bad = client.get("/escalations?limit=501", headers=_admin_headers(TENANT_A))
    assert bad.status_code == 422


# ─── PATCH /escalations/{escalation_id} ─────────────────────────────────
def test_patch_escalation_flips_status(
    client: TestClient, fake_service: _RouteFakeEscalationService
) -> None:
    escalation = _fixture_escalation(status=EscalationStatus.open)
    fake_service.seed(escalation)

    resp = client.patch(
        f"/escalations/{escalation.id}",
        json={"status": "resolved"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["tenant_id"] == str(TENANT_A)


def test_patch_escalation_404_when_absent(client: TestClient) -> None:
    resp = client.patch(
        f"/escalations/{uuid4()}",
        json={"status": "resolved"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_patch_escalation_tenant_isolation(
    client: TestClient, fake_service: _RouteFakeEscalationService
) -> None:
    escalation = _fixture_escalation(tenant_id=TENANT_A)
    fake_service.seed(escalation)

    resp = client.patch(
        f"/escalations/{escalation.id}",
        json={"status": "resolved"},
        headers=_admin_headers(TENANT_B),
    )
    assert resp.status_code == 404
    assert escalation.status == EscalationStatus.open  # untouched


def test_patch_escalation_rejects_invalid_status(
    client: TestClient, fake_service: _RouteFakeEscalationService
) -> None:
    escalation = _fixture_escalation()
    fake_service.seed(escalation)
    resp = client.patch(
        f"/escalations/{escalation.id}",
        json={"status": "exploded"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 422


def test_patch_escalation_requires_status(
    client: TestClient, fake_service: _RouteFakeEscalationService
) -> None:
    """Spec 012 FR-011 — status is the only updatable field, and it's required."""
    escalation = _fixture_escalation()
    fake_service.seed(escalation)
    resp = client.patch(
        f"/escalations/{escalation.id}",
        json={},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 422


# ─── No DELETE surface on escalations (Spec 012 Assumptions) ────────────
def test_no_delete_escalation_route_registered() -> None:
    """Admin cannot delete escalations — that lives in the erasure flow."""
    paths_methods = {(r.path, m) for r in app.routes for m in getattr(r, "methods", set()) or set()}
    assert ("/escalations/{escalation_id}", "DELETE") not in paths_methods
    assert ("/escalations", "DELETE") not in paths_methods


# ─── Auth gates ──────────────────────────────────────────────────────────
def test_get_escalations_rejects_without_service_token(client: TestClient) -> None:
    resp = client.get("/escalations", headers={"X-Tenant-Id": str(TENANT_A)})
    assert resp.status_code in (400, 403, 422)


def test_patch_escalation_rejects_wrong_service_token(client: TestClient) -> None:
    resp = client.patch(
        f"/escalations/{uuid4()}",
        json={"status": "resolved"},
        headers={
            "X-Service-Token": "wrong-secret",
            "X-Tenant-Id": str(TENANT_A),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 403


def test_get_escalations_rejects_bad_tenant_header(client: TestClient) -> None:
    resp = client.get(
        "/escalations",
        headers={
            "X-Service-Token": SERVICE_TOKEN,
            "X-Tenant-Id": "not-a-uuid",
        },
    )
    assert resp.status_code == 400


@pytest.fixture(autouse=True)
def _settings_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``SERVICE_AUTH_SECRET`` so the guard compares against ``SERVICE_TOKEN``."""
    monkeypatch.setenv("SERVICE_AUTH_SECRET", SERVICE_TOKEN)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# Ensure ConversationService import is exercised so basedpyright sees the
# stand-in shape match. (No runtime effect.)
assert ConversationService is not None
