# Research: Public Tenant Website

**Feature**: 019-public-tenant-site | **Date**: 2026-05-29

## Decision 1: HTML Rendering — Jinja2 vs React

**Decision**: Jinja2 server-rendered HTML via FastAPI `TemplateResponse`

**Rationale**: Zero additional containers, zero JS build step, instant availability within the existing api service, works with `aiofiles` for static assets. FastAPI's `Jinja2Templates` class integrates natively. The page is read-only and SEO-friendly with SSR.

**Alternatives considered**:
- React SPA — requires a build step, separate static hosting or another service, longer implementation time. Deferred to P3 if ever needed.

---

## Decision 2: TenantConfig — New Table vs Fields on Tenant

**Decision**: New `tenant_configs` table (one-to-one with `tenants`, optional row)

**Rationale**: The `Tenant` model already has a clean schema. Branding/contact config is logically separate (it can be absent, updated independently, and owned by the tenant admin). A separate table avoids widening the `tenants` table with nullable columns that belong to a different domain. The public site gracefully handles a missing config row using fallback defaults.

**Alternatives considered**:
- Inline fields on `Tenant` — simpler, fewer joins, but widens a shared table with optional presentation concerns. Rejected to keep `Tenant` focused on identity/status.
- JSONB config blob on `Tenant` — flexible but untyped. Rejected; Pydantic schemas require typed fields.

---

## Decision 3: Parallel vs Sequential DB Queries

**Decision**: Use `asyncio.gather` to fetch `tenant_config`, `cms_pages`, and `widget` in parallel after the initial tenant-by-slug lookup.

**Rationale**: The three secondary lookups are independent. Running them in parallel halves the DB round-trips from 4 to 2 (slug lookup first, then the 3 in parallel). Fits constitution Principle IV.

**Alternatives considered**:
- Sequential queries — simpler code but unnecessary latency. Rejected.

---

## Decision 4: RLS Context on Public Route

**Decision**: Do NOT set `app.tenant_id` RLS context on public site routes.

**Rationale**: The public site repository uses explicit `WHERE tenant_id = tenant.id` filters on every query — this is the primary isolation layer per constitution Principle I. RLS is defense-in-depth for authenticated routes where a dependency sets the context. For a fully unauthenticated public route, setting RLS would require knowing the tenant UUID before the session is opened, creating a chicken-and-egg problem. Explicit repository filters are sufficient and auditable.

**Alternatives considered**:
- Set RLS after tenant lookup — technically possible but adds complexity for no gain since the repository already owns isolation.

---

## Decision 5: Slug Validation

**Decision**: Validate `tenant_slug` path parameter as `^[a-z0-9-]{1,100}$` using a Pydantic `Path` annotation.

**Rationale**: Prevents path traversal, SQL injection via slug string, and unnecessarily long inputs before any DB query. Matches the `slug` column's existing `String(100)` definition.

**Alternatives considered**:
- No validation — would allow arbitrary strings to hit the DB query. Rejected for security hygiene.

---

## Decision 6: cms_repository and widget_repository stubs

**Decision**: Implement both stubs as part of this feature since the public site depends on them.

**Rationale**: Both files currently contain only `# TODO: implement`. The public site needs `list_published_cms_pages(tenant_id)` and `get_widget_by_tenant(tenant_id)`. Rather than building a full CMS/widget CRUD in these repos, add only the read methods needed for the public site and leave mutation methods for the CMS admin routes (owned by the respective specs).

**Alternatives considered**:
- Inline SQL in public_site_repository — violates constitution layering. Rejected.

---

## Resolved Unknowns

| Unknown | Resolution |
|---------|-----------|
| Does `jinja2` need to be added to pyproject.toml? | Yes — `jinja2>=3.1` must be added; FastAPI lists it optional |
| Does `aiofiles` need to be added? | Yes — required for FastAPI static file serving with Jinja2 |
| CMS body field name | `body` (not `content`) — confirmed from `cms.py` model |
| Widget public field name | `public_widget_id` — confirmed from `widget.py` model |
| Existing `/sites` route? | None — prefix is free to register |
| Existing `tenant_config` model? | Does not exist — must create |
| `cms_repository` status | Stub (one-line TODO) — implement here |
| `widget_repository` status | Stub (one-line TODO) — implement here |
