.PHONY: install check test lint fmt typecheck conventions kb-validate kb-index kb-stats clean

install:
	uv sync --all-packages

check: lint typecheck conventions test kb-validate

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy packages/core/src packages/kb/src

conventions:
	uv run python scripts/check_conventions.py

test:
	uv run pytest

kb-validate:
	uv run bg kb validate

kb-index:
	uv run bg kb index --stage theme_ideation

kb-stats:
	uv run bg kb stats

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
