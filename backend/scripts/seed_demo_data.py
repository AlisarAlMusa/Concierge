"""Deterministic demo seed for the RAG + widget end-to-end flow.

After this script finishes, the following loop works against the local
stack:

    1. POST /widgets/session  → JWT
    2. POST /chat              → router/agent path, RAG hits seeded chunks

The seed also writes ``CmsPage`` rows for every demo page so the corpus
is reachable through ``GET /cms/pages`` exactly the same way pages
uploaded via ``POST /cms/pages`` are.

Idempotency invariants:

* Tenant and Widget are looked up by their natural keys (``slug`` and
  ``public_widget_id``) before insert; existing rows are reused / updated.
* CMS pages are written through ``CmsPageService.create_page`` which
  upserts by ``(tenant_id, slug)`` and itself routes the body through
  ``RagService.index_page``. Re-running the seed produces the same final
  state for both ``cms_pages`` and ``cms_chunks``.
* UUIDs are generated with ``uuid5(NAMESPACE_DNS, ...)`` so the
  identifiers are stable across runs — useful for docs and for clients
  that hard-code the demo tenant id in tests.

Architecture rules:

* This script uses **only** existing services: ``CmsPageService`` (which
  in turn drives ``RagService.index_page`` → ``chunk_page`` →
  ``CohereEmbeddingClient``). No bypassed pipeline, no raw vector math,
  no direct ``cms_chunks`` INSERTs.
* RLS is honored: the session sets ``app.tenant_id`` before any write to
  a tenant-scoped table (``widgets``, ``cms_pages``, ``cms_chunks``).
* The script is the only caller — it owns the transaction and explicitly
  commits at the end.

Owner: Person B.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.rls import reset_tenant_context, set_tenant_context
from app.db.session import close_engine, get_session_factory
from app.models.cms import CmsPageStatus
from app.models.tenant import Tenant, TenantStatus
from app.models.widget import Widget
from app.services.cms_page_service import CmsPageService
from app.services.embedding_client import CohereEmbeddingClient
from app.services.rag_service import RagService

# Deterministic identifiers — stable across runs and machines.
TENANT_SLUG = "demo"
TENANT_NAME = "Demo Tenant"
TENANT_ID: UUID = uuid5(NAMESPACE_DNS, "demo.concierge.local")

WIDGET_PUBLIC_ID = "demo-widget-001"
WIDGET_NAME = "Demo Site Widget"
WIDGET_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8501",
]
WIDGET_GREETING = "Hi! I'm the Demo concierge — ask me about pricing, refunds, or shipping."
WIDGET_THEME = {
    "accent_color": "#4f46e5",
    "position": "bottom-right",
    "font_family": "Inter, system-ui, sans-serif",
}


# ----- Demo corpus ----------------------------------------------------------
@dataclass(frozen=True)
class SeedPage:
    slug: str
    title: str
    content: str


DEMO_PAGES: tuple[SeedPage, ...] = (
    SeedPage(
        slug="pricing",
        title="Pricing",
        content=(
            "Demo Co offers three plans: Starter, Growth, and Enterprise.\n\n"
            "The Starter plan is $19 per month and includes up to 1,000 chat "
            "conversations, one custom widget, and email support. It is the "
            "right choice for solo founders and very small teams that want to "
            "validate the AI concierge on their site.\n\n"
            "The Growth plan is $79 per month and includes up to 10,000 chat "
            "conversations, three custom widgets, lead-scoring, priority email "
            "support, and weekly usage reports. Most teams upgrade to Growth "
            "once they're past the first hundred captured leads.\n\n"
            "The Enterprise plan is custom-priced and includes unlimited "
            "conversations, dedicated infrastructure, a single-tenant Postgres "
            "instance, SSO/SAML, a contractual SLA of 99.9% uptime, and a "
            "named customer success engineer. Enterprise contracts start at "
            "$2,500 per month billed annually.\n\n"
            "All plans include a 14-day free trial with no credit card required. "
            "We do not bill per-message and we do not charge overage fees on "
            "Starter or Growth — if you exceed your monthly conversations we'll "
            "send a friendly upgrade nudge in your weekly report."
        ),
    ),
    SeedPage(
        slug="refund-policy",
        title="Refund policy",
        content=(
            "We offer a no-questions-asked refund within the first 30 days of "
            "any paid plan. Email support@demo-co.example with your account "
            "email and we will issue a full refund to the original payment "
            "method within five business days.\n\n"
            "After the initial 30-day window, we issue prorated refunds for "
            "the unused portion of the current billing period, calculated to "
            "the day. Annual Enterprise contracts are refundable in the first "
            "60 days only.\n\n"
            "Refunds for usage-based add-ons (custom domains, dedicated "
            "infrastructure) are handled case-by-case — please contact your "
            "customer success engineer."
        ),
    ),
    SeedPage(
        slug="shipping",
        title="Shipping and delivery",
        content=(
            "Demo Co is a SaaS product — there is no physical shipping for our "
            "subscriptions. The chat widget is delivered as a single JavaScript "
            "snippet you paste into your site's <head> tag. Once you complete "
            "checkout, the snippet is available immediately in your admin "
            "dashboard under Settings → Widgets.\n\n"
            "Physical swag (t-shirts, stickers) for our annual conference "
            "attendees ships from our fulfillment partner in Portland, Oregon. "
            "Domestic US orders typically arrive within 5–7 business days. "
            "International orders take 10–15 business days and may incur "
            "customs fees that are the recipient's responsibility."
        ),
    ),
    SeedPage(
        slug="support-hours",
        title="Support hours and channels",
        content=(
            "Email support is available 24/7 at support@demo-co.example. We "
            "guarantee a first response within 24 hours on the Starter plan, "
            "within 8 hours on Growth, and within 1 hour on Enterprise "
            "(business hours, Mon–Fri 9am–6pm Pacific Time).\n\n"
            "Live chat support is staffed Mon–Fri 9am–6pm Pacific Time and is "
            "available to Growth and Enterprise customers via the in-app chat "
            "in your admin dashboard. After hours, the chat falls back to "
            "email and we follow up the next business day.\n\n"
            "Enterprise customers also have access to a dedicated Slack "
            "Connect channel and a 24x7 emergency phone line for incidents "
            "that block production traffic. The phone line is intended for "
            "service-down emergencies only; general support questions should "
            "stay in email or the Slack channel."
        ),
    ),
    SeedPage(
        slug="product-overview",
        title="Product overview",
        content=(
            "Demo Co is an AI concierge for websites. It combines a "
            "router-and-bounded-agent architecture with retrieval-augmented "
            "generation over your own content: you publish FAQ, sales, and "
            "policy pages through our admin, and the agent answers visitor "
            "questions using only those pages as ground truth.\n\n"
            "Core features include: a JavaScript widget you embed on any site; "
            "a per-tenant content management system; deterministic intent "
            "routing to FAQ, sales, human, or agent paths; lead capture with "
            "configurable rate limiting; human-escalation with one-click "
            "handoff to your inbox; and a multi-tenant admin dashboard with "
            "conversation replay and per-tenant analytics.\n\n"
            "Out of scope at launch: outbound proactive messaging, voice or "
            "video chat, and integrations with third-party CRMs (planned for "
            "the Q4 release)."
        ),
    ),
)


# ----- Persistence helpers --------------------------------------------------
async def upsert_tenant(session: AsyncSession) -> Tenant:
    """Get-or-insert the demo Tenant. Looked up by ``slug``."""
    stmt = select(Tenant).where(Tenant.slug == TENANT_SLUG)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        log.info("tenant.found id=%s slug=%s", existing.id, existing.slug)
        return existing

    tenant = Tenant(
        id=TENANT_ID,
        name=TENANT_NAME,
        slug=TENANT_SLUG,
        status=TenantStatus.active,
    )
    session.add(tenant)
    await session.flush()
    log.info("tenant.created id=%s slug=%s", tenant.id, tenant.slug)
    return tenant


async def upsert_widget(session: AsyncSession, tenant_id: UUID) -> Widget:
    """Get-or-insert the demo Widget. Looked up by ``public_widget_id``.

    On hit, the mutable demo fields (allowed_origins, theme, greeting,
    enabled) are refreshed so the seed remains the source of truth even
    if a previous run wrote slightly different values.
    """
    stmt = select(Widget).where(Widget.public_widget_id == WIDGET_PUBLIC_ID)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.tenant_id = tenant_id
        existing.name = WIDGET_NAME
        existing.allowed_origins = list(WIDGET_ALLOWED_ORIGINS)
        existing.greeting = WIDGET_GREETING
        existing.theme = dict(WIDGET_THEME)
        existing.enabled = True
        await session.flush()
        log.info(
            "widget.refreshed id=%s public_widget_id=%s",
            existing.id,
            existing.public_widget_id,
        )
        return existing

    widget = Widget(
        tenant_id=tenant_id,
        public_widget_id=WIDGET_PUBLIC_ID,
        name=WIDGET_NAME,
        allowed_origins=list(WIDGET_ALLOWED_ORIGINS),
        greeting=WIDGET_GREETING,
        theme=dict(WIDGET_THEME),
        enabled=True,
    )
    session.add(widget)
    await session.flush()
    log.info(
        "widget.created id=%s public_widget_id=%s",
        widget.id,
        widget.public_widget_id,
    )
    return widget


# ----- Main -----------------------------------------------------------------
log = logging.getLogger("seed_demo_data")


async def seed() -> None:
    settings = get_settings()
    if not settings.COHERE_API_KEY:
        raise SystemExit(
            "COHERE_API_KEY is not set. Add it to .env (the same key the API "
            "container uses) before running the seed script."
        )

    embedding = CohereEmbeddingClient.from_api_key(
        api_key=settings.COHERE_API_KEY,
        model=settings.EMBEDDING_MODEL,
    )

    factory = get_session_factory()
    total_chunks = 0
    try:
        async with factory() as session:
            tenant = await upsert_tenant(session)

            # Tenant-scoped writes from here on. The ``widgets`` RLS policy
            # is relaxed when ``app.tenant_id`` is unset (token-mint reads),
            # but WITH CHECK still requires it to match on INSERT/UPDATE;
            # the ``cms_chunks`` policy requires it on every write.
            await set_tenant_context(session, tenant.id)

            widget = await upsert_widget(session, tenant.id)

            rag = RagService(session=session, embedding_client=embedding)
            cms = CmsPageService(session=session, rag_service=rag)
            for page in DEMO_PAGES:
                result = await cms.create_page(
                    tenant_id=tenant.id,
                    title=page.title,
                    slug=page.slug,
                    body=page.content,
                    status=CmsPageStatus.published,
                )
                total_chunks += result.chunks_written
                log.info(
                    "page.indexed title=%r page_id=%s slug=%s chunks=%d",
                    page.title,
                    result.page.id,
                    result.page.slug,
                    result.chunks_written,
                )

            await reset_tenant_context(session)
            await session.commit()

            log.info(
                "summary tenant_id=%s widget_public_id=%s pages=%d total_chunks=%d",
                tenant.id,
                widget.public_widget_id,
                len(DEMO_PAGES),
                total_chunks,
            )
            print(
                "\n=== Demo seed complete ===\n"
                f"  tenant_id          : {tenant.id}\n"
                f"  tenant_slug        : {tenant.slug}\n"
                f"  widget_public_id   : {widget.public_widget_id}\n"
                f"  widget_id          : {widget.id}\n"
                f"  allowed_origins    : {', '.join(widget.allowed_origins)}\n"
                f"  pages_seeded       : {len(DEMO_PAGES)}\n"
                f"  embeddings_written : {total_chunks}\n"
                "==========================\n"
            )
    finally:
        await close_engine()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(seed())


if __name__ == "__main__":
    main()
