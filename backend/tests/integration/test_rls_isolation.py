"""Integration tests: RLS tenant isolation.

Covers:
  US1 — all 12 tables exist post-0004; 10 have RLS policies
  US2 — querying with tenant A context never returns tenant B rows
  US3 — unscoped SELECT still enforces RLS (parametrized across all 10 tables)

Requires a live PostgreSQL with migration 0004 applied.
Skip automatically when TEST_DATABASE_URL is not set.
"""

from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")

_SKIP_NO_DB = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL not set — skipping Postgres integration tests",
)


def _asyncpg_url(url: str) -> str:
    """Strip SQLAlchemy driver prefix so asyncpg.connect() accepts it."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def asyncpg_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Raw asyncpg connection; skip if TEST_DATABASE_URL is unset."""
    if not _TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set — skipping Postgres integration tests")
    conn = await asyncpg.connect(_asyncpg_url(_TEST_DB_URL))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture()
async def two_tenants(
    asyncpg_conn: asyncpg.Connection,
) -> AsyncGenerator[tuple[uuid.UUID, uuid.UUID], None]:
    """Insert two tenant rows; yield (tenant_a_id, tenant_b_id); clean up all test rows."""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    await asyncpg_conn.execute(
        """
        INSERT INTO tenants (id, name, slug)
        VALUES ($1, 'Tenant A', $2), ($3, 'Tenant B', $4)
        """,
        tid_a,
        f"tenant-a-{tid_a}",
        tid_b,
        f"tenant-b-{tid_b}",
    )
    yield tid_a, tid_b

    # Cleanup — reset RLS context, then delete in FK-safe order
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', '', false)")

    for table in (
        "messages",
        "leads",
        "escalations",
        "guardrail_configs",
        "cms_chunks",
        "conversations",
        "widgets",
        "cms_pages",
        "cost_events",
        "audit_logs",
    ):
        await asyncpg_conn.execute(
            f"DELETE FROM {table} WHERE tenant_id = $1 OR tenant_id = $2",  # noqa: S608
            tid_a,
            tid_b,
        )
    await asyncpg_conn.execute(
        "DELETE FROM tenants WHERE id = $1 OR id = $2",
        tid_a,
        tid_b,
    )


# ---------------------------------------------------------------------------
# US1 — Schema introspection: all tables exist, 10 have RLS policies
# ---------------------------------------------------------------------------

_ALL_TABLES = {
    "tenants",
    "users",
    "audit_logs",
    "cost_events",
    "cms_chunks",
    "cms_pages",
    "widgets",
    "conversations",
    "messages",
    "leads",
    "escalations",
    "guardrail_configs",
}

_RLS_TABLES = {
    "audit_logs",
    "cost_events",
    "cms_chunks",
    "cms_pages",
    "widgets",
    "conversations",
    "messages",
    "leads",
    "escalations",
    "guardrail_configs",
}


@pytest.mark.integration
@_SKIP_NO_DB
async def test_all_tables_exist(asyncpg_conn: asyncpg.Connection) -> None:
    rows = await asyncpg_conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    found = {r["tablename"] for r in rows}
    missing = _ALL_TABLES - found
    assert not missing, f"Tables missing from public schema: {missing}"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_all_rls_tables_have_policy(asyncpg_conn: asyncpg.Connection) -> None:
    rows = await asyncpg_conn.fetch("""
        SELECT tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND policyname LIKE '%_tenant_isolation'
        """)
    policy_tables = {r["tablename"] for r in rows}
    missing = _RLS_TABLES - policy_tables
    assert not missing, f"RLS tables missing tenant_isolation policy: {missing}"
    extra = policy_tables - _RLS_TABLES
    assert not extra, f"Unexpected tables have tenant_isolation policy: {extra}"


# ---------------------------------------------------------------------------
# US2 — Cross-tenant data isolation
# ---------------------------------------------------------------------------


