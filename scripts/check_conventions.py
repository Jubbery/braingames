#!/usr/bin/env python3
"""Enforce the conventions from docs/12-development-plan.md §12.2.

Discipline does not survive month three. These are the rules that keep the
knowledge base from being quietly bypassed, expressed as a check that fails CI
rather than as a paragraph nobody rereads.

    1. No prompt text in application code.
    2. Every model call goes through the metered wrapper.

Run:  uv run python scripts/check_conventions.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = [REPO / "packages" / "core" / "src", REPO / "packages" / "kb" / "src"]

#: A string literal longer than this in application code is almost certainly a
#: prompt. Prompts belong in knowledge/ as versioned documents.
MAX_STRING_LITERAL = 400

#: Files allowed to import the Anthropic SDK directly.
SDK_IMPORT_ALLOWLIST = {"braingames_core/llm.py"}

#: Files allowed long literals, with the reason. Each entry is a deliberate
#: exception, not a blanket carve-out.
LONG_LITERAL_ALLOWLIST = {
    # Structural scaffolding for the index block, not guidance content.
    "braingames_kb/assembler.py",
    # Tool description: it is an API contract sent with the schema, and it has
    # to travel with the tool definition rather than living in a document.
    "braingames_kb/tools.py",
}


@dataclass
class Violation:
    path: Path
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        rel = self.path.relative_to(REPO)
        return f"{rel}:{self.line}  [{self.rule}] {self.detail}"


def rel_key(path: Path) -> str:
    for root in SOURCE_ROOTS:
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return path.name


def folded_length(node: ast.AST) -> int:
    """Length of a string expression after folding literal concatenation.

    ``"..." * 40`` and ``"a" + "b" + ...`` are each short at the AST level but
    long at runtime. Without folding, the rule is trivially sidestepped by
    accident as much as by intent.
    """
    if isinstance(node, ast.Constant):
        return len(node.value) if isinstance(node.value, str) else 0
    if isinstance(node, ast.JoinedStr):  # f-string
        return sum(folded_length(v) for v in node.values)
    if isinstance(node, ast.BinOp):
        left, right = node.left, node.right
        if isinstance(node.op, ast.Add):
            return folded_length(left) + folded_length(right)
        if isinstance(node.op, ast.Mult):
            for text_side, count_side in ((left, right), (right, left)):
                size = folded_length(text_side)
                if (
                    size
                    and isinstance(count_side, ast.Constant)
                    and isinstance(count_side.value, int)
                ):
                    return size * max(count_side.value, 0)
            return 0
    return 0


def docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of Constant nodes that are docstrings.

    Documentation is not prompt content — explaining why the code is shaped the
    way it is should never be discouraged by this check.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found


def check_file(path: Path) -> list[Violation]:
    key = rel_key(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, "syntax", str(exc))]

    out: list[Violation] = []
    docstrings = docstring_nodes(tree)

    seen_lines: set[int] = set()

    for node in ast.walk(tree):
        # Rule 1 — no prompt text in application code.
        is_string_expr = isinstance(node, ast.Constant | ast.BinOp | ast.JoinedStr)
        if (
            is_string_expr
            and id(node) not in docstrings
            and key not in LONG_LITERAL_ALLOWLIST
            and getattr(node, "lineno", None) not in seen_lines
        ):
            size = folded_length(node)
            if size > MAX_STRING_LITERAL:
                seen_lines.add(node.lineno)
                out.append(
                    Violation(
                        path,
                        node.lineno,
                        "no-prompts-in-code",
                        f"String expression of {size} chars. Prompt content belongs in "
                        f"knowledge/ as a versioned document — see docs/11 §11.1.",
                    )
                )

        # Rule 2 — the SDK is reached through the metered wrapper only.
        if key not in SDK_IMPORT_ALLOWLIST:
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]

            if any(name == "anthropic" or name.startswith("anthropic.") for name in imported):
                out.append(
                    Violation(
                        path,
                        node.lineno,
                        "metered-calls-only",
                        "Direct Anthropic SDK import. Use braingames_core.llm.LLMClient so the "
                        "call is metered and schema-constrained — see docs/12 §12.2.",
                    )
                )

    return out


def main() -> int:
    violations: list[Violation] = []
    for root in SOURCE_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            violations.extend(check_file(path))

    if violations:
        print(f"{len(violations)} convention violation(s):\n", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nThese are the rules that keep the knowledge base from being bypassed.\n"
            "If an exception is genuinely warranted, add it to the allowlist in this\n"
            "script with a comment saying why.",
            file=sys.stderr,
        )
        return 1

    print("Conventions OK: no prompt literals in code, no direct SDK imports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
