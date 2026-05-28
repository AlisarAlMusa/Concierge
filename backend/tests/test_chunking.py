"""Unit tests for the chunking pipeline.

Pure deterministic function. No I/O. Validates every invariant from
``specs/chunking-pipeline/spec.md §6`` and every planned scenario from §9
before any embedding or RAG-storage code lands.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.services.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP_CHARS,
    chunk_page,
)

PAGE_ID = UUID("00000000-0000-0000-0000-000000000001")


# ----- empty and whitespace inputs ------------------------------------------
def test_empty_content_returns_empty():
    assert chunk_page(page_id=PAGE_ID, content="") == []


def test_whitespace_only_returns_empty():
    assert chunk_page(page_id=PAGE_ID, content="   \n\n\t  \n") == []


# ----- happy paths ----------------------------------------------------------
def test_single_short_paragraph_one_chunk():
    chunks = chunk_page(page_id=PAGE_ID, content="Hello world.")
    assert len(chunks) == 1
    assert chunks[0].text == "Hello world."
    assert chunks[0].chunk_index == 0
    assert chunks[0].page_id == PAGE_ID


def test_short_paragraphs_combine_into_one_chunk():
    content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_page(page_id=PAGE_ID, content=content)
    assert len(chunks) == 1
    text = chunks[0].text
    assert "First paragraph." in text
    assert "Second paragraph." in text
    assert "Third paragraph." in text


def test_normalization_collapses_extra_whitespace():
    """Multiple spaces/tabs collapse to one; 3+ blank lines collapse to one."""
    content = "Para   A\twith\t\ttabs.\n\n\n\nPara B."
    chunks = chunk_page(page_id=PAGE_ID, content=content)
    assert len(chunks) == 1
    text = chunks[0].text
    assert "Para A with tabs." in text
    assert "Para B." in text
    assert "\n\n\n" not in text  # no run of 3+ newlines survives normalization


# ----- multi-chunk behavior -------------------------------------------------
def test_long_content_produces_multiple_chunks():
    paragraphs = ["a" * 150, "b" * 150, "c" * 150, "d" * 150, "e" * 150]
    content = "\n\n".join(paragraphs)
    chunks = chunk_page(page_id=PAGE_ID, content=content, max_chars=300, overlap_chars=50)
    assert len(chunks) >= 2


def test_chunks_respect_max_plus_overlap_bound():
    """Spec invariant §6.3: len(text) <= max_chars + overlap_chars on every chunk."""
    paragraphs = ["x" * 200] * 8
    content = "\n\n".join(paragraphs)
    max_chars, overlap_chars = 400, 75
    chunks = chunk_page(
        page_id=PAGE_ID, content=content, max_chars=max_chars, overlap_chars=overlap_chars
    )
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.text) <= max_chars + overlap_chars, (
            f"chunk {c.chunk_index} length {len(c.text)} "
            f"exceeds bound {max_chars + overlap_chars}"
        )


def test_overlap_present_between_adjacent_chunks():
    """Chunk i (i>0) starts with content drawn from chunk i-1's tail."""
    # Paragraphs without spaces → word-boundary trim never kicks in,
    # so the overlap is exactly the previous chunk's last overlap_chars.
    paragraphs = ["a" * 200, "b" * 200, "c" * 200, "d" * 200]
    content = "\n\n".join(paragraphs)
    max_chars, overlap_chars = 350, 50
    chunks = chunk_page(
        page_id=PAGE_ID, content=content, max_chars=max_chars, overlap_chars=overlap_chars
    )
    assert len(chunks) >= 2
    for i in range(1, len(chunks)):
        prev = chunks[i - 1].text
        expected = prev[-overlap_chars:] if len(prev) >= overlap_chars else prev
        assert chunks[i].text.startswith(expected), (
            f"chunk {i} should start with chunk {i - 1}'s last "
            f"{len(expected)} chars (got prefix {chunks[i].text[:overlap_chars]!r})"
        )


def test_zero_overlap_no_prefix_added():
    """overlap_chars=0 disables overlap stitching entirely."""
    paragraphs = ["a" * 200, "b" * 200]
    content = "\n\n".join(paragraphs)
    chunks = chunk_page(page_id=PAGE_ID, content=content, max_chars=300, overlap_chars=0)
    assert len(chunks) >= 2
    assert chunks[1].text.startswith("b")  # no "a" prefix


