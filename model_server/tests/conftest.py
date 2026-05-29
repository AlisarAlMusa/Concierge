"""Test fixtures for `model_server`.

`APP_ENV=local` is forced so the Settings singleton does not consult Vault.
A high-entropy `SERVICE_AUTH_SECRET` is seeded for spec 018 compatibility.
Cohere is never actually called — `embed_query` is replaced by an in-memory
fake that returns a deterministic 1024-dim vector.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

# `model_server/` must be on sys.path so `from app.X import ...` resolves
# against the sidecar's own `app/` package.
SIDECAR_ROOT = Path(__file__).resolve().parents[1]
if str(SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIDECAR_ROOT))

TEST_SERVICE_TOKEN = secrets.token_urlsafe(48)
ARTIFACTS_DIR = SIDECAR_ROOT / "artifacts"


@pytest.fixture(scope="session", autouse=True)
def _force_local_env() -> Iterator[None]:
    original = {
        k: os.environ.get(k)
        for k in (
            "APP_ENV",
            "SERVICE_AUTH_SECRET",
            "ARTIFACTS_DIR",
            "COHERE_API_KEY",
            "EMBEDDING_MODEL",
        )
    }
    os.environ["APP_ENV"] = "local"
    os.environ["SERVICE_AUTH_SECRET"] = TEST_SERVICE_TOKEN
    os.environ["ARTIFACTS_DIR"] = str(ARTIFACTS_DIR)
    # COHERE_API_KEY is required by the client's constructor; the value is
    # never used because tests monkeypatch `embed_query`.
    os.environ.setdefault("COHERE_API_KEY", "test-key-not-used")
    os.environ.setdefault("EMBEDDING_MODEL", "embed-english-v3.0")
    yield
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture()
def fake_embedding(seed: int = 0) -> list[float]:
    """A deterministic 1024-dim vector for tests that don't care about the value."""
    rng = np.random.default_rng(seed)
    return rng.normal(size=1024).astype(np.float64).tolist()
