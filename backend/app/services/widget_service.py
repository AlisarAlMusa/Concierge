"""WidgetService — public-widget-id lookups and origin validation (Spec 011).

Two responsibilities:

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

All raw SQL construction and ``session.execute`` calls live in
``app.repositories.widget_repository``; this service contains only the
pre-auth lookup contract and the pure origin-validation helper.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.widget import Widget
from app.repositories import widget_repository


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
