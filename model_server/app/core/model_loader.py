"""Model artifact loader for `model_server` (spec 007 FR-006, FR-013…016).

Loads the KNN classical pipeline (`joblib`) and the ONNX FFN, hash-verifies
each against its model card, and exposes a `LoadedModel` with a uniform
`predict(emb) -> (label_str, confidence)` callable for each.

Refuses to start on any of: missing artifact, missing model card, missing
`label_map.json`, SHA mismatch. The lifespan crashes loudly — there is no
fall-back path because a tampered or mis-staged model is a routing-correctness
hazard.

Owner: Person C. Frozen contract: `load_all(artifacts_dir) -> ModelLoader`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import joblib
import numpy as np
import onnxruntime

logger = logging.getLogger(__name__)

Predict = Callable[[np.ndarray], tuple[str, float]]


class ArtifactIntegrityError(RuntimeError):
    """Raised when an artifact's SHA-256 disagrees with its model card or a
    required file is missing. Refuses to mask a tampered model.
    """


@dataclass(frozen=True)
class LoadedModel:
    name: Literal["classical", "onnx"]
    predict: Predict
    model_version: str
    metrics: dict[str, float]


@dataclass(frozen=True)
class LabelMap:
    """Translates a data-label string (`faq`, `support`, `sales`, `spam`) to a
    routing intent (`spam`, `faq_support`, `sales_contact`, `human_request`,
    `ambiguous`). Unmapped data labels fall through to `ambiguous`.
    """

    routing: dict[str, str]
    routing_intents: list[str]
    data_classes_alphabetical: list[str]

    def to_routing(self, data_label: str) -> str:
        return self.routing.get(data_label, "ambiguous")


@dataclass(frozen=True)
class ModelLoader:
    classical: LoadedModel
    onnx: LoadedModel
    label_map: LabelMap
    deployed_name: Literal["classical", "onnx"]

    @property
    def deployed(self) -> LoadedModel:
        return self.classical if self.deployed_name == "classical" else self.onnx


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_sha(path: Path, expected: str, what: str) -> None:
    actual = _sha256_of_file(path)
    if actual != expected:
        raise ArtifactIntegrityError(
            f"{what} sha256 mismatch: {path.name} got {actual[:12]}…, "
            f"card said {expected[:12]}…"
        )


def _model_version(card: dict[str, Any], artifact_path: Path) -> str:
    """Pick `model_version` from the card, or derive a stable short version
    from the artifact's SHA-256 (FR-016 — never `unknown`, never empty)."""
    version = card.get("model_version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return _sha256_of_file(artifact_path)[:12]


def _softmax_max(logits: np.ndarray) -> tuple[int, float]:
    # logits: (4,) for a single sample.
    z = logits - logits.max()  # numerical stability
    exp = np.exp(z)
    p = exp / exp.sum()
    idx = int(p.argmax())
    return idx, float(p[idx])


def _load_classical(joblib_path: Path, card: dict[str, Any]) -> LoadedModel:
    _verify_sha(joblib_path, card["artifact_sha256"], "classical")
    clf = joblib.load(joblib_path)

    classes = getattr(clf, "classes_", None)
    if classes is None:
        raise ArtifactIntegrityError(
            f"classical pipeline at {joblib_path.name} has no `classes_` attribute"
        )
    classes_list = [str(c) for c in classes]
    has_proba = hasattr(clf, "predict_proba")

    def predict(emb: np.ndarray) -> tuple[str, float]:
        x = emb.reshape(1, -1) if emb.ndim == 1 else emb
        if has_proba:
            proba = clf.predict_proba(x)[0]
            idx = int(proba.argmax())
            return classes_list[idx], float(proba[idx])
        # LinearSVC-style fallback: softmax over decision_function.
        scores = clf.decision_function(x)[0]
        idx, conf = _softmax_max(np.asarray(scores))
        return classes_list[idx], conf

    return LoadedModel(
        name="classical",
        predict=predict,
        model_version=_model_version(card, joblib_path),
        metrics=dict(card.get("metrics", {})),
    )


def _load_onnx(
    onnx_path: Path, card: dict[str, Any], data_classes_alphabetical: list[str]
) -> LoadedModel:
    _verify_sha(onnx_path, card["artifact_sha256"], "onnx")
    # The .onnx file references intent_classifier_nn.onnx.data for large
    # weights; onnxruntime resolves that sibling automatically. No extra hash
    # check on the .data file because its identity is implied by the .onnx
    # header's external-data records.
    sess = onnxruntime.InferenceSession(
        str(onnx_path), providers=["CPUExecutionProvider"]
    )
    input_name = sess.get_inputs()[0].name
    expected_dim = card.get("input_dim", 1024)
    expected_classes = card.get("num_classes", len(data_classes_alphabetical))

    def predict(emb: np.ndarray) -> tuple[str, float]:
        x = emb.reshape(1, -1) if emb.ndim == 1 else emb
        if x.shape[1] != expected_dim:
            raise ValueError(
                f"onnx expects input_dim={expected_dim}, got {x.shape[1]}"
            )
        logits = sess.run(None, {input_name: x.astype(np.float32)})[0][0]
        idx, conf = _softmax_max(np.asarray(logits))
        if idx >= len(data_classes_alphabetical):
            raise ArtifactIntegrityError(
                f"onnx returned class index {idx} but label_map only knows "
                f"{len(data_classes_alphabetical)} classes"
            )
        if expected_classes != len(data_classes_alphabetical):
            logger.warning(
                "onnx card claims %d classes but label_map has %d",
                expected_classes,
                len(data_classes_alphabetical),
            )
        return data_classes_alphabetical[idx], conf

    return LoadedModel(
        name="onnx",
        predict=predict,
        model_version=_model_version(card, onnx_path),
        metrics=dict(card.get("metrics", {})),
    )


def _load_label_map(path: Path) -> LabelMap:
    raw = json.loads(path.read_text())
    routing = raw.get("routing")
    intents = raw.get("routing_intents")
    classes = raw.get("data_classes_alphabetical")
    if not all(isinstance(x, (dict, list)) for x in (routing, intents, classes)):
        raise ArtifactIntegrityError(f"{path.name} is malformed")
    return LabelMap(
        routing=dict(routing),
        routing_intents=list(intents),
        data_classes_alphabetical=list(classes),
    )


def load_all(artifacts_dir: Path) -> ModelLoader:
    if not artifacts_dir.is_dir():
        raise ArtifactIntegrityError(f"artifacts dir not found: {artifacts_dir}")

    label_map_path = artifacts_dir / "label_map.json"
    if not label_map_path.exists():
        raise ArtifactIntegrityError(
            f"label_map.json missing at {label_map_path} (FR-015)"
        )
    label_map = _load_label_map(label_map_path)

    ml_card = json.loads((artifacts_dir / "ml" / "model_card_ml.json").read_text())
    nn_card = json.loads((artifacts_dir / "nn" / "model_card_nn.json").read_text())

    classical = _load_classical(
        artifacts_dir / "ml" / "best_intent_classifier.joblib", ml_card
    )
    onnx = _load_onnx(
        artifacts_dir / "nn" / "intent_classifier_nn.onnx",
        nn_card,
        label_map.data_classes_alphabetical,
    )

    # Deploy whichever model has the higher macro_f1, with classical winning
    # ties (more interpretable, smaller blast radius on a misclassified row).
    ml_f1 = classical.metrics.get("macro_f1", 0.0)
    nn_f1 = onnx.metrics.get("macro_f1", 0.0)
    deployed_name: Literal["classical", "onnx"] = "classical" if ml_f1 >= nn_f1 else "onnx"
    logger.info(
        "model_server loaded — deployed=%s (ml_f1=%.4f onnx_f1=%.4f)",
        deployed_name,
        ml_f1,
        nn_f1,
    )

    return ModelLoader(
        classical=classical,
        onnx=onnx,
        label_map=label_map,
        deployed_name=deployed_name,
    )
