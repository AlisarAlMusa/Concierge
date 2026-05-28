# Data Model: Auth & Roles

**Feature**: 002-auth-and-roles | **Date**: 2026-05-27

---

## PostgreSQL Tables

### `users` (extends fastapi-users base)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID | PK, default gen_random_uuid() | fastapi-users base |
| `email` | VARCHAR(320) | UNIQUE, NOT NULL | fastapi-users base |
| `hashed_password` | TEXT | NOT NULL | fastapi-users base; bcrypt |
| `is_active` | BOOLEAN | NOT NULL, default TRUE | fastapi-users base |
| `is_superuser` | BOOLEAN | NOT NULL, default FALSE | fastapi-users base; unused — roles replace this |
| `is_verified` | BOOLEAN | NOT NULL, default TRUE | fastapi-users base; verification skipped in Week 8 |
| `role` | user_role_enum | NOT NULL, default 'member' | Custom: `tenant_manager` \| `tenant_admin` \| `member` |
| `tenant_id` | UUID | FK → tenants.id, NULLABLE | NULL for `tenant_manager`; required for `tenant_admin` + `member` |
| `created_at` | TIMESTAMPTZ | NOT NULL, default now() | Custom |

**Indexes**:
- `users_email_idx` UNIQUE on `email` (fastapi-users default)
- `users_tenant_id_idx` on `tenant_id` (for tenant admin lookups)
- `users_role_idx` on `role` (for platform queries)

**Constraints**:
- CHECK: `(role = 'tenant_manager' AND tenant_id IS NULL) OR (role != 'tenant_manager' AND tenant_id IS NOT NULL)` — enforces tenant_id nullability rule at DB level.

**RLS**: Users table is NOT tenant-isolated — it is a platform-level table. No RLS policy is applied. Access control is enforced at the application layer by the auth dependencies.

---

### `audit_logs` (used, not created here — created in 0001_initial.py)

Relevant auth event shapes:

| `action` value | `actor_user_id` | `tenant_id` | `metadata` |
|----------------|-----------------|-------------|------------|
| `register` | new user id | user's tenant_id | `{email: "..."}` |
| `login` | user id | user's tenant_id | `{ip: "..."}` |
| `logout` | user id | user's tenant_id | `{jti: "..."}` |
| `failed_login` | NULL | NULL | `{email_attempted: "...", ip: "..."}` |
| `invite_admin` | tenant_manager id | target tenant_id | `{invited_email: "..."}` |

---

## Redis Keys (not a DB table)

### JTI Revocation Blacklist

```
Key:    revoked_jti:{jti}
Value:  "1"
TTL:    remaining token lifetime in seconds (exp - now)
Set by: POST /auth/logout
Read by: auth middleware / get_current_user dependency
```

Example: `revoked_jti:a3f2c1d0-8e7b-4a9f-bc12-3d4e5f6a7b8c` → `"1"` (TTL: 82400s)

### Login Rate-Limit Counter

```
Key:    login_attempts:{client_ip}
Value:  integer (incremented on each attempt)
TTL:    900 seconds (15 minutes), set on first write
Set by: POST /auth/login (every attempt, success or failure)
Read by: POST /auth/login (before processing)
```

Example: `login_attempts:192.168.1.50` → `7` (TTL: 543s remaining)

---

## Enums

### `UserRole`

```python
class UserRole(str, Enum):
    tenant_manager = "tenant_manager"
    tenant_admin   = "tenant_admin"
    member         = "member"
```

Created as a PostgreSQL ENUM type `user_role_enum` in migration `0002_users_roles`.

---

## Migration: `0002_users_roles`

**Depends on**: `0001_initial`

**Operations**:
1. Create PostgreSQL ENUM type `user_role_enum` with values `('tenant_manager', 'tenant_admin', 'member')`
2. Create `users` table (fastapi-users columns + `role`, `tenant_id`, `created_at`)
3. Add FK constraint: `users.tenant_id → tenants.id ON DELETE SET NULL`
4. Add CHECK constraint: `(role = 'tenant_manager' AND tenant_id IS NULL) OR (role != 'tenant_manager' AND tenant_id IS NOT NULL)`
5. Create indexes: `users_tenant_id_idx`, `users_role_idx`

**No RLS on users table** — platform-level table, access controlled entirely at application layer.

---

## JWT Payload Schema

```json
{
  "sub": "uuid-string",
  "role": "tenant_admin",
  "jti": "uuid-string",
  "exp": 1748649600
}
```

**MUST NOT contain**: `email`, `tenant_id`, `hashed_password`, or any other PII.

The `role` field is included to avoid a DB lookup on every role-check dependency. The `sub` field is used to fetch the full user record when `tenant_id` is needed (e.g. in `require_tenant_admin`).
