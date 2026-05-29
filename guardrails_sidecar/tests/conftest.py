"""Session-scoped MiniLM embedder + rails engine.

The ONNX session is heavy to construct (~400 ms cold start), so we build it
once per test session and let every test reuse it.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Iterator

import pytest

# Put `guardrails_sidecar/` on sys.path so `from app.X import ...` resolves.
SIDECAR_ROOT = Path(__file__).resolve().parents[1]
if str(SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIDECAR_ROOT))

MODELS_DIR = SIDECAR_ROOT / "models"

TEST_SERVICE_TOKEN = secrets.token_urlsafe(48)


@pytest.fixture(scope="session", autouse=True)
def _env() -> Iterator[None]:
    original = {
        k: os.environ.get(k)
        for k in (
            "APP_ENV",
            "SERVICE_AUTH_SECRET",
            "GUARDRAILS_TOPIC_SIM_THRESHOLD",
            "GUARDRAILS_PLATFORM_THRESHOLD",
            "GUARDRAILS_HISTORY_TURNS",
        )
    }
    os.environ["APP_ENV"] = "local"
    os.environ["SERVICE_AUTH_SECRET"] = TEST_SERVICE_TOKEN
    # Leave thresholds unset so tests exercise the defaults.
    yield
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def embedder():
    from app.core.topic_similarity import build_embedder

    return build_embedder(MODELS_DIR)


@pytest.fixture(scope="session")
def rails_engine(embedder):
    from app.actions import set_embedder
    from app.core.rails_engine import RailsEngine

    set_embedder(embedder)
    return RailsEngine.build(embedder)
