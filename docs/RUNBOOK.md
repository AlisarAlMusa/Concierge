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
