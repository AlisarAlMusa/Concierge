#!/usr/bin/env bash
set -e
echo "Smoke test: checking health endpoints..."
curl -sf http://localhost:8000/health | grep '"status":"ok"'
curl -sf http://localhost:8001/health | grep '"status":"ok"'
curl -sf http://localhost:8002/health | grep '"status":"ok"'
echo "All health checks passed."
