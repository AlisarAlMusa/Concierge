# Concierge — Multi-tenant AI SaaS

Week 8 AIE Bootcamp Project.

## Quick Start

```bash
cp .env.example .env
# Fill in real API keys (LLM, embedding) in .env
docker compose up --build
```

The stack comes up at:
- API: http://localhost:8000 (docs at /docs in local mode)
- Admin: http://localhost:8501
- Model server: http://localhost:8001
- Guardrails: http://localhost:8002

## Run migrations

```bash
cd backend
uv sync
uv run alembic upgrade head
```

## Seed demo tenants

```bash
uv run python ../scripts/seed_tenants.py
```

## Development

```bash
cd backend
uv run ruff check .
uv run black --check .
uv run pytest
```

## Docs

- [Architecture + contracts](docs/SPEC.md)
- [Implementation plan](concierge_CLAUDE_plan.md)
- [Engineering rules](ENGINEERING_RULES.md)

## Team

- Person A (`feature/platform-tenancy`) — infrastructure, DB, auth, tenancy, admin UI, CI
- Person B (`feature/rag-agent-widget`) — CMS, embeddings, RAG, agent, widget
- Person C (`feature/ml-guardrails-evals`) — classifier, guardrails, redaction, evals

## Submission

```bash
git tag v0.1.0-week8
git push origin v0.1.0-week8
```
