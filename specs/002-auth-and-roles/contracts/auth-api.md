# API Contract: Auth & Roles

**Feature**: 002-auth-and-roles | **Date**: 2026-05-27

All responses use `Content-Type: application/json`.  
All protected routes require `Authorization: Bearer <token>`.  
All error responses follow the platform contract: `{"detail": "...", "code": "..."}`.

---

## POST /auth/register

**Auth**: None (public)

**Request**:
```json
{
  "email": "user@example.com",
  "password": "minimum8chars"
}
```

**Response 201**:
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "role": "member",
  "tenant_id": null,
  "is_active": true
}
```

**Errors**:
| Status | `code` | Condition |
|--------|--------|-----------|
| 400 | `validation_error` | Invalid email format or password < 8 chars |
| 409 | `conflict` | Email already registered |

**Notes**: `role` is always `member` regardless of any role field in the request body. `tenant_id` is always `null` for self-registered users.

---

## POST /auth/login

**Auth**: None (public)

**Request**:
```json
{
  "username": "user@example.com",
  "password": "yourpassword"
}
```

*(fastapi-users uses `username` field for OAuth2PasswordRequestForm compatibility)*

**Response 200**:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

**Errors**:
| Status | `code` | Condition |
|--------|--------|-----------|
| 400 | `invalid_credentials` | Wrong email or password |
| 429 | `rate_limit` | > 10 failed attempts from this IP in 15 minutes |

**Headers on 429**: `Retry-After: <seconds>`

**Notes**: Every attempt (success or failure) increments the IP rate-limit counter. Successful login resets the counter.

---

## POST /auth/logout

**Auth**: Bearer token (required)

**Request**: No body

**Response 204**: No content

**Errors**:
| Status | `code` | Condition |
|--------|--------|-----------|
| 401 | `auth_required` | Missing or invalid token |

**Notes**: Stores the token's JTI in Redis with TTL = remaining token lifetime. Subsequent requests with this JTI receive 401.

---

## GET /auth/me

**Auth**: Bearer token (required)

**Response 200**:
```json
{
  "id": "uuid",
  "email": "admin@tenant-a.com",
  "role": "tenant_admin",
  "tenant_id": "uuid-or-null",
  "is_active": true
}
```

**Errors**:
| Status | `code` | Condition |
|--------|--------|-----------|
| 401 | `auth_required` | Missing, expired, or revoked token |

---

## POST /platform/tenants/{tenant_id}/invite-admin

**Auth**: Bearer token — `tenant_manager` role required

**Request**:
```json
{
  "email": "newadmin@company.com"
}
```

**Response 201**:
```json
{
  "id": "uuid",
  "email": "newadmin@company.com",
  "role": "tenant_admin",
  "tenant_id": "uuid-of-target-tenant",
  "is_active": true
}
```

**Errors**:
| Status | `code` | Condition |
|--------|--------|-----------|
| 401 | `auth_required` | Missing or invalid token |
| 403 | `permission_denied` | Caller is not `tenant_manager` |
| 404 | `not_found` | Tenant does not exist or is not active |
| 409 | `conflict` | Email is already registered |

---

## Standard 401 / 403 Shapes

**401 Unauthorized**:
```json
{
  "detail": "Authentication required",
  "code": "auth_required"
}
```

Variants by `code`:
- `auth_required` — no token present
- `token_expired` — JWT `exp` is in the past
- `invalid_token` — signature mismatch or malformed JWT
- `token_revoked` — JTI found in Redis revocation blacklist

**403 Forbidden**:
```json
{
  "detail": "Insufficient role",
  "code": "permission_denied"
}
```

---

## Role → Route Access Matrix

| Route prefix | `tenant_manager` | `tenant_admin` | `member` | Unauthenticated |
|---|---|---|---|---|
| `GET /auth/me` | ✅ | ✅ | ✅ | ❌ 401 |
| `POST /auth/logout` | ✅ | ✅ | ✅ | ❌ 401 |
| `GET /platform/*` | ✅ | ❌ 403 | ❌ 403 | ❌ 401 |
| `POST /platform/tenants/*/invite-admin` | ✅ | ❌ 403 | ❌ 403 | ❌ 401 |
| `GET /tenant/*` | ❌ 403 | ✅ | ❌ 403 | ❌ 401 |
| `GET /cms/*` | ❌ 403 | ✅ | ❌ 403 | ❌ 401 |
| `GET /leads/*` | ❌ 403 | ✅ | ❌ 403 | ❌ 401 |
| `POST /public/*` | N/A (widget token) | N/A | N/A | ✅ (widget token) |
