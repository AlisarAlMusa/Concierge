"""Fixtures for spec 018 integration tests.

The sidecars live in sibling directories (`model_server/`, `guardrails_sidecar/`)
with their own `app/` packages. We load each one via `importlib` while isolating
the `app` namespace so the two sidecars don't collide in `sys.modules`.

Service-auth secret resolution is forced into local-mode fallback for the test
session — Vault is not consulted. The fixture token is identical across the
three services (matches the Phase 1 design).
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import sys
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parents[3]

# A high-entropy fixture token, regenerated per test session. Phase 1 expects
# the same value across api / model_server / guardrails_sidecar.
TEST_SERVICE_TOKEN = secrets.token_urlsafe(48)


_TEST_ENV_DEFAULTS = {
    # Service-auth (spec 018).
    "APP_ENV": "local",
    "SERVICE_AUTH_SECRET": TEST_SERVICE_TOKEN,
    # Backend's Settings class requires these — supply harmless defaults so
    # `get_settings()` does not blow up on missing-env-var validation errors.
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "VAULT_ADDR": "http://vault:8200",
    "VAULT_TOKEN": "dev-root-token",
    "VAULT_SERVICE_AUTH_PATH": "concierge/service-auth",
    "MINIO_ENDPOINT": "minio:9000",
    "MINIO_ACCESS_KEY": "minioadmin",
    "MINIO_SECRET_KEY": "minioadmin",
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "gpt-4o-mini",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "MODEL_SERVER_URL": "http://model_server:8001",
    "GUARDRAILS_URL": "http://guardrails_sidecar:8002",
    "WIDGET_TOKEN_SECRET": "test-widget-secret",
}


@pytest.fixture(scope="session", autouse=True)
def _force_local_env_with_fixture_token() -> Iterator[None]:
    """Seed the test process with a valid local-mode env.

    All three services' `get_settings()` honor `APP_ENV=local` — Vault is not
    consulted. The supplied `SERVICE_AUTH_SECRET` is what authenticated
    requests must carry. Tests that need a non-local mode use `monkeypatch` to
    override individual keys.
    """
    original = {k: os.environ.get(k) for k in _TEST_ENV_DEFAULTS}
    for k, v in _TEST_ENV_DEFAULTS.items():
        os.environ[k] = v
    yield
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _load_sidecar_app(name: str, sidecar_root: Path) -> FastAPI:
    """Load `<sidecar_root>/app/main.py` as a uniquely-named module.

    The two sidecars and the backend all use the literal package name `app`,
    so we save & restore `sys.modules["app.*"]` around the sidecar load to
    prevent backend code (e.g. Vault tests that patch `app.core.config`) from
    being shadowed by a sidecar's module of the same name.
    """
    saved = {k: sys.modules[k] for k in list(sys.modules) if k == "app" or k.startswith("app.")}
    for k in saved:
        del sys.modules[k]

    sys.path.insert(0, str(sidecar_root))
    try:
        spec = importlib.util.spec_from_file_location(
            f"{name}_main", sidecar_root / "app" / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module: ModuleType = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(sidecar_root))
        for k in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            del sys.modules[k]
        for k, v in saved.items():
            sys.modules[k] = v

    return module.app  # type: ignore[no-any-return]


@pytest.fixture()
def model_server_app() -> FastAPI:
    # `model_server` now imports `joblib`, `onnxruntime`, `cohere` at startup
    # (spec 007). The backend's venv does not (and should not) ship those —
    # constitution V keeps the API image lean. Skip cleanly when the deps are
    # absent; model_server's own tests cover its routes end-to-end.
    try:
        return _load_sidecar_app("model_server", REPO_ROOT / "model_server")
    except ModuleNotFoundError as exc:
        pytest.skip(
            f"model_server deps not installed in backend venv ({exc.name}); "
            f"run model_server's own test suite for endpoint coverage"
        )


@pytest.fixture()
def guardrails_sidecar_app() -> FastAPI:
    # `guardrails_sidecar` now imports `onnxruntime`, `tokenizers`, `numpy` at
    # startup (spec 010 FR-017). The backend's venv intentionally does not
    # ship those — constitution V keeps the API image lean. Skip when the
    # deps are absent; the sidecar's own tests cover its routes end-to-end.
    try:
        return _load_sidecar_app("guardrails_sidecar", REPO_ROOT / "guardrails_sidecar")
    except ModuleNotFoundError as exc:
        pytest.skip(
            f"guardrails_sidecar deps not installed in backend venv ({exc.name}); "
            f"run guardrails_sidecar's own test suite for endpoint coverage"
        )
