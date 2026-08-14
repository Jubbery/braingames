"""Crossword construction: lexicon, grid templates, fill solver, mechanical QA.

Nothing in this package talks to an LLM. Grid legality, fill, and the mechanical
checks are deterministic and testable; the model's job is theme ideation and
cluing, which lives elsewhere. Keeping that line sharp is what makes puzzle
quality something you can regression-test.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
