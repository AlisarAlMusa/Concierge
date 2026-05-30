"""WidgetService — public-widget-id lookups, origin validation, and
tenant-admin widget lifecycle (Spec 011).

Public-runtime responsibilities (unchanged):

1. ``get_by_public_id`` — resolve the host-site's ``public_widget_id`` into
   the internal ``Widget`` row whose ``tenant_id`` will be baked into the
   session token. This is the *only* DB read that happens before the
   request has an authenticated tenant context, so it bypasses
   ``get_rls_session`` and uses a plain session — the widgets RLS policy
   is intentionally relaxed to permit reads when ``app.tenant_id`` is
   unset.

2. ``validate_origin`` — pure check that the host site's ``Origin`` header
   is in ``Widget.allowed_origins``. Spec 011 FR-003 / FR-004 require this
   server-side; CORS + CSP frame-ancestors are layered on top for
   defense-in-depth.

Tenant-admin lifecycle responsibilities (new):

3. ``create_widget`` / ``update_widget`` / ``delete_widget`` — back the
   ``POST /widgets/``, ``PATCH /widgets/{id}``, ``DELETE /widgets/{id}``
   admin routes. ``tenant_id`` is taken from the authenticated user
   (never from the request body); ``public_widget_id`` is generated
   server-side so admins can't pick a colliding or predictable one.

All raw SQL construction and ``session.execute`` calls live in
``app.repositories.widget_repository``; this service contains only
business logic (id generation, conflict retries, validation, logging).

Owner: Person B.
"""

from __future__ import annotations

import secrets
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.widget import Widget
from app.repositories import widget_repository

logger = structlog.get_logger(__name__)

# Generated id shape: ``pub_wid_`` + 16-char base64url ≈ 12 bytes of entropy.
# Collision odds at 10 000 widgets are ~1e-19; we still retry on the
# uniqueness check for safety.
_PUBLIC_ID_PREFIX = "pub_wid_"
_PUBLIC_ID_NBYTES = 12
_PUBLIC_ID_MAX_RETRIES = 5


