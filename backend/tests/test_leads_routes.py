"""Tests for the admin leads surface (GET/PATCH/DELETE /leads).

Validates:

* ``LeadService.list_leads`` returns ``(items, total)`` and rejects bad
  pagination args at the service layer.
* ``LeadService.get_lead`` returns ``None`` for absent / cross-tenant ids.
* ``LeadService.update_lead`` updates only the requested fields and
  returns ``None`` on miss.
* ``LeadService.delete_lead`` returns ``False`` on miss, ``True`` after
  removing the row.
* The HTTP surface honors the same dual auth gate
  (``X-Service-Token`` + ``X-Tenant-Id``) as ``/cms`` and round-trips
  through to the service.
* Routes never leak across tenants: a request for tenant A cannot see
  / modify / delete tenant B's leads.

No real Postgres — the session and the LeadService are hand-rolled
fakes; the goal is to lock contracts, not the storage engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_admin_lead_service, get_admin_rls_session
from app.main import app
from app.models.lead import Lead, LeadStatus
from app.services.lead_service import LeadService

SERVICE_TOKEN = "service-secret"

TENANT_A = UUID("00000000-0000-0000-0000-00000000aaaa")
TENANT_B = UUID("00000000-0000-0000-0000-00000000bbbb")


# ─── Fake session / settings ─────────────────────────────────────────────
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
        self.deleted: list[Any] = []

    def enqueue(self, rows: list[Any]) -> None:
        self.execute_results.append(rows)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.execute_calls.append(stmt)
        rows = self.execute_results.pop(0) if self.execute_results else []
        return _FakeResult(rows)

    async def flush(self) -> None:
        self.flushed += 1

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)


class _Settings:
    LEAD_CAPTURE_LIMIT_PER_SESSION = 5
    LEAD_CAPTURE_WINDOW_HOURS = 1


def _fixture_lead(
    *,
    tenant_id: UUID = TENANT_A,
    status: LeadStatus = LeadStatus.new,
    notes: str | None = None,
    intent: str = "purchase enterprise plan",
) -> Lead:
    lead = Lead(
        id=uuid4(),
        tenant_id=tenant_id,
        conversation_id=uuid4(),
        visitor_session_id=uuid4(),
        name="Ada",
        email="ada@example.com",
        phone=None,
        intent=intent,
        context=None,
        lead_score=None,
        source="agent",
        status=status,
        notes=notes,
    )
    lead.created_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    lead.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    return lead


# ─── LeadService.list_leads ──────────────────────────────────────────────
async def test_list_leads_returns_items_and_total() -> None:
    session = _FakeSession()
    session.enqueue([4])  # count
    rows = [_fixture_lead() for _ in range(3)]
    session.enqueue(rows)
    svc = LeadService(session=session, settings=_Settings())

    items, total = await svc.list_leads(tenant_id=TENANT_A)
    assert total == 4
    assert items == rows


async def test_list_leads_rejects_bad_pagination() -> None:
    svc = LeadService(session=_FakeSession(), settings=_Settings())
    with pytest.raises(ValueError):
        await svc.list_leads(tenant_id=TENANT_A, limit=0)
    with pytest.raises(ValueError):
        await svc.list_leads(tenant_id=TENANT_A, limit=501)
    with pytest.raises(ValueError):
        await svc.list_leads(tenant_id=TENANT_A, offset=-1)


# ─── LeadService.get_lead ────────────────────────────────────────────────
async def test_get_lead_returns_none_when_absent_or_cross_tenant() -> None:
    session = _FakeSession()
    session.enqueue([])
    svc = LeadService(session=session, settings=_Settings())
    assert await svc.get_lead(tenant_id=TENANT_A, lead_id=uuid4()) is None


# ─── LeadService.update_lead ─────────────────────────────────────────────
async def test_update_lead_returns_none_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])
    svc = LeadService(session=session, settings=_Settings())
    assert (
        await svc.update_lead(tenant_id=TENANT_A, lead_id=uuid4(), status=LeadStatus.contacted)
        is None
    )


async def test_update_lead_status_only() -> None:
    lead = _fixture_lead(status=LeadStatus.new, notes="existing")
    session = _FakeSession()
    session.enqueue([lead])
    svc = LeadService(session=session, settings=_Settings())

    result = await svc.update_lead(tenant_id=TENANT_A, lead_id=lead.id, status=LeadStatus.contacted)
    assert result is lead
    assert lead.status == LeadStatus.contacted
    # Notes unchanged when not in the payload.
    assert lead.notes == "existing"
    assert session.flushed == 1


async def test_update_lead_notes_only() -> None:
    lead = _fixture_lead(status=LeadStatus.contacted, notes=None)
    session = _FakeSession()
    session.enqueue([lead])
    svc = LeadService(session=session, settings=_Settings())

    result = await svc.update_lead(
        tenant_id=TENANT_A, lead_id=lead.id, notes="called Tuesday, will call back"
    )
    assert result is lead
    assert lead.notes == "called Tuesday, will call back"
    # Status unchanged when not in the payload.
    assert lead.status == LeadStatus.contacted


async def test_update_lead_empty_notes_clears() -> None:
    lead = _fixture_lead(notes="existing notes")
    session = _FakeSession()
    session.enqueue([lead])
    svc = LeadService(session=session, settings=_Settings())

    await svc.update_lead(tenant_id=TENANT_A, lead_id=lead.id, notes="")
    assert lead.notes is None  # empty string → cleared


# ─── LeadService.delete_lead ─────────────────────────────────────────────
async def test_delete_lead_returns_false_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])
    svc = LeadService(session=session, settings=_Settings())
    assert await svc.delete_lead(tenant_id=TENANT_A, lead_id=uuid4()) is False
    assert session.deleted == []


async def test_delete_lead_drops_row_and_flushes() -> None:
    lead = _fixture_lead()
    session = _FakeSession()
    session.enqueue([lead])
    svc = LeadService(session=session, settings=_Settings())

    ok = await svc.delete_lead(tenant_id=TENANT_A, lead_id=lead.id)
    assert ok is True
    assert session.deleted == [lead]
    assert session.flushed == 1


# ─── HTTP surface ────────────────────────────────────────────────────────
class _RouteFakeLeadService:
    """Route-level double — in-memory tenant-keyed store."""

    def __init__(self) -> None:
        self._by_tenant: dict[UUID, list[Lead]] = {}
        self.list_calls: list[UUID] = []
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[UUID] = []

    def seed(self, lead: Lead) -> None:
        self._by_tenant.setdefault(lead.tenant_id, []).append(lead)

    async def list_leads(
        self, *, tenant_id: UUID, limit: int = 50, offset: int = 0
    ) -> tuple[list[Lead], int]:
        self.list_calls.append(tenant_id)
        bucket = list(self._by_tenant.get(tenant_id, []))
        return bucket[offset : offset + limit], len(bucket)

    async def get_lead(self, *, tenant_id: UUID, lead_id: UUID) -> Lead | None:
        self.get_calls.append((tenant_id, lead_id))
        for lead in self._by_tenant.get(tenant_id, []):
            if lead.id == lead_id:
                return lead
        return None

    async def update_lead(
        self,
        *,
        tenant_id: UUID,
        lead_id: UUID,
        status: LeadStatus | None = None,
        notes: str | None = None,
    ) -> Lead | None:
        self.update_calls.append(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "status": status,
                "notes": notes,
            }
        )
        lead = await self.get_lead(tenant_id=tenant_id, lead_id=lead_id)
        if lead is None:
            return None
        if status is not None:
            lead.status = status
        if notes is not None:
            lead.notes = notes or None
        return lead

    async def delete_lead(self, *, tenant_id: UUID, lead_id: UUID) -> bool:
        self.delete_calls.append(lead_id)
        bucket = self._by_tenant.get(tenant_id, [])
        for i, lead in enumerate(bucket):
            if lead.id == lead_id:
                bucket.pop(i)
                return True
        return False


@pytest.fixture
def fake_service() -> _RouteFakeLeadService:
    return _RouteFakeLeadService()


@pytest.fixture
def client(fake_service: _RouteFakeLeadService):
    app.dependency_overrides[get_admin_lead_service] = lambda: fake_service

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


# ─── GET /leads ──────────────────────────────────────────────────────────
def test_get_leads_lists_only_caller_tenant(
    client: TestClient, fake_service: _RouteFakeLeadService
) -> None:
    fake_service.seed(_fixture_lead(tenant_id=TENANT_A, intent="A1"))
    fake_service.seed(_fixture_lead(tenant_id=TENANT_A, intent="A2"))
    fake_service.seed(_fixture_lead(tenant_id=TENANT_B, intent="B1"))

    a_list = client.get("/leads", headers=_admin_headers(TENANT_A)).json()
    b_list = client.get("/leads", headers=_admin_headers(TENANT_B)).json()

    assert a_list["total"] == 2
    assert {item["intent"] for item in a_list["items"]} == {"A1", "A2"}
    assert all(item["tenant_id"] == str(TENANT_A) for item in a_list["items"])

    assert b_list["total"] == 1
    assert b_list["items"][0]["intent"] == "B1"


def test_get_leads_pagination_args(client: TestClient, fake_service: _RouteFakeLeadService) -> None:
    for i in range(7):
        fake_service.seed(_fixture_lead(intent=f"lead-{i}"))

    page = client.get("/leads?limit=3&offset=2", headers=_admin_headers(TENANT_A)).json()
    assert page["total"] == 7
    assert len(page["items"]) == 3


def test_get_leads_rejects_bad_query_args(client: TestClient) -> None:
    bad_limit = client.get("/leads?limit=0", headers=_admin_headers(TENANT_A))
    assert bad_limit.status_code == 422
    bad_offset = client.get("/leads?offset=-1", headers=_admin_headers(TENANT_A))
    assert bad_offset.status_code == 422


# ─── PATCH /leads/{lead_id} ──────────────────────────────────────────────
def test_patch_lead_updates_status_and_notes(
    client: TestClient, fake_service: _RouteFakeLeadService
) -> None:
    lead = _fixture_lead(status=LeadStatus.new)
    fake_service.seed(lead)

    resp = client.patch(
        f"/leads/{lead.id}",
        json={"status": "contacted", "notes": "left voicemail"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "contacted"
    assert body["notes"] == "left voicemail"
    assert body["tenant_id"] == str(TENANT_A)


def test_patch_lead_404_when_absent(client: TestClient) -> None:
    resp = client.patch(
        f"/leads/{uuid4()}",
        json={"status": "rejected"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_patch_lead_tenant_isolation(
    client: TestClient, fake_service: _RouteFakeLeadService
) -> None:
    lead = _fixture_lead(tenant_id=TENANT_A)
    fake_service.seed(lead)
    # Tenant B tries to PATCH tenant A's lead → 404, never modifies.
    resp = client.patch(
        f"/leads/{lead.id}",
        json={"status": "rejected"},
        headers=_admin_headers(TENANT_B),
    )
    assert resp.status_code == 404
    assert lead.status == LeadStatus.new  # untouched


def test_patch_lead_rejects_invalid_status(
    client: TestClient, fake_service: _RouteFakeLeadService
) -> None:
    lead = _fixture_lead()
    fake_service.seed(lead)
    resp = client.patch(
        f"/leads/{lead.id}",
        json={"status": "exploded"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 422


# ─── DELETE /leads/{lead_id} ─────────────────────────────────────────────
def test_delete_lead_204_and_404_on_followup(
    client: TestClient, fake_service: _RouteFakeLeadService
) -> None:
    lead = _fixture_lead()
    fake_service.seed(lead)

    resp = client.delete(f"/leads/{lead.id}", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 204
    assert lead.id in fake_service.delete_calls

    follow = client.patch(
        f"/leads/{lead.id}",
        json={"status": "rejected"},
        headers=_admin_headers(TENANT_A),
    )
    assert follow.status_code == 404


def test_delete_lead_404_when_absent(client: TestClient) -> None:
    resp = client.delete(f"/leads/{uuid4()}", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 404


def test_delete_lead_tenant_isolation(
    client: TestClient, fake_service: _RouteFakeLeadService
) -> None:
    lead = _fixture_lead(tenant_id=TENANT_A)
    fake_service.seed(lead)
    cross = client.delete(f"/leads/{lead.id}", headers=_admin_headers(TENANT_B))
    assert cross.status_code == 404
    # Lead still visible to its real owner.
    listing = client.get("/leads", headers=_admin_headers(TENANT_A)).json()
    assert listing["total"] == 1


# ─── Auth gates ──────────────────────────────────────────────────────────
def test_get_leads_rejects_without_service_token(client: TestClient) -> None:
    resp = client.get("/leads", headers={"X-Tenant-Id": str(TENANT_A)})
    assert resp.status_code in (400, 403, 422)


def test_get_leads_rejects_wrong_service_token(client: TestClient) -> None:
    resp = client.get(
        "/leads",
        headers={"X-Service-Token": "wrong-secret", "X-Tenant-Id": str(TENANT_A)},
    )
    assert resp.status_code == 403


def test_delete_lead_rejects_bad_tenant_header(client: TestClient) -> None:
    resp = client.delete(
        f"/leads/{uuid4()}",
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
