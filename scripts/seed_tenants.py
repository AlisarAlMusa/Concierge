"""Seed two demo tenants for local development and demo.

Usage:
    cd backend
    DATABASE_URL=postgresql+asyncpg://concierge:concierge@localhost:5432/concierge \
        uv run python ../scripts/seed_tenants.py
"""

import asyncio
import os
import sys
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.tenant import Tenant, TenantStatus  # noqa: E402

TENANTS = [
    {"name": "Acme Corp", "slug": "acme-corp"},
    {"name": "Globex Industries", "slug": "globex-industries"},
]

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://concierge:concierge@localhost:5432/concierge",
)


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session: AsyncSession
        for data in TENANTS:
            existing = await session.scalar(
                select(Tenant).where(Tenant.slug == data["slug"])
            )
            if existing:
                print(f"  skip: {data['slug']} already exists (id={existing.id})")
                continue

            tenant = Tenant(id=uuid4(), status=TenantStatus.active, **data)
            session.add(tenant)
            await session.flush()
            print(f"  created: {data['slug']} (id={tenant.id})")

        await session.commit()

    await engine.dispose()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
