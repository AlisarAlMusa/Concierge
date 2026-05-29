"""NeMo Guardrails custom Python action for dynamic tenant topic blocking.

This is the bridge between NeMo's static Colang flows and our per-tenant
JSONB config. Colang can call `execute check_blocked_topics(...)` and we
return a bool that the flow uses to refuse the message.

Registered with the LLMRails engine at startup (see
`app.core.nemo_engine.build_rails_engine`).

Spec 010 FR-015 / FR-016. Threshold default 0.65 — note the smoke-test
observation that all-MiniLM-L6-v2 paraphrase similarity for short topic
labels sits in the 0.4–0.6 range, so the topic-probes eval set will likely
tune this down. The env override `GUARDRAILS_TOPIC_SIM_THRESHOLD` exists
exactly for this.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.topic_similarity import TopicEmbedder, cosine, embed

logger = logging.getLogger(__name__)

# Tuned 2026-05-29 against a small probe set. `all-MiniLM-L6-v2`'s cosine
# distribution for paraphrase pairs of short topic labels (e.g. "compare
# competitors") sits in the 0.55–0.65 band; the original brief's 0.65 missed
# legitimate paraphrases. The topic-probes eval set will tune this further.
DEFAULT_THRESHOLD = 0.55


def _threshold() -> float:
    raw = os.environ.get("GUARDRAILS_TOPIC_SIM_THRESHOLD", "")
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "GUARDRAILS_TOPIC_SIM_THRESHOLD=%r is not a float; using default %.2f",
            raw,
            DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD


# Module-level singleton — set by the lifespan after the embedder is built.
# We use a plain global rather than a NeMo context variable because the action
# is called from inside Colang where access to FastAPI app.state is awkward.
_EMBEDDER: TopicEmbedder | None = None


def set_embedder(embedder: TopicEmbedder) -> None:
    """Called once during lifespan startup."""
    global _EMBEDDER
    _EMBEDDER = embedder


def get_embedder() -> TopicEmbedder:
    if _EMBEDDER is None:
        raise RuntimeError(
            "TopicEmbedder not initialised — call set_embedder() during lifespan"
        )
    return _EMBEDDER


@lru_cache(maxsize=512)
def _cached_topic_vector(topic: str) -> np.ndarray:
    """LRU-cache topic embeddings keyed by the topic string.

    Topic strings are short, FR-023 caps them at 30 chars × 10 per tenant.
    Steady-state: one embed per check_input (the user message only).
    """
    return embed(get_embedder(), topic)


def _evaluate(user_text: str, blocked_topics: list[str]) -> tuple[bool, float, str]:
    """Sync core of the check. Returns (is_blocked, top_similarity, top_topic).

    Splitting the sync compute out makes both the async wrapper and unit
    tests trivial.
    """
    if not blocked_topics:
        return False, 0.0, ""
    threshold = _threshold()
    user_vec = embed(get_embedder(), user_text)
    top_sim = -1.0
    top_topic = ""
    for topic in blocked_topics:
        if not isinstance(topic, str) or not topic.strip():
            continue
        topic_vec = _cached_topic_vector(topic.strip())
        sim = cosine(user_vec, topic_vec)
        if sim > top_sim:
            top_sim = sim
            top_topic = topic.strip()
        if sim >= threshold:
            return True, sim, topic.strip()
    return False, max(top_sim, 0.0), top_topic


async def check_blocked_topics(
    user_text: str,
    blocked_topics: list[str] | None = None,
    **_: Any,
) -> bool:
    """NeMo custom action: True ⇒ block the message.

    Signature matches the Colang `execute check_blocked_topics(...)` call.
    Empty / missing blocked_topics ⇒ pass through.
    """
    if not blocked_topics:
        return False
    # CPU-bound embedding wrapped so we don't stall the event loop on a
    # 5–10 ms compute (FR-IV).
    is_blocked, sim, topic = await asyncio.to_thread(
        _evaluate, user_text, list(blocked_topics)
    )
    if is_blocked:
        logger.info(
            "tenant topic blocked: topic=%r similarity=%.3f", topic, sim
        )
    return is_blocked