# ----- oversized paragraph hard-split ---------------------------------------
def test_oversized_paragraph_is_hard_split():
    """A paragraph longer than max_chars is split into pieces, never preserved whole."""
    content = "a" * 1500  # no spaces → forces hard character-boundary split
    chunks = chunk_page(page_id=PAGE_ID, content=content, max_chars=400, overlap_chars=50)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c.text) <= 400 + 50


def test_hard_split_prefers_word_boundary_when_available():
    """If whitespace is within the backward search window, split at the space."""
    # "word " repeats every 5 chars. With max_chars=100, end falls at position 100;
    # the nearest preceding space is at position 99 (offset 1). Split lands there.
    paragraph = "word " * 100  # 500 chars
    chunks = chunk_page(page_id=PAGE_ID, content=paragraph, max_chars=100, overlap_chars=20)
    assert len(chunks) >= 2
    # The first chunk must end with a complete "word", not a partial one.
    assert chunks[0].text.endswith("word"), f"first chunk ended mid-word: {chunks[0].text[-10:]!r}"


# ----- indexing and identity ------------------------------------------------
def test_chunk_index_is_sequential_and_zero_based():
    """Spec invariant §6.5: chunk_index = 0..N-1, contiguous, no gaps."""
    content = "x" * 500 + "\n\n" + "y" * 500 + "\n\n" + "z" * 500
    chunks = chunk_page(page_id=PAGE_ID, content=content, max_chars=400, overlap_chars=50)
    assert len(chunks) >= 2
    for i, c in enumerate(chunks):
        assert c.chunk_index == i


def test_chunks_carry_the_supplied_page_id():
    """Spec invariant §6.6: no identity in the value beyond page_id."""
    content = "x" * 200 + "\n\n" + "y" * 200
    chunks = chunk_page(page_id=PAGE_ID, content=content)
    for c in chunks:
        assert c.page_id == PAGE_ID


# ----- determinism ----------------------------------------------------------
def test_chunking_is_deterministic_across_calls():
    """Spec invariant §6.1: identical inputs yield identical outputs every call."""
    content = ("This is a paragraph. " * 20 + "\n\n") * 5
    a = chunk_page(page_id=PAGE_ID, content=content, max_chars=400, overlap_chars=80)
    b = chunk_page(page_id=PAGE_ID, content=content, max_chars=400, overlap_chars=80)
    assert a == b


def test_chunking_is_independent_of_page_id_for_text_and_index():
    """Different page_id changes only the page_id field, not text or chunk_index."""
    content = ("This is a paragraph. " * 20 + "\n\n") * 5
    a = chunk_page(page_id=PAGE_ID, content=content, max_chars=400, overlap_chars=80)
    other = UUID("00000000-0000-0000-0000-000000000002")
    c = chunk_page(page_id=other, content=content, max_chars=400, overlap_chars=80)
    assert [(x.chunk_index, x.text) for x in a] == [(x.chunk_index, x.text) for x in c]
    assert all(x.page_id == other for x in c)


# ----- input validation -----------------------------------------------------
def test_invalid_max_chars_raises():
    with pytest.raises(ValueError):
        chunk_page(page_id=PAGE_ID, content="x", max_chars=0)
    with pytest.raises(ValueError):
        chunk_page(page_id=PAGE_ID, content="x", max_chars=-10)


def test_invalid_overlap_chars_raises():
    with pytest.raises(ValueError):
        chunk_page(page_id=PAGE_ID, content="x", max_chars=100, overlap_chars=-1)


def test_overlap_must_be_strictly_less_than_max_raises():
    with pytest.raises(ValueError):
        chunk_page(page_id=PAGE_ID, content="x", max_chars=100, overlap_chars=100)
    with pytest.raises(ValueError):
        chunk_page(page_id=PAGE_ID, content="x", max_chars=100, overlap_chars=150)


# ----- spec defaults are stable --------------------------------------------
def test_module_defaults_match_spec():
    """Spec §8: DEFAULT_MAX_CHARS=800, DEFAULT_OVERLAP_CHARS=150."""
    assert DEFAULT_MAX_CHARS == 800
    assert DEFAULT_OVERLAP_CHARS == 150
