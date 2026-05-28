#!/usr/bin/env python
"""Seed script: create the first tenant_manager and demo tenant_admin accounts.

Run once after `alembic upgrade head`:
    uv run python scripts/seed_tenants.py

Idempotent — skips inserts if the email already exists.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

# Allow running from repo root or backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main() -> None:
    import structlog
    from fastapi_users.password import PasswordHelper

    from app.core.config import get_settings
    from app.db.session import close_engine, get_engine, get_session_factory
    from app.models.tenant import Tenant, TenantStatus
    from app.models.user import User, UserRole

    log = structlog.get_logger("seed")
    get_settings()

    get_engine()
    factory = get_session_factory()

    password_helper = PasswordHelper()

    async with factory() as session:
        from sqlalchemy import select

        # ── Create demo tenant ───────────────────────────────────────────────
        result = await session.execute(select(Tenant).where(Tenant.slug == "demo-tenant"))
        tenant = result.scalar_one_or_none()

        if tenant is None:
            tenant = Tenant(
                id=uuid.uuid4(),
                name="Demo Tenant",
                slug="demo-tenant",
                status=TenantStatus.active,
            )
            session.add(tenant)
            await session.flush()
            log.info("seed.tenant_created", slug="demo-tenant", id=str(tenant.id))
        else:
            log.info("seed.tenant_exists", slug="demo-tenant", id=str(tenant.id))

        # ── Create tenant_manager ────────────────────────────────────────────
        MANAGER_EMAIL = "manager@concierge.internal"
        MANAGER_PASSWORD = "Manager!2026"

        result = await session.execute(select(User).where(User.email == MANAGER_EMAIL))
        manager = result.scalar_one_or_none()

        if manager is None:
            manager = User(
                id=uuid.uuid4(),
                email=MANAGER_EMAIL,
                hashed_password=password_helper.hash(MANAGER_PASSWORD),
                is_active=True,
                is_superuser=False,
                is_verified=True,
                role=UserRole.tenant_manager,
                tenant_id=None,  # tenant_manager has no tenant affiliation
            )
            session.add(manager)
            log.info("seed.manager_created", email=MANAGER_EMAIL)
        else:
            log.info("seed.manager_exists", email=MANAGER_EMAIL)

        # ── Create demo tenant_admin ─────────────────────────────────────────
        ADMIN_EMAIL = "admin@demo-tenant.local"
        ADMIN_PASSWORD = "Admin!2026"

        result = await session.execute(select(User).where(User.email == ADMIN_EMAIL))
        admin = result.scalar_one_or_none()

        if admin is None:
            admin = User(
                id=uuid.uuid4(),
                email=ADMIN_EMAIL,
                hashed_password=password_helper.hash(ADMIN_PASSWORD),
                is_active=True,
                is_superuser=False,
                is_verified=True,
                role=UserRole.tenant_admin,
                tenant_id=tenant.id,
            )
            session.add(admin)
            log.info("seed.admin_created", email=ADMIN_EMAIL, tenant_id=str(tenant.id))
        else:
            log.info("seed.admin_exists", email=ADMIN_EMAIL)

        await session.commit()

    await close_engine()

    print(
        f"\n✅ Seed complete\n"
        f"   Tenant manager : {MANAGER_EMAIL}  /  {MANAGER_PASSWORD}\n"
        f"   Tenant admin   : {ADMIN_EMAIL}  /  {ADMIN_PASSWORD}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
