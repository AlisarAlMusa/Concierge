# API Contracts: Platform Tenant Management

All routes require `Authorization: Bearer <token>` with `role=tenant_manager`.  
Non-`tenant_manager` → 403. Unauthenticated → 401.

---

## POST /platform/tenants

Create a new tenant.

**Request body**
```json
{ "name": "Acme Corp", "slug": "acme-corp" }
```

**Slug rules**: lowercase alphanumeric + hyphens only, 2–100 chars, starts/ends with alphanumeric.

**Responses**
| Status | Body | Condition |
|---|---|---|
| 201 | `TenantRead` | Created |
| 409 | `{"detail": "Slug already exists"}` | Duplicate slug |
| 422 | Pydantic validation error | Invalid slug format |

**Audit event**: `tenant_created`

---

## GET /platform/tenants

List all non-deleted tenants.

**Responses**
| Status | Body |
|---|---|
| 200 | `list[TenantRead]` — `id`, `name`, `slug`, `status`, `created_at`, `updated_at`. No CMS, conversations, leads. |

---

## GET /platform/tenants/{tenant_id}

Get one tenant by ID.

**Responses**
| Status | Body |
|---|---|
| 200 | `TenantRead` |
| 404 | Tenant not found or deleted |

---

## POST /platform/tenants/{tenant_id}/invite-admin

Create a `tenant_admin` user for the tenant.

**Request body**
```json
{ "email": "admin@acme.com" }
```

**Responses**
| Status | Body | Condition |
|---|---|---|
| 201 | `UserRead` (`id`, `email`, `role`, `tenant_id`) | Created |
| 404 | Tenant not found or not active | |
| 409 | Email already registered | |
| 422 | Invalid email | |

**Audit event**: `invite_admin`

---

## POST /platform/tenants/{tenant_id}/suspend

Suspend an active tenant.

**Request body**: empty

**Responses**
| Status | Body | Condition |
|---|---|---|
| 200 | `TenantRead` with `status=suspended` | Suspended (idempotent if already suspended) |
| 404 | Tenant not found | |
| 422 | Tenant is `deleting` or `deleted` | Cannot suspend |

**Audit event**: `tenant_suspended`

**Side effect**: All subsequent `require_tenant_admin` calls for this tenant's users → 403 (`tenant_suspended`).

---

## POST /platform/tenants/{tenant_id}/reactivate

Restore a suspended tenant to active.

**Responses**
| Status | Body | Condition |
|---|---|---|
| 200 | `TenantRead` with `status=active` | Reactivated |
| 404 | Tenant not found | |
| 422 | Tenant is not suspended | Cannot reactivate |

**Audit event**: `tenant_reactivated`

---

## DELETE /platform/tenants/{tenant_id}

Trigger tenant deletion and data erasure.

**Responses**
| Status | Body | Condition |
|---|---|---|
| 202 | `{"status": "deleting", "tenant_id": "..."}` | Accepted, erasure running async |
| 404 | Tenant not found | |
| 409 | Already `deleting` or `deleted` | Idempotent 200 or 409 |

**Audit event**: `tenant_delete_triggered`

**Side effect**: `ErasureService.purge_tenant(tenant_id)` fires as a background task (spec 015).

---

## GET /platform/tenants/{tenant_id}/usage-summary

Aggregate cost/token metrics for a tenant. No conversation content returned.

**Responses**
```json
{
  "tenant_id": "uuid",
  "total_input_tokens": 120000,
  "total_output_tokens": 45000,
  "total_cost_usd": "0.183400"
}
```

| Status | Condition |
|---|---|
| 200 | OK (returns zeros if no cost events) |
| 404 | Tenant not found |

---

## GET /platform/audit-logs

Paginated audit log. Restricted to `tenant_manager`.

**Query params**: `limit` (default 50, max 200), `offset` (default 0), `tenant_id` (optional filter)

**Response**
```json
[
  {
    "id": "uuid",
    "actor_user_id": "uuid | null",
    "actor_role": "tenant_manager",
    "tenant_id": "uuid | null",
    "action": "tenant_created",
    "target_type": "tenant",
    "target_id": "uuid-string",
    "metadata_": {},
    "created_at": "2026-05-28T10:00:00Z"
  }
]
```

---

## Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `permission_denied` | 403 | Wrong role |
| `tenant_suspended` | 403 | Tenant is suspended |
| `not_found` | 404 | Resource missing or deleted |
| `conflict` | 409 | Duplicate slug or email |
| `tenant_not_active` | 422 | Operation requires active tenant |
