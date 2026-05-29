# MiniLM ONNX export — reproducible pipeline

This directory holds the **runtime artifacts** for the guardrails sidecar's
topic-similarity lane (spec 010 FR-016 / FR-017). They are exported once,
offline, and committed.

## Files

| File | Size | Purpose |
|---|---|---|
| `minilm_l6_v2.onnx` | ~86 MB | FP32 ONNX export of `sentence-transformers/all-MiniLM-L6-v2` |
| `minilm_tokenizer.json` | ~700 KB | Tokenizer state (Hugging Face `tokenizers`-compatible) |
| `minilm_l6_v2.sha256` | 65 B | SHA-256 of the .onnx file — verified at sidecar startup |

## Why FP32 (not int8)

We attempted dynamic int8 quantization (would shrink to ~22 MB). Cosine
agreement with the FP32 reference dropped to ~0.96 on short noun-phrase
inputs — the topic-similarity threshold (default 0.65) cannot tolerate
that much drift in either direction. The 86 MB FP32 model preserves
cos > 0.999 on the export-verification probes and is well under GitHub's
100 MB hard cap.

## Why this lives outside the sidecar venv

`sentence-transformers` requires `torch` to load and export. The constitution
(Principle V) forbids torch / transformers / sentence-transformers in the
**runtime** image. The export pipeline runs once, offline, in a separate
venv that NEVER ships. The sidecar runtime serves the exported `.onnx` via
`onnxruntime` only.

## How to re-export

```bash
# In a scratch venv that DOES include torch — never the sidecar venv.
mkdir -p /tmp/minilm_export && cd /tmp/minilm_export
uv venv --python 3.11
.venv/bin/python -m pip install transformers torch optimum[onnxruntime]
# Paste the export.py script from `guardrails_sidecar/models/EXPORT_SCRIPT.py`
.venv/bin/python export.py
# Verify cosine agreement >= 0.999 on the export-verification probes.
# Then copy out/{*.onnx, *.json, *.sha256} into guardrails_sidecar/models/.
```

## Integrity contract

The sidecar's `core/topic_similarity.py::get_session()` computes the
SHA-256 of `minilm_l6_v2.onnx` at startup and compares it against
`minilm_l6_v2.sha256`. Mismatch raises `IntegrityError` and the sidecar
refuses to start. Same pattern as model_server's joblib + ONNX checks
in spec 007 FR-006.

If you re-export, also update `minilm_l6_v2.sha256` (the export script
writes both atomically).
