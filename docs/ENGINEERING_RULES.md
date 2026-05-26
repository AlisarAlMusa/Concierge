# ENGINEERING_RULES.md — Project Rules and Code Review Standards

This file summarizes the engineering rules this project must follow. It is based on the Week 7 project brief, Week 3/4 code review lessons, and the Engineering Standards guide.

## 1. Own Every Line

If code, config, Docker directives, prompts, or model settings live in the repository, the developer must be able to explain them.

Avoid:
- copied code without understanding;
- vague answers like "I think";
- giant files where responsibilities are mixed.

## 2. Clean Project Structure

Separate:
- API routes;
- services;
- repositories;
- domain models;
- infrastructure adapters;
- prompts;
- tests;
- frontend apps;
- model-server code.

Rules:
- routes handle HTTP only;
- services own business logic;
- repositories own SQL;
- domain models are separate from ORM models;
- infra owns external systems;
- prompts live in `prompts/`, not inside service code.

## 3. Async All the Way Down

Use async I/O in request paths:
- `httpx.AsyncClient` for HTTP;
- SQLAlchemy async sessions;
- async LLM SDK methods where available;
- `asyncio.gather` for independent I/O calls.

Avoid:
- `requests`;
- `time.sleep`;
- blocking external calls;
- loading models per request.

CPU-heavy work should be moved to a worker or `asyncio.to_thread` if needed.

## 4. Dependency Injection

Use FastAPI `Depends()` for:
- DB sessions;
- current authenticated maintainer;
- settings;
- services;
- model clients;
- Redis;
- MinIO;
- Vault;
- LLM clients.

Do not construct these inside routes.

## 5. Lifespan Singletons

Load expensive shared resources once in FastAPI lifespan:
- DB engine;
- Redis client;
- HTTP client;
- LLM client;
- model-server client;
- embedding model;
- tracing client.

Expose them through dependencies.

## 6. Configuration

Use `pydantic-settings`.

Rules:
- no `os.getenv` outside config;
- all required values are typed;
- use `extra="forbid"`;
- `.env.example` documents all needed values;
- secrets are resolved from Vault, not hardcoded.

## 7. Auth and Authorization

Use JWT auth.

Rules:
- JWT is sent in `Authorization: Bearer <token>`;
- JWT payload must not contain sensitive data;
- 401 means unauthenticated;
- 403 means authenticated but not allowed;
- protected endpoints must have explicit dependencies.

Roles:
- maintainer;
- admin.

## 8. Database Persistence

Use:
- PostgreSQL 16;
- SQLAlchemy ORM;
- Alembic migrations.

Rules:
- no important data only in Python memory;
- migrations are committed;
- data must survive container restarts;
- no deleting volumes as a migration strategy.

## 9. API Contracts and DTOs

Use Pydantic models for:
- request bodies;
- response models;
- tool args;
- tool outputs;
- webhook payloads;
- structured errors.

Never return ORM objects directly if they contain internal fields.

## 10. Tool Design

Every tool must have:
- clear name;
- docstring;
- typed Pydantic args schema;
- typed output schema;
- structured error result.

Tool failures should not crash the chatbot. Return a structured error so the LLM can recover.

## 11. RAG Pipeline Ownership

Be able to explain:

Ingestion:
```text
load → clean → chunk → embed → store
```

Retrieval:
```text
query → rewrite → sparse search + dense search → merge → rerank → prompt → answer
```

Document:
- chunking strategy;
- embedding model choice;
- metadata stored;
- top-k values;
- retrieval metrics.

## 12. Leakage Prevention

Prevent leakage:
- held-out issues must not appear in classifier training;
- RAG eval ground-truth answers must not be inserted into retrieval index;
- prompts must not include expected answers;
- test set should be more recent than train when required.

## 13. Evaluation

CI must run:
- classification eval;
- RAG eval;
- redaction test;
- unit tests;
- smoke tests where possible.

Thresholds are committed in `eval_thresholds.yaml`.

No threshold may be zero/disabled.

## 14. Logging

Use structured JSON logs.

Log fields:
- event;
- level;
- timestamp;
- service;
- request_id;
- trace_id;
- safe metadata.

Do not use `print()` inside application services.

## 15. Redaction

Redact sensitive data before:
- logs;
- traces;
- memory writes;
- MinIO snapshots;
- error responses.

Test redaction explicitly with fake secrets.

## 16. Tracing

Every conversation should be traceable.

Trace spans:
- LLM call;
- tool call;
- model-server call;
- RAG retrieval;
- reranking;
- memory write;
- errors.

The trace ID should appear in logs.

## 17. Errors and Retries

External calls need:
- timeout;
- retries with exponential backoff for transient failures;
- no retries for permanent 4xx-style errors;
- structured error mapping.

Use domain exceptions:
- `NotFoundError`;
- `PermissionDeniedError`;
- `ToolFailureError`;
- `ExternalServiceError`.

Map them centrally to HTTP responses.

## 18. Docker Rules

Be able to explain:
- every service;
- every volume;
- every network;
- every healthcheck;
- every environment variable.

Remember:
- networks let containers talk;
- volumes persist data.

Do not hardcode environment-specific URLs.

## 19. Testing Rules

Minimum tests:
- Pydantic valid/invalid schema tests;
- tool tests with mocked external services;
- redaction tests;
- auth/permission tests;
- one end-to-end chat flow with mocked LLM/model-server;
- RAG eval script;
- classification eval script.

Tests must run in CI.

## 20. Frontend Rules

Streamlit:
- internal/admin dashboard;
- fast iteration;
- memory inspection;
- widget config;
- full chat.

React widget:
- production-like embeddable UI;
- small bundle;
- runtime config;
- postMessage resize;
- origin allowlisting.

## 21. Documentation Files

Required/recommended:
- `README.md`: project map and run instructions;
- `PLAN.md`: implementation plan;
- `ARCH.md`: architecture and diagrams;
- `DECISIONS.md`: defended choices with numbers;
- `EVALS.md`: golden sets, metrics, thresholds;
- `SECURITY.md`: auth, Vault, redaction, widget security;
- `RUNBOOK.md`: operational/debug instructions;
- `ENGINEERING_RULES.md`: standards and review checklist.

## 22. Review Readiness Checklist

Before demo, be able to answer:
- Where is each route?
- Where is business logic?
- Where is SQL?
- Where are prompts?
- Where are tools registered?
- How is JWT sent?
- What returns 401 vs 403?
- Where does data persist?
- How do containers communicate?
- Which volumes preserve data?
- How is RAG evaluated?
- How is classification evaluated?
- How does redaction work?
- What happens if model-server fails?
- What happens if Vault is unreachable?
- Why did you choose your deployment model?
- Why did you choose your embedding model?
