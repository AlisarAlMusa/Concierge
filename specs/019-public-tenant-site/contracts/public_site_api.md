# API Contract: Public Tenant Website

**Feature**: 019-public-tenant-site | **Date**: 2026-05-29

---

## GET /sites/{tenant_slug}

**Purpose**: Render the public-facing tenant website as an HTML page.

**Auth**: None — fully public endpoint.

**Path parameters**:

| Param | Type | Validation | Notes |
|-------|------|------------|-------|
| `tenant_slug` | string | `^[a-z0-9-]{1,100}$` | Resolved server-side; never trusted as `tenant_id` |

**Success response**: `200 OK` — `Content-Type: text/html`

HTML page containing:
- Tenant brand name and public description
- Published CMS content sections (title + body each)
- Contact email (if present in config)
- `<script src="/widget.js" data-widget-id="{public_widget_id}">` (if widget configured)

**Error responses**:

| Status | Condition |
|--------|-----------|
| `400 Bad Request` | `tenant_slug` fails regex validation |
| `403 Forbidden` | Tenant exists but `status = suspended` |
| `404 Not Found` | No tenant matches the slug |

**Security rules**:
- `tenant_id` MUST NOT appear in request body or query params
- Draft and archived CMS pages MUST NOT appear in response
- Leads, conversations, guardrail config, cost data MUST NOT appear in response

---

## GET /api/public/sites/{tenant_slug} *(P3 — optional)*

**Purpose**: Return public tenant site data as JSON for React/external integration.

**Auth**: None — fully public endpoint.

**Path parameters**: Same as above.

**Success response**: `200 OK` — `Content-Type: application/json`

```json
{
  "tenant": {
    "name": "ABC Gym",
    "slug": "abc-gym"
  },
  "config": {
    "brand_name": "ABC Gym",
    "theme_color": "#111827",
    "greeting": "Hi! How can we help you?",
    "public_description": "A modern fitness center in Beirut.",
    "contact_email": "hello@abcgym.com"
  },
  "pages": [
    {
      "title": "Opening Hours",
      "body": "We open Monday to Saturday from 8 AM to 10 PM."
    }
  ],
  "widget": {
    "widget_id": "pub_wid_abc123"
  }
}
```

`"widget"` field is `null` if no widget is configured for the tenant.

**Error responses**: Same as HTML endpoint.

---

## Router registration (api/router.py addition)

```python
from app.api.routes import public_site
api_router.include_router(public_site.router, prefix="/sites", tags=["public_site"])
```
