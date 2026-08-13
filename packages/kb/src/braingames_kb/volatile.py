"""Detect volatile content above a cache breakpoint.

Prompt caching is a *prefix match*: one interpolated timestamp in the spine
invalidates every block after it, on every call, forever. The failure is silent
— you get no error, just a bill and a ``cache_read_input_tokens`` of zero.

So the assembler refuses to build a prompt containing a runtime-varying literal
above the breakpoint. This scanner is what makes that refusal possible.

It looks for *values*, not identifiers. A code template whose body contains the
text ``Date.now()`` is fine — that is static prompt text. What is not fine is an
actual timestamp that differs between two otherwise identical calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ISO 8601 datetimes. Bare dates (2026-08-13) are allowed — documents
#: legitimately cite them — but anything carrying a clock time is a red flag.
ISO_DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?\b")

UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

#: 10-digit (seconds) and 13-digit (milliseconds) Unix epochs in a plausible range.
EPOCH = re.compile(r"\b1[6-9]\d{8}(?:\d{3})?\b")

#: Wall-clock times like "14:32:07" that are not part of a date.
BARE_TIME = re.compile(r"(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?![\d-])")

PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("iso_datetime", ISO_DATETIME, "an ISO timestamp"),
    ("uuid", UUID, "a UUID (run id? request id?)"),
    ("epoch", EPOCH, "a Unix epoch timestamp"),
    ("bare_time", BARE_TIME, "a wall-clock time"),
]


@dataclass(frozen=True)
class VolatileFinding:
    kind: str
    matched: str
    description: str
    offset: int

    def context(self, text: str, width: int = 60) -> str:
        start = max(0, self.offset - width // 2)
        end = min(len(text), self.offset + len(self.matched) + width // 2)
        snippet = text[start:end].replace("\n", "⏎")
        return f"...{snippet}..."


class VolatilePromptError(RuntimeError):
    """A cache-invalidating literal was found above the cache breakpoint."""


def scan(text: str) -> list[VolatileFinding]:
    """Find volatile literals, reporting each span once.

    Patterns deliberately overlap — the time inside ``2026-08-13T04:15:22``
    matches both ISO_DATETIME and BARE_TIME. Reporting it twice would make the
    error message noisier without saying anything more, so overlapping matches
    collapse to the longest one starting earliest.
    """
    candidates: list[tuple[int, int, str, str, str]] = []
    for kind, pattern, desc in PATTERNS:
        for m in pattern.finditer(text):
            candidates.append((m.start(), m.end(), kind, m.group(0), desc))

    # Earliest start first; on a tie, the longest match wins.
    candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

    findings: list[VolatileFinding] = []
    claimed_until = -1
    for start, end, kind, matched, desc in candidates:
        if start < claimed_until:
            continue
        findings.append(VolatileFinding(kind=kind, matched=matched, description=desc, offset=start))
        claimed_until = end
    return findings


def assert_stable(text: str, *, block_index: int, block_name: str) -> None:
    """Raise if ``text`` contains anything that varies between identical calls."""
    findings = scan(text)
    if not findings:
        return

    lines = [
        f"Cache-invalidating content in system block {block_index} ({block_name}).",
        "",
        "Everything above the cache breakpoint must be byte-identical across calls.",
        "Move volatile values into the user turn, below the breakpoint.",
        "",
    ]
    for f in findings[:8]:
        lines.append(f"  • {f.description}: {f.matched!r}")
        lines.append(f"    {f.context(text)}")
    if len(findings) > 8:
        lines.append(f"  ... and {len(findings) - 8} more")
    raise VolatilePromptError("\n".join(lines))


__all__ = ["VolatileFinding", "VolatilePromptError", "assert_stable", "scan"]
