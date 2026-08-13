"""Frontmatter parsing for the two document flavours.

Markdown documents use the usual ``---`` fence. Code templates keep their
native extension so they stay lintable, compilable, and diffable as real source
files, and carry frontmatter in a leading block comment instead. A template you
cannot typecheck is a template that rots.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MD_FENCE = "---"
CODE_OPEN = "/*---"
CODE_CLOSE = "---*/"

#: Extension -> (fence style, language hint for prompt rendering)
SUPPORTED: dict[str, tuple[str, str]] = {
    ".md": ("md", "markdown"),
    ".ts": ("code", "typescript"),
    ".tsx": ("code", "tsx"),
    ".css": ("code", "css"),
}


class FrontmatterError(ValueError):
    """Malformed or missing frontmatter."""


@dataclass(frozen=True)
class ParsedFile:
    meta: dict[str, Any]
    body: str
    lang: str


def parse(text: str, suffix: str) -> ParsedFile:
    try:
        style, lang = SUPPORTED[suffix]
    except KeyError:
        raise FrontmatterError(
            f"Unsupported knowledge file type {suffix!r} (expected one of {sorted(SUPPORTED)})"
        ) from None

    raw, body = _split(text, style)
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"Frontmatter is not valid YAML: {exc}") from exc

    if not isinstance(loaded, dict):
        raise FrontmatterError("Frontmatter must be a YAML mapping")
    return ParsedFile(meta=loaded, body=body.strip("\n"), lang=lang)


def _split(text: str, style: str) -> tuple[str, str]:
    stripped = text.lstrip("﻿")
    if style == "md":
        if not stripped.startswith(MD_FENCE):
            raise FrontmatterError(
                "Missing frontmatter. Markdown documents must open with a '---' fence."
            )
        parts = stripped.split(MD_FENCE, 2)
        if len(parts) < 3:
            raise FrontmatterError("Unterminated frontmatter — no closing '---' fence found.")
        return parts[1], parts[2]

    if not stripped.startswith(CODE_OPEN):
        raise FrontmatterError(
            f"Missing frontmatter. Code templates must open with a '{CODE_OPEN}' comment block."
        )
    end = stripped.find(CODE_CLOSE)
    if end == -1:
        raise FrontmatterError(f"Unterminated frontmatter — no closing '{CODE_CLOSE}' found.")
    return stripped[len(CODE_OPEN) : end], stripped[end + len(CODE_CLOSE) :]


def render(meta: dict[str, Any], body: str, suffix: str) -> str:
    """Serialize back to disk. Used by `bg kb tokens` when updating counts."""
    style, _ = SUPPORTED[suffix]
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=100).rstrip("\n")
    if style == "md":
        return f"{MD_FENCE}\n{front}\n{MD_FENCE}\n\n{body.strip()}\n"
    return f"{CODE_OPEN}\n{front}\n{CODE_CLOSE}\n\n{body.strip()}\n"


def read(path: Path) -> ParsedFile:
    return parse(path.read_text(encoding="utf-8"), path.suffix)


__all__ = ["SUPPORTED", "FrontmatterError", "ParsedFile", "parse", "read", "render"]
