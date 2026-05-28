| Service | URL | Credentials / Notes |
| --- | --- | --- |
| API | http://localhost:8000 | JWT auth — register via `POST /auth/register`, login via `POST /auth/login` |
| API Docs (Swagger) | http://localhost:8000/docs | Only available when `APP_ENV=local` |
| Model Server | http://localhost:8001 | Header: `X-Service-Token: change-me-local-dev-only` |
| Model Server Health | http://localhost:8001/health | No auth |
| Guardrails Sidecar | http://localhost:8002 | Header: `X-Service-Token: change-me-local-dev-only` |
| Guardrails Health | http://localhost:8002/health | No auth |
| Admin App | http://localhost:8501 | Login with a `tenant_admin` or `tenant_manager` account created via the API |
| PostgreSQL | localhost:5432 | User: `concierge` / Password: `concierge` / DB: `concierge` |
| Redis | localhost:6379 | No password (dev mode) |
| MinIO API | http://localhost:9000 | Access key: `minioadmin` / Secret: `minioadmin` |
| MinIO Console | http://localhost:9001 | Same: `minioadmin` / `minioadmin` |
| Vault | http://localhost:8200 | Root token: `dev-root-token` |
| Phoenix UI | http://localhost:6006 | Local trace viewer (spec 017) |

---

## Service-to-Service Auth (spec 018)

`api`, `model_server`, and `guardrails_sidecar` share a single token,
`SERVICE_AUTH_SECRET`, sourced from Vault at `kv/concierge/service-auth` (key
`token`). The `vault-init` one-shot Compose service writes a random 36-byte
value on first stack startup; existing values are not overwritten.

**Read the current token (local dev only)**

```bash
docker compose exec vault \
  vault kv get -mount=kv concierge/service-auth
```

**Rotate the token**

Phase 1 supports rotation via restart only (hot reload arrives in Phase 2):

```bash
# 1. Write a new value in Vault.
docker compose exec vault \
  vault kv put -mount=kv concierge/service-auth \
    token="$(head -c 36 /dev/urandom | base64 | tr -d '\n')"

# 2. Restart the three services so they re-fetch on startup.
docker compose restart api model_server guardrails_sidecar
```

**Local-mode fallback**

When `APP_ENV=local` and `SERVICE_AUTH_SECRET` is unset, Vault is not
consulted and the dependency rejects every request with 403 (a warning is
logged on boot). Set `SERVICE_AUTH_SECRET` in `.env` for offline work; the
warning is intentional and tells you you're in fallback mode.

## Demo seed data (Owner B)

After the stack is up and migrations `0001`–`0003` have applied, run the
seed script to populate one tenant, one widget, and a small RAG corpus
covering pricing / refunds / shipping / support / product overview.
Re-running is safe — the script is idempotent (Tenant + Widget upsert by
natural key; CMS chunks are written via `RagService.index_page` which
delete-then-inserts per `(tenant_id, page_id)`).

```bash
# Recommended — runs inside the api container so the env + Postgres
# hostnames in .env match.
docker compose exec api uv run python scripts/seed_demo_data.py
```

```bash
# Alternative — run on the host. Requires:
#   * COHERE_API_KEY set in your shell or in backend/.env
#   * DATABASE_URL pointing at localhost:5432 instead of postgres:5432
cd backend
uv run python scripts/seed_demo_data.py
```

Stable demo identifiers (deterministic — same on every machine):

| Field | Value |
| --- | --- |
| `tenant_slug` | `demo` |
| `tenant_id` | `uuid5(NAMESPACE_DNS, "demo.concierge.local")` |
| `widget_public_id` | `demo-widget-001` |
| `allowed_origins` | `http://localhost:3000`, `http://localhost:8501` |

### Test the widget session endpoint

```bash
curl -s -X POST http://localhost:8000/widgets/session \
  -H 'Content-Type: application/json' \
  -d '{"public_widget_id": "demo-widget-001", "origin": "http://localhost:3000"}'
# → {"token": "<JWT>", "token_type": "Bearer", "expires_in": 900}

# Disallowed origin → 403:
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/widgets/session \
  -H 'Content-Type: application/json' \
  -d '{"public_widget_id": "demo-widget-001", "origin": "https://attacker.example.com"}'
# → 403

# Unknown widget → 404:
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/widgets/session \
  -H 'Content-Type: application/json' \
  -d '{"public_widget_id": "does-not-exist", "origin": "http://localhost:3000"}'
# → 404
```

