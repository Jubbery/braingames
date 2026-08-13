"""Structured logging and tracing (docs/12-development-plan.md P0.3).

Two things worth building before there is anything to observe:

* **Correlation IDs.** Retrofitting these means touching every call site.
  Establishing the contextvar now means every later component inherits it for
  free.
* **A span per pipeline stage.** The generation DAG is the artifact you most
  want when a cohort's puzzles come out badly — one trace, one span per stage,
  with token counts attached. That only works if the plumbing predates the DAG.

OpenTelemetry is optional at import time. Without the SDK installed the tracing
calls become no-ops and structured logging still works, so a thin deployment or
a test run never fails for want of a collector.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# ----------------------------------------------------------------------
# Correlation context
# ----------------------------------------------------------------------

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
# Default is None rather than {} — a mutable default on a ContextVar is shared
# across every context that never sets it, and one in-place mutation would leak
# everywhere. Readers go through context_fields().
_context_fields: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "context_fields", default=None
)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def correlation_id() -> str | None:
    return _correlation_id.get()


def run_id() -> str | None:
    return _run_id.get()


def context_fields() -> dict[str, Any]:
    return dict(_context_fields.get() or {})


@contextmanager
def correlation(
    cid: str | None = None,
    *,
    run: str | None = None,
    **fields: Any,
) -> Iterator[str]:
    """Bind a correlation id (and optional run id / arbitrary fields) to this context.

    Everything logged inside carries them, and so does every cost record — which
    is what lets you pull the whole trace of one bad generation run out of the
    logs afterwards.
    """
    cid = cid or new_id("cor")
    cid_token = _correlation_id.set(cid)
    fields_token = _context_fields.set({**context_fields(), **fields})
    run_token = _run_id.set(run) if run is not None else None
    try:
        yield cid
    finally:
        if run_token is not None:
            _run_id.reset(run_token)
        _context_fields.reset(fields_token)
        _correlation_id.reset(cid_token)


# ----------------------------------------------------------------------
# Structured logging
# ----------------------------------------------------------------------

#: Attributes the stdlib puts on every record. Anything else a caller passed
#: via `extra=` is application data and belongs in the JSON output.
_STDLIB_ATTRS = frozenset(
    [
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    ]
)


_factory_installed = False


def _install_record_factory() -> None:
    """Stamp correlation context onto records when they are *created*.

    Reading the contextvars at format time looked simpler and is wrong: the
    formatter can run outside the context that produced the record — under a
    QueueHandler, in a worker thread, or in a test that captures records and
    formats them afterwards — and the correlation id silently disappears
    exactly when it is most needed.

    A record factory is the one hook that fires at creation regardless of
    handlers, propagation, or capture.
    """
    global _factory_installed
    if _factory_installed:
        return

    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.correlation_id = _correlation_id.get()
        record.run_id = _run_id.get()
        for key, value in context_fields().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


_install_record_factory()

#: Set by the record factory and rendered explicitly, so they are excluded from
#: the generic sweep of caller-supplied fields.
_CONTEXT_ATTRS = frozenset({"correlation_id", "run_id"})


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with correlation context merged in."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        if cid := getattr(record, "correlation_id", None):
            payload["correlation_id"] = cid
        if rid := getattr(record, "run_id", None):
            payload["run_id"] = rid

        for key, value in record.__dict__.items():
            if key in _STDLIB_ATTRS or key in _CONTEXT_ATTRS or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable form for local development."""

    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None)
        prefix = f"[{cid}] " if cid else ""
        base = f"{record.levelname:<7} {prefix}{record.name}: {record.getMessage()}"
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STDLIB_ATTRS
            and k not in _CONTEXT_ATTRS
            and not k.startswith("_")
            and v is not None
        }
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(*, level: str = "INFO", fmt: str | None = None) -> None:
    """Install a single stderr handler.

    Defaults to JSON, and to text when stderr is a TTY — machines get parseable
    output, humans get readable output, and neither has to pass a flag.
    """
    if fmt is None:
        fmt = "text" if sys.stderr.isatty() else "json"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # These libraries are chatty at INFO and say nothing we act on.
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ----------------------------------------------------------------------
# Tracing
# ----------------------------------------------------------------------

_tracer: Any = None
_tracing_configured = False


def configure_tracing(
    *,
    service_name: str = "braingames",
    endpoint: str | None = None,
    console: bool = False,
) -> bool:
    """Set up OTel tracing. Returns whether it actually started.

    Never raises: missing SDK, missing exporter, or an unreachable collector all
    degrade to no-op spans. Observability must not be able to take down a
    generation run.
    """
    global _tracer, _tracing_configured
    if _tracing_configured:
        return _tracer is not None

    _tracing_configured = True
    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            except ImportError:
                logging.getLogger(__name__).warning(
                    "otlp_exporter_unavailable", extra={"endpoint": endpoint}
                )
        if console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        return True
    except ImportError:
        _tracer = None
        return False


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open a span, or a no-op if tracing is not configured.

    Attributes are also attached to log records emitted inside, so a trace and
    the logs around it carry the same fields whether or not a collector exists.
    """
    if _tracer is None:
        token = _context_fields.set({**context_fields(), **attributes})
        try:
            yield None
        finally:
            _context_fields.reset(token)
        return

    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        token = _context_fields.set({**context_fields(), **attributes})
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            raise
        finally:
            _context_fields.reset(token)


def set_span_attributes(**attributes: Any) -> None:
    """Attach attributes to the current span, if there is one."""
    if _tracer is None:
        return
    try:
        from opentelemetry import trace

        current = trace.get_current_span()
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
    except ImportError:
        return


def _reset_for_tests() -> None:
    global _tracer, _tracing_configured
    _tracer = None
    _tracing_configured = False


__all__ = [
    "JsonFormatter",
    "TextFormatter",
    "configure_logging",
    "configure_tracing",
    "context_fields",
    "correlation",
    "correlation_id",
    "get_logger",
    "new_id",
    "run_id",
    "set_span_attributes",
    "span",
]
