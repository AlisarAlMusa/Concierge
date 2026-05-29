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


async def _upsert_tenant(session, slug: str, name: str, log) -> "Tenant":
    from sqlalchemy import select

    from app.models.tenant import Tenant, TenantStatus

    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(id=uuid.uuid4(), name=name, slug=slug, status=TenantStatus.active)
        session.add(tenant)
        await session.flush()
        log.info("seed.tenant_created", slug=slug, id=str(tenant.id))
    else:
        log.info("seed.tenant_exists", slug=slug, id=str(tenant.id))
    return tenant


async def _upsert_config(session, tenant_id, brand_name, description, contact_email, log) -> None:
    from sqlalchemy import select

    from app.models.tenant_config import TenantConfig

    result = await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    if result.scalar_one_or_none() is None:
        cfg = TenantConfig(
            tenant_id=tenant_id,
            brand_name=brand_name,
            theme_color="#1f2937",
            public_description=description,
            contact_email=contact_email,
        )
        session.add(cfg)
        log.info("seed.config_created", tenant_id=str(tenant_id))


async def _upsert_cms_page(session, tenant_id, slug, title, body, log) -> None:
    from sqlalchemy import select

    from app.models.cms import CmsPage, CmsPageStatus

    result = await session.execute(
        select(CmsPage).where(CmsPage.tenant_id == tenant_id, CmsPage.slug == slug)
    )
    if result.scalar_one_or_none() is None:
        page = CmsPage(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            title=title,
            slug=slug,
            body=body,
            status=CmsPageStatus.published,
        )
        session.add(page)
        log.info("seed.cms_page_created", tenant_id=str(tenant_id), slug=slug)


async def _upsert_widget(session, tenant_id, public_widget_id, log) -> None:
    from sqlalchemy import select

    from app.models.widget import Widget

    result = await session.execute(
        select(Widget).where(Widget.public_widget_id == public_widget_id)
    )
    if result.scalar_one_or_none() is None:
        widget = Widget(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            public_widget_id=public_widget_id,
            name="default",
            allowed_origins=[],
            theme={},
            greeting="Hi! How can I help you today?",
            enabled=True,
        )
        session.add(widget)
        log.info("seed.widget_created", tenant_id=str(tenant_id), public_widget_id=public_widget_id)


async def _upsert_user(session, email, password, role, tenant_id, password_helper, log) -> None:
    from sqlalchemy import select

    from app.models.user import User

    result = await session.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is None:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=password_helper.hash(password),
            is_active=True,
            is_superuser=False,
            is_verified=True,
            role=role,
            tenant_id=tenant_id,
        )
        session.add(user)
        log.info("seed.user_created", email=email, role=role)
    else:
        log.info("seed.user_exists", email=email)


async def main() -> None:
    import structlog
    from fastapi_users.password import PasswordHelper

    from app.core.config import get_settings
    from app.db.session import close_engine, get_engine, get_session_factory
    from app.models.user import UserRole

    log = structlog.get_logger("seed")
    get_settings()

    get_engine()
    factory = get_session_factory()

    password_helper = PasswordHelper()

    async with factory() as session:
        from sqlalchemy import select

        # ── Tenant 1: ABC Gym ────────────────────────────────────────────────
        abc_gym = await _upsert_tenant(session, "abc-gym", "ABC Gym", log)
        await _upsert_config(
            session,
            abc_gym.id,
            brand_name="ABC Gym",
            description="A modern fitness center in Beirut.",
            contact_email="hello@abcgym.example",
            log=log,
        )
        await _upsert_cms_page(
            session,
            abc_gym.id,
            "opening-hours",
            "Opening Hours",
            "We are open Monday to Saturday from 8 AM to 10 PM.",
            log,
        )
        await _upsert_cms_page(
            session,
            abc_gym.id,
            "membership",
            "Membership Plans",
            "Monthly: $50. Quarterly: $130. Annual: $480.",
            log,
        )
        await _upsert_widget(session, abc_gym.id, "pub_wid_abc_gym_001", log)

        # ── Tenant 2: Green Clinic ───────────────────────────────────────────
        green_clinic = await _upsert_tenant(session, "green-clinic", "Green Clinic", log)
        await _upsert_config(
            session,
            green_clinic.id,
            brand_name="Green Clinic",
            description="Your trusted family health clinic.",
            contact_email="info@greenclinic.example",
            log=log,
        )
        await _upsert_cms_page(
            session,
            green_clinic.id,
            "services",
            "Our Services",
            "General practice, pediatrics, physiotherapy, and nutritional counseling.",
            log,
        )
        await _upsert_cms_page(
            session,
            green_clinic.id,
            "hours",
            "Clinic Hours",
            "Monday to Friday: 9 AM to 6 PM. Saturday: 9 AM to 1 PM.",
            log,
        )
        await _upsert_widget(session, green_clinic.id, "pub_wid_green_clinic_001", log)

        # ── Legacy demo-tenant (backward-compat) ─────────────────────────────
        demo_tenant = await _upsert_tenant(session, "demo-tenant", "Demo Tenant", log)

        # ── Users ────────────────────────────────────────────────────────────
        MANAGER_PASSWORD = "Manager!2026"
        ADMIN_PASSWORD = "Admin!2026"

        await _upsert_user(
            session, "manager@concierge.internal", MANAGER_PASSWORD,
            UserRole.tenant_manager, None, password_helper, log,
        )
        await _upsert_user(
            session, "admin@demo-tenant.com", ADMIN_PASSWORD,
            UserRole.tenant_admin, demo_tenant.id, password_helper, log,
        )
        await _upsert_user(
            session, "admin@abc-gym.com", ADMIN_PASSWORD,
            UserRole.tenant_admin, abc_gym.id, password_helper, log,
        )
        await _upsert_user(
            session, "admin@green-clinic.com", ADMIN_PASSWORD,
            UserRole.tenant_admin, green_clinic.id, password_helper, log,
        )

        await session.commit()

    await close_engine()

    print(
        f"\n✅ Seed complete\n"
        f"\n   Platform manager\n"
        f"     manager@concierge.internal  /  {MANAGER_PASSWORD}\n"
        f"\n   Tenant admins\n"
        f"     admin@abc-gym.com         /  {ADMIN_PASSWORD}  → /sites/abc-gym\n"
        f"     admin@green-clinic.com    /  {ADMIN_PASSWORD}  → /sites/green-clinic\n"
        f"     admin@demo-tenant.com     /  {ADMIN_PASSWORD}  → demo-tenant\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
