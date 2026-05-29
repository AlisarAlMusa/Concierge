"""Local ONNX sentence-embedding lane for spec 010 FR-016 / FR-017.

Loads `models/minilm_l6_v2.onnx` (FP32 export of
`sentence-transformers/all-MiniLM-L6-v2`) once at sidecar startup and
serves single-shot embeddings via `onnxruntime`. No torch, no transformers,
no sentence-transformers — the sidecar image stays constitution-V compliant.

The session is constructed once per process and attached to `app.state` by
the lifespan. The SHA-256 of the .onnx file is verified against
`models/minilm_l6_v2.sha256` before the session is created; mismatch raises
`IntegrityError` and the sidecar refuses to start (same pattern as spec 007
model_server's joblib + onnx hash check).

Usage:

    session = build_session(Path("guardrails_sidecar/models"))
    emb = embed(session, "what's your refund policy?")   # np.ndarray (384,)
    sim = cosine(emb, embed(session, "refunds"))         # float in [-1, 1]
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)

ONNX_FILENAME = "minilm_l6_v2.onnx"
SHA_FILENAME = "minilm_l6_v2.sha256"
TOKENIZER_FILENAME = "minilm_tokenizer.json"

MAX_TOKENS = 128
EMBEDDING_DIM = 384


class IntegrityError(RuntimeError):
    """Raised when the MiniLM artifact's SHA-256 does not match the committed digest."""


@dataclass(frozen=True)
class TopicEmbedder:
    """A bundled ONNX session + tokenizer ready for single-shot embedding."""

    session: ort.InferenceSession
    tokenizer: Tokenizer


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_artifact(models_dir: Path) -> Path:
    onnx_path = models_dir / ONNX_FILENAME
    sha_path = models_dir / SHA_FILENAME
    if not onnx_path.exists():
        raise IntegrityError(f"MiniLM ONNX missing: {onnx_path}")
    if not sha_path.exists():
        raise IntegrityError(f"MiniLM SHA file missing: {sha_path}")
    expected = sha_path.read_text().strip()
    actual = _sha256_of_file(onnx_path)
    if expected != actual:
        raise IntegrityError(
            f"{ONNX_FILENAME} sha256 mismatch: got {actual[:12]}…, "
            f"sha file said {expected[:12]}…"
        )
    return onnx_path


def build_embedder(models_dir: Path) -> TopicEmbedder:
    """Verify integrity, load ONNX session + tokenizer, return a bundle.

    Single call per process. Reuse the returned bundle for every embed.
    """
    onnx_path = _verify_artifact(models_dir)
    tokenizer_path = models_dir / TOKENIZER_FILENAME
    if not tokenizer_path.exists():
        raise IntegrityError(f"MiniLM tokenizer missing: {tokenizer_path}")

    session = ort.InferenceSession(
        str(onnx_path), providers=["CPUExecutionProvider"]
    )
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.enable_padding(length=MAX_TOKENS)
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    logger.info(
        "MiniLM ONNX loaded (sha=%s…, dim=%d, max_tokens=%d)",
        _sha256_of_file(onnx_path)[:12],
        EMBEDDING_DIM,
        MAX_TOKENS,
    )
    return TopicEmbedder(session=session, tokenizer=tokenizer)


def embed(embedder: TopicEmbedder, text: str) -> np.ndarray:
    """Return an L2-normalized 384-d float32 vector for one sentence.

    Mean-pools the last hidden state weighted by the attention mask, then
    L2-normalizes. Matches the pooling used by sentence-transformers' MiniLM
    serving so cosine values are directly comparable.
    """
    if not isinstance(text, str) or not text.strip():
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)

    enc = embedder.tokenizer.encode(text)
    ids = np.asarray([enc.ids], dtype=np.int64)
    mask = np.asarray([enc.attention_mask], dtype=np.int64)
    type_ids = np.zeros_like(ids, dtype=np.int64)

    outputs = embedder.session.run(
        None,
        {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": type_ids,
        },
    )
    last_hidden = outputs[0]  # (1, T, 384)
    mask_f = mask[:, :, None].astype(np.float32)
    summed = (last_hidden * mask_f).sum(axis=1)
    counts = np.maximum(mask_f.sum(axis=1), 1e-9)
    pooled = summed / counts  # (1, 384)
    vec = pooled[0]
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-9 or not np.isfinite(norm):
        # Degenerate vector — return zeros so cosine() returns 0 (no block).
        # Edge Cases: "What happens when MiniLM ONNX returns NaN or a
        # degenerate vector?" → treated as 0.0 similarity (no block).
        logger.warning("MiniLM produced degenerate embedding; returning zeros")
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    return (vec / norm).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity for two pre-normalized vectors.

    Returns 0.0 if either input is the zero vector (defensive).
    """
    if np.linalg.norm(a) == 0.0 or np.linalg.norm(b) == 0.0:
        return 0.0
    return float(np.clip(np.dot(a, b), -1.0, 1.0))
