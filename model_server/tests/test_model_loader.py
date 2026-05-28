"""Tests for `model_server.app.core.model_loader`.

Verifies the hash-integrity contract (spec 007 FR-006 / US3) and the
classical+ONNX `predict(emb) -> (data_label, confidence)` shape.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from app.core.model_loader import (
    ArtifactIntegrityError,
    ModelLoader,
    load_all,
)
from tests.conftest import ARTIFACTS_DIR


def test_load_all_succeeds_on_real_artifacts() -> None:
    loader = load_all(ARTIFACTS_DIR)
    assert isinstance(loader, ModelLoader)
    assert loader.deployed_name in {"classical", "onnx"}
    assert loader.label_map.routing == {
        "spam": "spam",
        "faq": "faq_support",
        "support": "faq_support",
        "sales": "sales_contact",
    }
    assert "ambiguous" in loader.label_map.routing_intents


def test_classical_predict_returns_string_label_and_bounded_confidence() -> None:
    loader = load_all(ARTIFACTS_DIR)
    emb = np.zeros(1024, dtype=np.float64)
    data_label, confidence = loader.classical.predict(emb)
    assert data_label in loader.label_map.data_classes_alphabetical
    assert 0.0 <= confidence <= 1.0


def test_onnx_predict_returns_string_label_and_bounded_confidence() -> None:
    loader = load_all(ARTIFACTS_DIR)
    emb = np.zeros(1024, dtype=np.float64)
    data_label, confidence = loader.onnx.predict(emb)
    assert data_label in loader.label_map.data_classes_alphabetical
    assert 0.0 <= confidence <= 1.0


def test_onnx_rejects_wrong_input_dim() -> None:
    loader = load_all(ARTIFACTS_DIR)
    with pytest.raises(ValueError, match="input_dim"):
        loader.onnx.predict(np.zeros(7, dtype=np.float64))


def test_missing_label_map_refuses_startup(tmp_path: Path) -> None:
    # Copy the real artifacts into a temp dir, omit label_map.json.
    (tmp_path / "ml").mkdir()
    (tmp_path / "nn").mkdir()
    shutil.copy(ARTIFACTS_DIR / "ml" / "best_intent_classifier.joblib", tmp_path / "ml")
    shutil.copy(ARTIFACTS_DIR / "ml" / "model_card_ml.json", tmp_path / "ml")
    shutil.copy(ARTIFACTS_DIR / "nn" / "intent_classifier_nn.onnx", tmp_path / "nn")
    shutil.copy(ARTIFACTS_DIR / "nn" / "intent_classifier_nn.onnx.data", tmp_path / "nn")
    shutil.copy(ARTIFACTS_DIR / "nn" / "model_card_nn.json", tmp_path / "nn")

    with pytest.raises(ArtifactIntegrityError, match="label_map.json missing"):
        load_all(tmp_path)


def test_sha_mismatch_refuses_startup(tmp_path: Path) -> None:
    (tmp_path / "ml").mkdir()
    (tmp_path / "nn").mkdir()
    shutil.copy(ARTIFACTS_DIR / "ml" / "best_intent_classifier.joblib", tmp_path / "ml")
    shutil.copy(ARTIFACTS_DIR / "ml" / "model_card_ml.json", tmp_path / "ml")
    shutil.copy(ARTIFACTS_DIR / "nn" / "intent_classifier_nn.onnx", tmp_path / "nn")
    shutil.copy(ARTIFACTS_DIR / "nn" / "intent_classifier_nn.onnx.data", tmp_path / "nn")
    shutil.copy(ARTIFACTS_DIR / "nn" / "model_card_nn.json", tmp_path / "nn")
    shutil.copy(ARTIFACTS_DIR / "label_map.json", tmp_path)

    # Corrupt the classical artifact by appending a single byte.
    with (tmp_path / "ml" / "best_intent_classifier.joblib").open("ab") as fh:
        fh.write(b"\x00")

    with pytest.raises(ArtifactIntegrityError, match="sha256 mismatch"):
        load_all(tmp_path)
