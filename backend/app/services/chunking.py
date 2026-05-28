"""Chunking pipeline for CMS content.

Pure deterministic function over ``(page_id, content, max_chars, overlap_chars)``.
No I/O, no async, no tokenizer dependency. See ``specs/chunking-pipeline/spec.md``
for the authoritative contract (behavior, invariants, failure handling, tests).

This module is the single place the project decides "what is one retrievable
unit of content." It does not embed, store, or query — those belong to
``CohereEmbeddingClient`` and ``RagService`` respectively.

Owner: Person B.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel

# ----- module constants (per spec §8) ---------------------------------------
DEFAULT_MAX_CHARS = 800
DEFAULT_OVERLAP_CHARS = 150

# Backward-only word-boundary search window inside _hard_split. Keeping the
# search one-sided is what guarantees `len(piece) <= max_chars`; the spec's
# stricter bound (`<= max_chars + overlap_chars`) would be violated by a
# forward-search variant that pushed the piece past max_chars.
_WORD_BOUNDARY_WINDOW = 20

_TRIPLE_NL = re.compile(r"\n{3,}")
_HORIZONTAL_WS = re.compile(r"[ \t]+")


class CmsChunk(BaseModel):
    """One retrievable unit produced by ``chunk_page``.

    The shape is small by design. ``tenant_id`` is never carried in the value;
    tenant scope is enforced by ``RagService`` at the storage and retrieval
    layers (see ``specs/rag-service/spec.md §6.1``).
    """

    page_id: UUID
    chunk_index: int
    text: str


def chunk_page(
    *,
    page_id: UUID,
    content: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[CmsChunk]:
    """Split CMS page content into bounded, paragraph-respecting chunks.

    Behavior per ``specs/chunking-pipeline/spec.md §5``:

    1. Normalize whitespace.
    2. Split on blank-line paragraph boundaries.
    3. Accumulate paragraphs into chunks of size <= ``max_chars``.
    4. Hard-split any paragraph itself larger than ``max_chars``.
    5. Prepend up to ``overlap_chars`` of the previous chunk's tail to chunks 1..N.
    6. Assign ``chunk_index`` 0, 1, 2, ... in emission order.

    Returns ``[]`` on empty or whitespace-only input. Raises ``ValueError`` for
    out-of-range bounds.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be >= 0")
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be < max_chars")

    normalized = _normalize(content)
    if not normalized:
        return []

    paragraphs = _split_paragraphs(normalized)
    if not paragraphs:
        return []

    raw_chunks = _build_chunks(paragraphs, max_chars)
    return _attach_overlap_and_index(raw_chunks, page_id, overlap_chars)


# ----- normalization --------------------------------------------------------
def _normalize(content: str) -> str:
    """Strip outer whitespace, collapse spaces/tabs per line, cap blank-line runs.

    Intra-paragraph single newlines are preserved (they remain part of the
    paragraph after blank-line splitting). Three or more consecutive newlines
    collapse to exactly one blank-line separator (``\\n\\n``).
    """
    stripped = content.strip()
    if not stripped:
        return ""
    no_triple_nl = _TRIPLE_NL.sub("\n\n", stripped)
    lines = no_triple_nl.split("\n")
    cleaned = [_HORIZONTAL_WS.sub(" ", line).strip() for line in lines]
    return "\n".join(cleaned)


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank-line boundaries and drop empty paragraphs."""
    return [p for p in text.split("\n\n") if p.strip()]


# ----- chunk assembly -------------------------------------------------------
def _build_chunks(paragraphs: list[str], max_chars: int) -> list[str]:
    """Accumulate paragraphs into chunks of size <= max_chars.

    Oversized paragraphs are flushed individually via ``_hard_split``. A chunk
    that contains multiple paragraphs joins them with ``\\n\\n`` (the paragraph
    separator), preserving the original document shape.
    """
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(paragraph, max_chars))
            continue

        if current:
            candidate = current + "\n\n" + paragraph
            if len(candidate) <= max_chars:
                current = candidate
                continue
            chunks.append(current)
        current = paragraph

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split a paragraph longer than max_chars into pieces of size <= max_chars.

    Backward-only word-boundary search within ``_WORD_BOUNDARY_WINDOW`` chars
    of the cut. If no whitespace is found in that window, falls back to a hard
    character-boundary split (acceptable, per spec §5.3).
    """
    pieces: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        # Skip leading whitespace within the paragraph (defensive — paragraphs
        # arriving here have already been normalized).
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break

        if n - i <= max_chars:
            piece = text[i:].rstrip()
            if piece:
                pieces.append(piece)
            break

        end = i + max_chars
        best = end
        for offset in range(_WORD_BOUNDARY_WINDOW + 1):
            pos = end - offset
            if pos <= i:
                break
            if text[pos].isspace():
                best = pos
                break

        piece = text[i:best].rstrip()
        if piece:
            pieces.append(piece)
        i = best

    return pieces


# ----- overlap and indexing -------------------------------------------------
def _attach_overlap_and_index(
    raw_chunks: list[str],
    page_id: UUID,
    overlap_chars: int,
) -> list[CmsChunk]:
    """Prepend ``overlap_chars`` of the previous chunk's tail to chunks 1..N.

    Chunk 0 is emitted as-is. The overlap is sliced from the *un-decorated*
    previous chunk (i.e. ``raw_chunks[i-1]``), so adding overlap to chunk i
    doesn't compound when chunk i+1 also asks for its overlap from chunk i.
    """
    result: list[CmsChunk] = []
    for i, text in enumerate(raw_chunks):
        if not text.strip():
            continue
        if i == 0 or overlap_chars == 0:
            final_text = text
        else:
            overlap = _overlap_prefix(raw_chunks[i - 1], overlap_chars)
            final_text = overlap + text if overlap else text
        result.append(CmsChunk(page_id=page_id, chunk_index=len(result), text=final_text))
    return result


def _overlap_prefix(prev_text: str, overlap_chars: int) -> str:
    """Return up to ``overlap_chars`` trailing chars of ``prev_text``.

    If the slice would start mid-word (the char immediately before the slice
    and the slice's first char are both non-whitespace), advance to the next
    whitespace boundary inside the slice. The result is at most ``overlap_chars``
    long and is always non-mid-word when a word boundary is available.
    """
    if overlap_chars <= 0 or not prev_text:
        return ""
    if len(prev_text) <= overlap_chars:
        return prev_text
    candidate = prev_text[-overlap_chars:]
    if not candidate[0].isspace():
        prev_pos = len(prev_text) - overlap_chars - 1
        if prev_pos >= 0 and not prev_text[prev_pos].isspace():
            for idx, ch in enumerate(candidate):
                if ch.isspace():
                    return candidate[idx + 1 :]
    return candidate
