"""Tests for `guardrails_sidecar.app.core.topic_similarity`."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from app.core.topic_similarity import (
    EMBEDDING_DIM,
    IntegrityError,
    build_embedder,
    cosine,
    embed,
)


def test_sha_mismatch_refuses_build(tmp_path: Path, embedder) -> None:
    """SHA mismatch must raise IntegrityError before the session loads."""
    # Copy real artifacts into tmp, then tamper with the onnx.
    from tests.conftest import MODELS_DIR

    for name in ("minilm_l6_v2.sha256", "minilm_tokenizer.json"):
        shutil.copy(MODELS_DIR / name, tmp_path / name)
    shutil.copy(MODELS_DIR / "minilm_l6_v2.onnx", tmp_path / "minilm_l6_v2.onnx")
    with (tmp_path / "minilm_l6_v2.onnx").open("ab") as fh:
        fh.write(b"\x00")
    with pytest.raises(IntegrityError, match="sha256 mismatch"):
        build_embedder(tmp_path)


def test_missing_artifact_refuses_build(tmp_path: Path) -> None:
    with pytest.raises(IntegrityError, match="ONNX missing"):
        build_embedder(tmp_path)


def test_embedding_shape_and_dtype(embedder) -> None:
    vec = embed(embedder, "what time do you open?")
    assert vec.shape == (EMBEDDING_DIM,)
    assert vec.dtype == np.float32


def test_embedding_is_l2_normalized(embedder) -> None:
    vec = embed(embedder, "lorem ipsum dolor sit amet")
    norm = float(np.linalg.norm(vec))
    assert abs(norm - 1.0) < 1e-5


def test_cosine_self_similarity_is_one(embedder) -> None:
    vec = embed(embedder, "anything reasonable here")
    assert cosine(vec, vec) == pytest.approx(1.0, abs=1e-5)


def test_empty_input_returns_zero_vector(embedder) -> None:
    vec = embed(embedder, "")
    assert vec.shape == (EMBEDDING_DIM,)
    assert (vec == 0).all()


def test_semantic_sanity(embedder) -> None:
    """Closer pairs must have higher cosine than unrelated pairs."""
    plumbing = embed(embedder, "plumbing")
    pipes = embed(embedder, "broken pipes")
    politics = embed(embedder, "politics")
    assert cosine(plumbing, pipes) > cosine(plumbing, politics)


def test_cosine_handles_zero_vector_safely() -> None:
    a = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    b = np.ones(EMBEDDING_DIM, dtype=np.float32) / np.sqrt(EMBEDDING_DIM)
    assert cosine(a, b) == 0.0
