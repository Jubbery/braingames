"""The cache-invalidation scanner.

It looks for *values*, not identifiers. A code template whose body mentions
``Date.now()`` is static prompt text and perfectly cacheable. An actual
timestamp differing between two otherwise identical calls is not.
"""

from __future__ import annotations

import pytest

from braingames_kb.volatile import VolatilePromptError, assert_stable, scan


@pytest.mark.parametrize(
    "text",
    [
        "Generated at 2026-08-13T04:15:22Z",
        "Run started 2026-08-13 04:15",
        "run_id 4f2a91c0-9d3e-4b1a-8c77-1e2f3a4b5c6d",
        "epoch 1786579200",
        "epoch millis 1786579200123",
        "at 14:32:07 the fill completed",
    ],
)
def test_detects_volatile_values(text: str) -> None:
    assert scan(text)


@pytest.mark.parametrize(
    "text",
    [
        "Superseded on 2026-03-01 by the new rule.",
        "Review by 2027-03-01.",
        "Use Date.now() to seed the animation clock.",
        "const id = crypto.randomUUID();",
        "Tier 3 grids have 68-72 words.",
        "Cache reads bill at 0.1x input.",
        "Version 2.14.0 of the lexicon.",
        "A 15x15 grid has 225 squares.",
    ],
)
def test_allows_static_content(text: str) -> None:
    assert scan(text) == [], text


def test_assert_stable_passes_on_clean_text() -> None:
    assert_stable("Perfectly ordinary guidance.", block_index=0, block_name="spine")


def test_error_names_the_block_and_shows_context() -> None:
    text = "Some guidance.\nGenerated at 2026-08-13T04:15:22 for this run.\nMore guidance."
    with pytest.raises(VolatilePromptError) as exc:
        assert_stable(text, block_index=2, block_name="index")

    message = str(exc.value)
    assert "block 2" in message
    assert "index" in message
    assert "2026-08-13T04:15:22" in message
    assert "user turn" in message  # tells you where it should go instead


def test_reports_multiple_findings_in_order() -> None:
    text = "run 4f2a91c0-9d3e-4b1a-8c77-1e2f3a4b5c6d at 2026-08-13T04:15:22"
    findings = scan(text)
    assert [f.kind for f in findings] == ["uuid", "iso_datetime"]
    assert findings[0].offset < findings[1].offset


def test_finding_context_is_readable() -> None:
    text = "prefix " * 20 + "2026-08-13T04:15:22" + " suffix" * 20
    finding = scan(text)[0]
    ctx = finding.context(text)
    assert "2026-08-13T04:15:22" in ctx
    assert ctx.startswith("...")