@pytest.mark.integration
@_SKIP_NO_DB
async def test_tenant_a_cannot_see_tenant_b_leads(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tid_a, tid_b = two_tenants
    await asyncpg_conn.execute(
        "INSERT INTO leads (id, tenant_id, intent) VALUES ($1, $2, 'inquiry'), ($3, $4, 'inquiry')",
        uuid.uuid4(),
        tid_a,
        uuid.uuid4(),
        tid_b,
    )
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid_a))
    rows = await asyncpg_conn.fetch("SELECT tenant_id FROM leads")
    tenant_ids = {r["tenant_id"] for r in rows}
    assert tid_b not in tenant_ids, "Tenant A context exposed Tenant B leads"
    assert tid_a in tenant_ids, "Tenant A cannot see its own leads"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_tenant_a_cannot_see_tenant_b_messages(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tid_a, tid_b = two_tenants

    wid_a, wid_b = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        """
        INSERT INTO widgets (id, tenant_id, public_widget_id, name)
        VALUES ($1, $2, $3, 'W-A'), ($4, $5, $6, 'W-B')
        """,
        wid_a,
        tid_a,
        f"pub-{wid_a}",
        wid_b,
        tid_b,
        f"pub-{wid_b}",
    )
    conv_a, conv_b = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        """
        INSERT INTO conversations (id, tenant_id, widget_id, visitor_session_id)
        VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)
        """,
        conv_a,
        tid_a,
        wid_a,
        uuid.uuid4(),
        conv_b,
        tid_b,
        wid_b,
        uuid.uuid4(),
    )
    await asyncpg_conn.execute(
        """
        INSERT INTO messages (id, tenant_id, conversation_id, role, content_redacted)
        VALUES ($1, $2, $3, 'visitor', 'hello'), ($4, $5, $6, 'visitor', 'hello')
        """,
        uuid.uuid4(),
        tid_a,
        conv_a,
        uuid.uuid4(),
        tid_b,
        conv_b,
    )
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid_a))
    rows = await asyncpg_conn.fetch("SELECT tenant_id FROM messages")
    tenant_ids = {r["tenant_id"] for r in rows}
    assert tid_b not in tenant_ids, "Tenant A context exposed Tenant B messages"
    assert tid_a in tenant_ids, "Tenant A cannot see its own messages"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_tenant_a_cannot_see_tenant_b_cms_pages(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tid_a, tid_b = two_tenants
    await asyncpg_conn.execute(
        """
        INSERT INTO cms_pages (id, tenant_id, title, slug, body)
        VALUES ($1, $2, 'Page A', $3, 'body'), ($4, $5, 'Page B', $6, 'body')
        """,
        uuid.uuid4(),
        tid_a,
        f"slug-{uuid.uuid4()}",
        uuid.uuid4(),
        tid_b,
        f"slug-{uuid.uuid4()}",
    )
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid_a))
    rows = await asyncpg_conn.fetch("SELECT tenant_id FROM cms_pages")
    tenant_ids = {r["tenant_id"] for r in rows}
    assert tid_b not in tenant_ids, "Tenant A context exposed Tenant B cms_pages"
    assert tid_a in tenant_ids, "Tenant A cannot see its own cms_pages"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_unscoped_query_blocked_by_rls(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tid_a, tid_b = two_tenants
    for tid in (tid_a, tid_b):
        await asyncpg_conn.execute(
            "INSERT INTO leads (id, tenant_id, intent) VALUES ($1, $2, 'test')",
            uuid.uuid4(),
            tid,
        )
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid_a))
    # SELECT without WHERE — RLS must filter automatically
    rows = await asyncpg_conn.fetch("SELECT tenant_id FROM leads")
    tenant_ids = {r["tenant_id"] for r in rows}
    assert tid_b not in tenant_ids, "Unscoped query returned Tenant B rows"
    assert tid_a in tenant_ids, "Unscoped query returned zero rows for context tenant"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_no_context_returns_zero_rows(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tid_a, tid_b = two_tenants
    for tid in (tid_a, tid_b):
        await asyncpg_conn.execute(
            "INSERT INTO leads (id, tenant_id, intent) VALUES ($1, $2, 'test')",
            uuid.uuid4(),
            tid,
        )
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', '', false)")
    rows = await asyncpg_conn.fetch("SELECT id FROM leads")
    assert len(rows) == 0, "Empty RLS context should return zero rows"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_insert_blocked_for_wrong_tenant(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """WITH CHECK: inserting a row with tenant_b's id under tenant_a context must fail."""
    tid_a, tid_b = two_tenants
    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid_a))
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await asyncpg_conn.execute(
            "INSERT INTO leads (id, tenant_id, intent) VALUES ($1, $2, 'blocked')",
            uuid.uuid4(),
            tid_b,
        )


# ---------------------------------------------------------------------------
# US3 — RLS covers all 10 tables (parametrized)
# ---------------------------------------------------------------------------

# Tables and minimal INSERT tuples: (table, columns, value_placeholders, row_factory)
# row_factory receives (tenant_id, **deps) and returns positional arg list for $1..$n
#
# Tables with FK dependencies get deps injected via the _rls_all_tables_setup fixture.


_RLS_TABLE_NAMES = sorted(_RLS_TABLES)


