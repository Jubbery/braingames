"""Application configuration.

Every environment read in the codebase goes through here. Nothing calls
``os.environ`` directly — a missing required setting must fail at startup with a
readable message, not at first use in the middle of a nightly generation run.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    """Walk up from this file until we find the workspace root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    return Path.cwd()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = Field(default="local", description="local | preview | staging | production")

    # --- Knowledge base -------------------------------------------------
    knowledge_dir: Path = Field(default_factory=lambda: _repo_root() / "knowledge")
    eval_dir: Path = Field(default_factory=lambda: _repo_root() / "eval")

    # --- Models ---------------------------------------------------------
    # Pinned per stage. Model choice is a deliberate, reviewable decision;
    # see docs/04-puzzle-generation.md §4.8 for the cost rationale.
    model_ideation: str = "claude-opus-5"
    model_clues: str = "claude-sonnet-5"
    model_qa: str = "claude-opus-5"
    model_theme_code: str = "claude-opus-5"
    model_classify: str = "claude-haiku-4-5"

    #: Model used to measure document token counts. Must match the model the
    #: documents are actually sent to, or the budgets are fiction.
    model_token_reference: str = "claude-opus-5"

    # --- Cost accounting ------------------------------------------------
    cost_sink: str = Field(default="jsonl", description="jsonl | postgres | null")
    cost_log_path: Path = Field(default_factory=lambda: _repo_root() / ".bg" / "llm_calls.jsonl")

    # --- Anthropic ------------------------------------------------------
    #: Optional. When unset the SDK resolves credentials from its own chain
    #: (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile),
    #: so leaving this empty is normal and not an error.
    anthropic_api_key: str | None = None

    @field_validator("knowledge_dir", "eval_dir")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v.resolve()


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
