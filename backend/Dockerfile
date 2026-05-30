# syntax=docker/dockerfile:1.6
#
# Two-stage build:
#   1) ``widget-builder`` (node:20-alpine) compiles the embeddable widget bundle.
#   2) Runtime (python:3.11-slim) installs the Python API and pulls the bundle
#      across so ``GET /widget.js`` can serve it as a static asset.
#
# IMPORTANT: docker-compose passes ``context: .`` (the repo root) to this
# Dockerfile so the node stage can reach the sibling ``widget/`` directory.

# ── Stage 1: widget bundle ───────────────────────────────────────────────────
FROM node:20-alpine AS widget-builder
WORKDIR /widget

COPY widget/package.json widget/package-lock.json* ./
RUN npm install --silent

COPY widget/ ./
RUN npm run build && test -f dist/widget.js

# ── Stage 2: API runtime ─────────────────────────────────────────────────────
FROM python:3.11-slim

RUN pip install uv

WORKDIR /app

COPY backend/pyproject.toml .
RUN uv pip install --system -e .

COPY backend/ .

RUN mkdir -p /app/static
COPY --from=widget-builder /widget/dist/widget.js /app/static/widget.js

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