class WidgetService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def get_by_public_id(self, public_widget_id: str) -> Widget | None:
        """Return the enabled widget for a public id, or ``None``.

        Disabled widgets are treated as nonexistent — the session endpoint
        must not mint tokens for them. RLS on ``widgets`` is relaxed to
        allow this pre-auth read; the lookup is constrained to a non-secret
        public identifier so this is safe.
        """
        if not public_widget_id:
            return None
        return await widget_repository.get_by_public_id(
            self._session, public_widget_id=public_widget_id
        )

    async def get_by_id(self, widget_id: UUID, *, tenant_id: UUID) -> Widget | None:
        """Return the enabled widget for ``widget_id`` scoped to ``tenant_id``.

        Used by ``GET /public/widgets/config`` after the request has a
        verified widget JWT — the route reads both ``widget_id`` and
        ``tenant_id`` from the token. The explicit ``WHERE tenant_id = $1``
        runs in addition to the RLS policy the caller's session sets so
        cross-tenant lookup is impossible even if RLS were bypassed in
        some future test setup (defense in depth, per ``docs/SPEC.md``).
        Disabled widgets return ``None`` so the route can surface a clean
        404 rather than handing back stale config.
        """
        return await widget_repository.get_for_tenant(
            self._session, tenant_id=tenant_id, widget_id=widget_id
        )

    async def list_by_tenant(self, tenant_id: UUID) -> list[Widget]:
        """Return all widgets (enabled or not) for a tenant admin listing."""
        return await widget_repository.list_for_tenant(
            self._session, tenant_id=tenant_id
        )

    @staticmethod
    def validate_origin(widget: Widget, origin: str | None) -> bool:
        """``True`` iff ``origin`` is in the widget's allowed list.

        Exact-match comparison only. No prefix matching, no wildcard
        expansion — Spec 011 FR-003 mandates a precise allowlist.
        """
        if not origin:
            return False
        return origin in (widget.allowed_origins or [])

    # ──────────────────────────────────────────────────────────────────
    # Tenant-admin lifecycle (POST / PATCH / DELETE)
    # ──────────────────────────────────────────────────────────────────

    async def create_widget(
        self,
        *,
        tenant_id: UUID,
        name: str,
        allowed_origins: list[str],
        greeting: str = "",
        theme: dict | None = None,
        enabled: bool = True,
    ) -> Widget:
        """Insert a new widget for ``tenant_id``.

        ``public_widget_id`` is generated server-side; the admin never
        chooses it. Collisions on the unique constraint are retried up
        to ``_PUBLIC_ID_MAX_RETRIES`` times — the entropy budget makes a
        collision astronomically unlikely, but the retry keeps the
        service correct even under contrived seed-script reseeding.
        Origin shape was already validated by the Pydantic schema.
        """
        if not name or not name.strip():
            raise ValueError("create_widget: name must be non-empty")

        public_widget_id = ""
        for _ in range(_PUBLIC_ID_MAX_RETRIES):
            candidate = _PUBLIC_ID_PREFIX + secrets.token_urlsafe(_PUBLIC_ID_NBYTES)
            if not await widget_repository.public_id_exists(
                self._session, public_widget_id=candidate
            ):
                public_widget_id = candidate
                break
        if not public_widget_id:
            raise RuntimeError(
                "create_widget: failed to generate a unique public_widget_id "
                f"after {_PUBLIC_ID_MAX_RETRIES} attempts"
            )

        widget = Widget(
            id=uuid4(),
            tenant_id=tenant_id,
            public_widget_id=public_widget_id,
            name=name.strip(),
            allowed_origins=list(allowed_origins),
            theme=dict(theme or {}),
            greeting=greeting,
            enabled=enabled,
        )
        await widget_repository.add(self._session, widget)
        await widget_repository.flush_pending(self._session)
        # Re-fetch server defaults (``created_at`` / ``updated_at``) so the
        # route can serialise the ORM row synchronously without triggering
        # a lazy load — same MissingGreenlet guard CmsPageService uses.
        await self._session.refresh(widget)
        logger.info(
            "widget.created",
            tenant_id=str(tenant_id),
            widget_id=str(widget.id),
            public_widget_id=widget.public_widget_id,
            allowed_origins_count=len(widget.allowed_origins or []),
            enabled=widget.enabled,
        )
        return widget

    async def update_widget(
        self,
        *,
        widget_id: UUID,
        tenant_id: UUID,
        name: str | None = None,
        allowed_origins: list[str] | None = None,
        greeting: str | None = None,
        theme: dict | None = None,
        enabled: bool | None = None,
    ) -> Widget | None:
        """Partial update. Returns ``None`` if the row doesn't exist
        for this tenant (cross-tenant lookup or 404).

        Only the keyword arguments the caller passes are touched; every
        other field stays untouched. ``tenant_id`` is enforced both
        on the lookup (defense in depth) and never accepted from
        the request body.
        """
        widget = await widget_repository.get_for_tenant_admin(
            self._session, tenant_id=tenant_id, widget_id=widget_id
        )
        if widget is None:
            return None

        if name is not None:
            stripped = name.strip()
            if not stripped:
                raise ValueError("update_widget: name must be non-empty")
            widget.name = stripped
        if allowed_origins is not None:
            widget.allowed_origins = list(allowed_origins)
        if greeting is not None:
            widget.greeting = greeting
        if theme is not None:
            widget.theme = dict(theme)
        if enabled is not None:
            widget.enabled = enabled

        await widget_repository.flush_pending(self._session)
        # Same MissingGreenlet guard as the CMS path — ``updated_at`` has
        # ``onupdate=func.now()`` and the route reads it synchronously.
        await self._session.refresh(widget)
        logger.info(
            "widget.updated",
            tenant_id=str(tenant_id),
            widget_id=str(widget.id),
            updated_fields=[
                k
                for k, v in {
                    "name": name,
                    "allowed_origins": allowed_origins,
                    "greeting": greeting,
                    "theme": theme,
                    "enabled": enabled,
                }.items()
                if v is not None
            ],
        )
        return widget

    async def delete_widget(self, *, widget_id: UUID, tenant_id: UUID) -> bool:
        """Hard-delete a widget. ``True`` if removed, ``False`` if absent.

        Cascades nothing — ``widgets`` has no FK references pointing at
        it. The session tokens minted from this widget remain valid for
        their TTL (≤ 15 minutes); refusing to issue new tokens is the
        immediate effect.
        """
        widget = await widget_repository.get_for_tenant_admin(
            self._session, tenant_id=tenant_id, widget_id=widget_id
        )
        if widget is None:
            return False
        await widget_repository.remove(self._session, widget)
        await widget_repository.flush_pending(self._session)
        logger.info(
            "widget.deleted",
            tenant_id=str(tenant_id),
            widget_id=str(widget_id),
            public_widget_id=widget.public_widget_id,
        )
        return True