### Test the chat endpoint

Use the JWT from `/widgets/session` as the bearer token. The route reads
`tenant_id` / `widget_id` / `visitor_session_id` from the verified token
only — anything in the request body is ignored.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/widgets/session \
  -H 'Content-Type: application/json' \
  -d '{"public_widget_id": "demo-widget-001", "origin": "http://localhost:3000"}' \
  | jq -r .token)

curl -s -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message": "How much does the Growth plan cost?"}'
# → {"message": "...", "conversation_id": "...", "intent_label": "...", "sources": [...]}
```

Try a few of the seeded topics to exercise different RAG hits:

| Question | Expected RAG source |
| --- | --- |
| `How much does the Growth plan cost?` | Pricing page |
| `What's your refund policy?` | Refund policy page |
| `When are you open for support?` | Support hours page |
| `Do you ship internationally?` | Shipping page |
| `What does your product do?` | Product overview page |

## CMS ingestion (Owner B)

`POST /cms/pages` is the real authoring surface: tenant admins publish a
`title` + `body`, the API persists a `cms_pages` row, and the same
request routes the body through `RagService.index_page` so the
embeddings land in `cms_chunks` in the same transaction. This is the
exact pipeline the demo seed uses internally — no parallel paths.

### Auth (transitional)

Until Owner A's admin auth ships, the CMS surface is gated by two
layers:

* `X-Service-Token` — value of `SERVICE_AUTH_SECRET` from `.env`
  (default `change-me-local-dev-only`).
* `X-Tenant-Id` — the tenant id the admin is operating on. The server
  uses this for the RLS session, the `cms_pages.tenant_id` column, and
  the `cms_chunks.tenant_id` column.

A wrong service token → 403. A non-UUID tenant header → 400.

### Upload a page

```bash
TENANT_ID="$(docker compose exec -T postgres psql -U concierge -d concierge -tA \
  -c "select id from tenants where slug='demo'")"

curl -s -X POST http://localhost:8000/cms/pages \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Returns & Exchanges",
    "body": "We accept returns within 30 days of purchase. Items must be unworn and in original packaging. International returns are the buyer's responsibility for return shipping cost. To start a return email returns@demo-co.example with your order number."
  }'
# →
# {
#   "id": "...",
#   "tenant_id": "...",
#   "title": "Returns & Exchanges",
#   "slug": "returns-exchanges",
#   "body": "...",
#   "status": "published",
#   "chunks_written": 1,
#   "created_at": "...",
#   "updated_at": "..."
# }
```

Re-posting the same payload (same slug) updates the existing page in
place and re-indexes the chunks. The response is still 201.

### List pages

```bash
curl -s http://localhost:8000/cms/pages \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" \
  | jq '.total, .items[].slug'
# 6
# "returns-exchanges"
# "product-overview"
# "support-hours"
# "shipping"
# "refund-policy"
# "pricing"
```

### Fetch a single page

```bash
curl -s http://localhost:8000/cms/pages/<page-id> \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID"
```

A page belonging to another tenant returns `404` (not `403` — leaking
existence across tenants is itself an isolation defect).

### Test retrieval against uploaded content

After uploading, ask the agent something only that page can answer:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/widgets/session \
  -H 'Content-Type: application/json' \
  -d '{"public_widget_id": "demo-widget-001", "origin": "http://localhost:3000"}' \
  | jq -r .token)

