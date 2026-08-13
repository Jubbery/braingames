"""The agent knowledge base.

Knowledge lives in versioned documents under ``knowledge/``, not in string
constants in application code. This package loads, validates, routes, and
assembles them into prompts.

See docs/11-agent-knowledge-base.md for the design and docs/12-development-plan.md
Phase 1 for the build order.
"""

from .assembler import AssembledPrompt, assemble, fingerprint
from .evalkit import EvalReport, GoldenSet, Scenario, run_eval
from .loader import KnowledgeBase, KnowledgeBaseError
from .models import Defect, Doc, Frontmatter, Kind, Stage, Status, Triggers
from .router import RequiredDocsOverflowError, RouteResult, RoutingContext, route
from .tools import TOOL_DEF, ReadLog, handle_read_knowledge
from .volatile import VolatilePromptError

__all__ = [
    "TOOL_DEF",
    "AssembledPrompt",
    "Defect",
    "Doc",
    "EvalReport",
    "Frontmatter",
    "GoldenSet",
    "Kind",
    "KnowledgeBase",
    "KnowledgeBaseError",
    "ReadLog",
    "RequiredDocsOverflowError",
    "RouteResult",
    "RoutingContext",
    "Scenario",
    "Stage",
    "Status",
    "Triggers",
    "VolatilePromptError",
    "assemble",
    "fingerprint",
    "handle_read_knowledge",
    "route",
    "run_eval",
]
