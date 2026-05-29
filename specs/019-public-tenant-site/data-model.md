# Data Model: Public Tenant Website

**Feature**: 019-public-tenant-site | **Date**: 2026-05-29

## New Table: tenant_configs

One-to-one with `tenants`. Row is optional — public site falls back to defaults if absent.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `tenant_id` | UUID (FK → tenants.id, CASCADE DELETE) | No | Primary key |
| `brand_name` | VARCHAR(255) | Yes | Display name override; falls back to `tenants.name` |
| `theme_color` | VARCHAR(7) | Yes | Hex color e.g. `#111827`; fallback `#1f2937` |
| `greeting` | TEXT | Yes | Widget greeting message |
| `public_description` | TEXT | Yes | Shown on public site below brand name |
| `contact_email` | VARCHAR(255) | Yes | Shown on public site if present |
| `allowed_origins` | TEXT[] | Yes | Widget CORS origins — NOT exposed on public page |
| `created_at` | TIMESTAMPTZ | No | `server_default=now()` |
| `updated_at` | TIMESTAMPTZ | No | `onupdate=now()` |

**RLS**: Enable RLS on `tenant_configs` with policy `current_setting('app.tenant_id')::uuid = tenant_id`. Public site reads bypass RLS via explicit filter (see research Decision 4).

**Migration**: `0006_tenant_config.py`

---

## Existing Tables Read (no schema changes)

### tenants (read-only)

| Column | Used by public site? | Notes |
|--------|---------------------|-------|
| `id` | Yes | Used to join config/pages/widget |
| `name` | Yes | Fallback display name |
| `slug` | Yes | Primary resolution key |
| `status` | Yes | Must be `active`; `suspended` → 403 |

### cms_pages (read-only, published only)

| Column | Used by public site? | Notes |
|--------|---------------------|-------|
| `id` | No | Not needed on rendered page |
| `tenant_id` | Yes | Explicit filter |
| `title` | Yes | Section heading |
| `slug` | No | Not shown on public page |
| `body` | Yes | Section content (field is `body`, not `content`) |
| `status` | Yes | Filter: `status = 'published'` only |

**Index used**: `ix_cms_pages_tenant_status` on `(tenant_id, status)` — already exists.

### widgets (read-only)

| Column | Used by public site? | Notes |
|--------|---------------------|-------|
| `tenant_id` | Yes | Explicit filter |
| `public_widget_id` | Yes | Injected into `data-widget-id` on script tag |

---

## State Transitions

**CmsPage.status** (existing):
```
draft → published → archived
         ↑
   only published appears on public site
```

**Tenant.status** (existing):
```
active → suspended → deleted
  ↑          ↓
  |      public site → 403
  └── public site → render page
```

---

## Pydantic Schemas (public_site.py)

```python
class PublicCmsSection(BaseModel):
    title: str
    body: str

class PublicTenantConfig(BaseModel):
    brand_name: str           # from TenantConfig.brand_name or Tenant.name
    theme_color: str          # from TenantConfig.theme_color or "#1f2937"
    greeting: str | None
    public_description: str | None
    contact_email: str | None

class PublicWidgetInfo(BaseModel):
    widget_id: str            # public_widget_id from Widget table

class PublicSiteContext(BaseModel):
    tenant_name: str
    config: PublicTenantConfig
    pages: list[PublicCmsSection]
    widget: PublicWidgetInfo | None   # None if no widget configured
```

These are the template context DTOs. The Jinja2 template receives a `PublicSiteContext` instance. They are also the JSON response body for the optional `GET /api/public/sites/{slug}` endpoint.