curl -s -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message": "How do I start a return?"}' | jq
# → the reply references "returns@demo-co.example" + the 30-day window;
#   sources[] includes the page_id of the page you just uploaded.
```

### Tenant isolation check

Upload identically-titled pages under two different tenant ids and
confirm each list call only sees its own:

```bash
curl -s http://localhost:8000/cms/pages \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: <tenant-a-uuid>" | jq '.total'
curl -s http://localhost:8000/cms/pages \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: <tenant-b-uuid>" | jq '.total'
```

Each tenant sees only its own pages — `WHERE tenant_id = $1` at the SQL
layer plus the `cms_pages_tenant_isolation` RLS policy enforces this on
the read path. The write path enforces it on insert via the same RLS
`WITH CHECK` clause.

<<<<<<< HEAD
## Admin leads & escalations (Owner B, Spec 012)

The agent writes leads via the `capture_lead` tool and escalations via
the `escalate` tool — both go through `LeadService.capture` /
`EscalationService.create` on the widget-token path. The admin surfaces
below are read-only-plus-status-edit views on the same rows, gated by
the same `X-Service-Token` + `X-Tenant-Id` headers as `/cms`.

Per Spec 012 Assumptions, there is **no** `DELETE /escalations` route —
removing an escalation belongs to the tenant erasure flow (feature 015).
`DELETE /leads/{lead_id}` is a hard delete; the row is gone from the DB
once the call returns 204.

### List leads

```bash
curl -s "http://localhost:8000/leads?limit=50&offset=0" \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" | jq '.total, .items[] | .status + " " + .intent'
# 12
# "new purchase enterprise plan"
# "contacted demo request"
# ...
```

Default page size is 50 (Spec 012 Assumptions); `limit` is clamped to
`[1, 500]`, `offset` to `>= 0`. Bad values return `422`.

### Update a lead (status / notes)

```bash
curl -s -X PATCH http://localhost:8000/leads/$LEAD_ID \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"status": "contacted", "notes": "Left voicemail Tuesday 2pm."}'
# → updated LeadRead with new status + notes + bumped updated_at
```

Either field is optional. Pass an empty `notes` string to clear notes.
Only `status` and `notes` are admin-writable (Spec 012 FR-006); other
columns are visitor-provided or pipeline-owned.

### Delete a lead

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X DELETE http://localhost:8000/leads/$LEAD_ID \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID"
# → 204 on success, 404 if the lead does not exist for this tenant.
```

Cross-tenant deletes return 404 (never 403) — leaking existence across
tenants is itself an isolation defect.

### List escalations

```bash
curl -s http://localhost:8000/escalations \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" | jq '.total, .items[].status'
# 3
# "open"
# "in_progress"
# "resolved"
```

### Update escalation status

```bash
curl -s -X PATCH http://localhost:8000/escalations/$ESCALATION_ID \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"status": "resolved"}'
# → updated EscalationRead with new status + bumped updated_at
```

Valid transitions: `open` → `in_progress` → `resolved` (or `dismissed`).
The route intentionally does **not** flip the parent `Conversation.status`
back to `active` on resolve — that side effect needs explicit product
design and is out of this PR's scope.
=======
### Edit, unpublish, delete, and reindex (Spec 005 FR-004 → FR-008)

The CMS surface exposes the full edit lifecycle. All routes share the
same `X-Service-Token` + `X-Tenant-Id` gate as `POST /cms/pages`. The
reindex policy is enforced at the service layer: a body change on a
published page reindexes through `RagService.index_page`; flipping a
page to `draft` purges its chunks; reindex on a draft is a no-op
(FR-009).

```bash
PAGE_ID=<id-from-POST-response>

# Partial update — change only what you send. Body change on a published
# page triggers a reindex, returns chunks_written.
curl -s -X PATCH http://localhost:8000/cms/pages/$PAGE_ID \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"body": "Updated copy here."}'

# Unpublish — flips status to draft AND drops chunks so retrieval can
# never surface draft content (FR-009).
curl -s -X PATCH http://localhost:8000/cms/pages/$PAGE_ID \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"status": "draft"}'

# Publish — sets status to published, reindexes through RagService.
curl -s -X POST http://localhost:8000/cms/pages/$PAGE_ID/publish \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID"

# Reindex a single published page in place. RagService.index_page is
# itself delete-then-insert, so chunks are never duplicated. Draft
# pages return chunks_written=0 without touching the vector store.
curl -s -X POST http://localhost:8000/cms/pages/$PAGE_ID/reindex \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID"

# Delete — removes the page AND its chunks (tenant-scoped). 204 on
# success, 404 if the page does not exist for this tenant.
curl -s -o /dev/null -w "%{http_code}\n" \
  -X DELETE http://localhost:8000/cms/pages/$PAGE_ID \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID"
# → 204

# Bulk reindex — re-embed every published page for the caller's tenant.
# Synchronous; fine for the seed corpus (SC-005 caps a tenant at 500
# pages). Returns counts so admin tooling can verify the operation.
curl -s -X POST http://localhost:8000/cms/reindex-all \
  -H "X-Service-Token: change-me-local-dev-only" \
  -H "X-Tenant-Id: $TENANT_ID"
# → {"pages_reindexed": 6, "chunks_written": 14}
```

Slug changes on PATCH are validated server-side: a slug already taken
by a *different* page for the same tenant returns `409 conflict` rather
than silently upserting (that path is only available through
`POST /cms/pages`).
>>>>>>> main
<<<<<<<
<<<<<<<

e
x
it

X