@pytest.fixture()
async def rls_all_tables_setup(
    asyncpg_conn: asyncpg.Connection,
    two_tenants: tuple[uuid.UUID, uuid.UUID],
) -> dict:
    """Create one row per RLS table per tenant for the parametrized test.

    Returns a mapping of table → (row_a_id, row_b_id) for assertion.
    Relies on two_tenants fixture for tenant rows and cleanup.
    """
    tid_a, tid_b = two_tenants
    row_ids: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}

    # cms_pages (no FK deps beyond tenant)
    pa, pb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO cms_pages (id, tenant_id, title, slug, body) VALUES "
        "($1,$2,'T','slug-a','b'),($3,$4,'T','slug-b','b')",
        pa,
        tid_a,
        pb,
        tid_b,
    )
    row_ids["cms_pages"] = (pa, pb)

    # cms_chunks (requires page_id FK)
    ca, cb = uuid.uuid4(), uuid.uuid4()
    zeros = "[" + ",".join(["0"] * 1024) + "]"
    await asyncpg_conn.execute(
        f"""
        INSERT INTO cms_chunks (id, tenant_id, page_id, chunk_index, text, embedding)
        VALUES ($1,$2,$3,0,'t','{zeros}'::vector),
               ($4,$5,$6,0,'t','{zeros}'::vector)
        """,
        ca,
        tid_a,
        pa,
        cb,
        tid_b,
        pb,
    )
    row_ids["cms_chunks"] = (ca, cb)

    # widgets
    wa, wb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO widgets (id, tenant_id, public_widget_id, name) VALUES "
        "($1,$2,$3,'W'),($4,$5,$6,'W')",
        wa,
        tid_a,
        f"pub-{wa}",
        wb,
        tid_b,
        f"pub-{wb}",
    )
    row_ids["widgets"] = (wa, wb)

    # conversations (requires widget FK)
    cva, cvb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO conversations (id, tenant_id, widget_id, visitor_session_id) VALUES "
        "($1,$2,$3,$4),($5,$6,$7,$8)",
        cva,
        tid_a,
        wa,
        uuid.uuid4(),
        cvb,
        tid_b,
        wb,
        uuid.uuid4(),
    )
    row_ids["conversations"] = (cva, cvb)

    # messages (requires conversation FK)
    ma, mb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO messages (id, tenant_id, conversation_id, role, content_redacted) VALUES "
        "($1,$2,$3,'visitor','x'),($4,$5,$6,'visitor','x')",
        ma,
        tid_a,
        cva,
        mb,
        tid_b,
        cvb,
    )
    row_ids["messages"] = (ma, mb)

    # leads (conversation FK nullable)
    la, lb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO leads (id, tenant_id, intent) VALUES ($1,$2,'x'),($3,$4,'x')",
        la,
        tid_a,
        lb,
        tid_b,
    )
    row_ids["leads"] = (la, lb)

    # escalations (conversation FK nullable)
    ea, eb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO escalations (id, tenant_id, reason) VALUES ($1,$2,'r'),($3,$4,'r')",
        ea,
        tid_a,
        eb,
        tid_b,
    )
    row_ids["escalations"] = (ea, eb)

    # guardrail_configs (UNIQUE per tenant — one row each)
    ga, gb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO guardrail_configs (id, tenant_id) VALUES ($1,$2),($3,$4)",
        ga,
        tid_a,
        gb,
        tid_b,
    )
    row_ids["guardrail_configs"] = (ga, gb)

    # cost_events
    cea, ceb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO cost_events (id, tenant_id, provider, model, operation) VALUES "
        "($1,$2,'openai','gpt-4o','llm'),($3,$4,'openai','gpt-4o','llm')",
        cea,
        tid_a,
        ceb,
        tid_b,
    )
    row_ids["cost_events"] = (cea, ceb)

    # audit_logs (tenant_id nullable — insert with explicit tenant ids)
    ala, alb = uuid.uuid4(), uuid.uuid4()
    await asyncpg_conn.execute(
        "INSERT INTO audit_logs (id, actor_role, tenant_id, action) VALUES "
        "($1,'tenant_admin',$2,'test'),($3,'tenant_admin',$4,'test')",
        ala,
        tid_a,
        alb,
        tid_b,
    )
    row_ids["audit_logs"] = (ala, alb)

    return {"tid_a": tid_a, "tid_b": tid_b, "row_ids": row_ids}


@pytest.mark.integration
@_SKIP_NO_DB
@pytest.mark.parametrize("table", _RLS_TABLE_NAMES)
async def test_rls_covers_all_ten_tables(
    asyncpg_conn: asyncpg.Connection,
    rls_all_tables_setup: dict,
    table: str,
) -> None:
    tid_a = rls_all_tables_setup["tid_a"]
    row_ids = rls_all_tables_setup["row_ids"]
    row_a_id, row_b_id = row_ids[table]

    await asyncpg_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tid_a))
    rows = await asyncpg_conn.fetch(f"SELECT id, tenant_id FROM {table}")  # noqa: S608

    ids = {r["id"] for r in rows}
    assert row_b_id not in ids, f"RLS on {table}: tenant_b row visible under tenant_a context"
    assert row_a_id in ids, f"RLS on {table}: tenant_a row NOT visible under its own context"
    for r in rows:
        assert r["tenant_id"] == tid_a, f"RLS on {table}: row with wrong tenant_id leaked through"
