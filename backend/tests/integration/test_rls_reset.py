"""Integration tests: RLS session-variable reset.

Covers US4 — app.tenant_id is cleared after every request regardless of
success or failure, so pooled connections cannot carry stale context.

Requires a live PostgreSQL. Skip when TEST_DATABASE_URL is not set.
"""

from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.rls import reset_tenant_context, set_tenant_context

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")

_SKIP_NO_DB = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL not set — skipping Postgres integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def pg_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLAlchemy session connected to a live Postgres; skip if no URL."""
    if not _TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set — skipping Postgres integration tests")
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
async def tenant_with_lead(pg_session: AsyncSession) -> AsyncGenerator[uuid.UUID, None]:
    """Insert one tenant + one lead row; yield tenant_id; clean up on exit."""
    from sqlalchemy import text

    tid = uuid.uuid4()
    lead_id = uuid.uuid4()
    await pg_session.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tid, "name": "Reset Test Tenant", "slug": f"reset-{tid}"},
    )
    await pg_session.execute(
        text("INSERT INTO leads (id, tenant_id, intent) VALUES (:id, :tid, 'reset-test')"),
        {"id": lead_id, "tid": tid},
    )
    await pg_session.commit()

    yield tid

    await pg_session.execute(text("DELETE FROM leads WHERE tenant_id = :tid"), {"tid": tid})
    await pg_session.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})
    await pg_session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@_SKIP_NO_DB
async def test_reset_clears_tenant_context(
    pg_session: AsyncSession,
    tenant_with_lead: uuid.UUID,
) -> None:
    """set_tenant_context then reset_tenant_context → SELECT leads returns empty."""
    from sqlalchemy import text

    await set_tenant_context(pg_session, tenant_with_lead)
    rows_before = (await pg_session.execute(text("SELECT id FROM leads"))).fetchall()
    assert len(rows_before) > 0, "Setup failed: no leads visible under tenant context"

    await reset_tenant_context(pg_session)

    rows_after = (await pg_session.execute(text("SELECT id FROM leads"))).fetchall()
    assert (
        len(rows_after) == 0
    ), "reset_tenant_context did not clear app.tenant_id — leads still visible"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_context_cleared_after_exception(
    pg_session: AsyncSession,
    tenant_with_lead: uuid.UUID,
) -> None:
    """Context must be cleared even when the handler raises an exception."""
    from sqlalchemy import text

    try:
        await set_tenant_context(pg_session, tenant_with_lead)
        raise ValueError("simulated handler failure")
    except ValueError:
        pass
    finally:
        await reset_tenant_context(pg_session)

    setting = (
        await pg_session.execute(text("SELECT current_setting('app.tenant_id', true) AS v"))
    ).scalar_one()
    assert setting == "", f"app.tenant_id not cleared after exception; got {setting!r}"


@pytest.mark.integration
@_SKIP_NO_DB
async def test_reset_uses_finally_block() -> None:
    """Static inspection: reset_tenant_context must be called inside a finally block.

    Reads app/db/rls.py (get_tenant_db_session) and app/dependencies.py
    (require_tenant_admin) and asserts the reset call is within a finally clause.
    """
    import ast
    import pathlib

    repo_root = pathlib.Path(__file__).parents[3]

    def _has_finally_reset(source: str, func_name: str) -> bool:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name != func_name:
                    continue
                for child in ast.walk(node):
                    if isinstance(child, ast.Try):
                        for handler in child.finalbody:
                            src = ast.unparse(handler)
                            if "reset_tenant_context" in src:
                                return True
        return False

    rls_source = (repo_root / "backend/app/db/rls.py").read_text()
    assert _has_finally_reset(
        rls_source, "get_tenant_db_session"
    ), "rls.py get_tenant_db_session: reset_tenant_context not in finally block"

    deps_source = (repo_root / "backend/app/dependencies.py").read_text()
    assert _has_finally_reset(
        deps_source, "require_tenant_admin"
    ), "dependencies.py require_tenant_admin: reset_tenant_context not in finally block"
