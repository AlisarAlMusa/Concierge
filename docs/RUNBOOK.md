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
