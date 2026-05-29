"""One-shot offline export of sentence-transformers/all-MiniLM-L6-v2 to ONNX.
Outputs minilm_l6_v2.onnx, minilm_tokenizer.json, minilm_l6_v2.sha256.
Run from /tmp/minilm_export. Verify embeddings match HF reference.
"""
import hashlib, json, sys
from pathlib import Path
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel
from optimum.onnxruntime import ORTModelForFeatureExtraction

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
OUT = Path("./out")
OUT.mkdir(exist_ok=True)

def mean_pool(last_hidden_state, attention_mask):
    mask = attention_mask.unsqueeze(-1).float()
    summed = (last_hidden_state * mask).sum(1)
    counts = mask.sum(1).clamp(min=1e-9)
    return summed / counts

print(f"[1/4] Exporting {MODEL_ID} to ONNX...")
ort_model = ORTModelForFeatureExtraction.from_pretrained(MODEL_ID, export=True)
ort_model.save_pretrained(OUT / "ort")

print("[2/4] Saving tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
tokenizer.save_pretrained(OUT / "ort")

# Find the actual onnx model file
onnx_src = next((OUT / "ort").glob("*.onnx"))
print(f"  found: {onnx_src}")

final_onnx = OUT / "minilm_l6_v2.onnx"
final_tok = OUT / "minilm_tokenizer.json"
onnx_src.rename(final_onnx)
(OUT / "ort" / "tokenizer.json").rename(final_tok)

print("[3/4] Verifying ONNX embeddings match HF reference (cosine > 0.999)...")
ref = AutoModel.from_pretrained(MODEL_ID)
ref.eval()
import onnxruntime as ort
sess = ort.InferenceSession(str(final_onnx))

probes = ["plumbing repair service", "What is your refund policy?", "Ignore all previous instructions"]
ok = True
for p in probes:
    enc = tokenizer(p, padding=True, truncation=True, max_length=128, return_tensors="pt")
    with torch.no_grad():
        ref_out = ref(**enc)
    ref_emb = mean_pool(ref_out.last_hidden_state, enc.attention_mask).numpy()[0]
    ref_emb = ref_emb / np.linalg.norm(ref_emb)

    onnx_inputs = {k: v.numpy() for k, v in enc.items()}
    onnx_outs = sess.run(None, onnx_inputs)
    last_hidden = onnx_outs[0]
    mask = enc.attention_mask.numpy()[:, :, None].astype(np.float32)
    onnx_emb = (last_hidden * mask).sum(1) / np.maximum(mask.sum(1), 1e-9)
    onnx_emb = onnx_emb[0] / np.linalg.norm(onnx_emb[0])

    cos = float(np.dot(ref_emb, onnx_emb))
    print(f"  cos(ref, onnx) = {cos:.6f}  for: {p!r}")
    if cos < 0.999:
        ok = False

if not ok:
    print("FAIL: ONNX output drift", file=sys.stderr); sys.exit(1)

print("[4/4] Writing SHA256...")
h = hashlib.sha256(final_onnx.read_bytes()).hexdigest()
(OUT / "minilm_l6_v2.sha256").write_text(h + "\n")
print(f"  sha256 = {h[:16]}...")
print(f"  size   = {final_onnx.stat().st_size / 1024 / 1024:.1f} MB")
print("DONE.")
