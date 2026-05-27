"""Prompt template loader.

Prompts live versioned in backend/app/prompts/*.md. Read once per process,
cached by name. Substitution uses string.Template (${name}) so literal
`{...}` inside prompts (e.g. JSON examples) is safe.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Read a prompt file from backend/app/prompts/ and cache by name."""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **substitutions: str) -> str:
    """Load a prompt and apply ${name} substitutions; leaves unknown vars intact."""
    return Template(load_prompt(name)).safe_substitute(substitutions)
