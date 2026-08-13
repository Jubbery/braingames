"""Structured logging and correlation context.

Correlation IDs are the thing you cannot retrofit cheaply — every call site
would need touching. These tests pin the contract that later components will
depend on.
"""

from __future__ import annotations

import json
import logging

import pytest

from braingames_core import obs


@pytest.fixture(autouse=True)
def _clean_tracing():
    obs._reset_for_tests()
    yield
    obs._reset_for_tests()


def _capture(caplog, fn):
    formatter = obs.JsonFormatter()
    with caplog.at_level(logging.INFO):
        fn()
    return [json.loads(formatter.format(r)) for r in caplog.records]


def test_correlation_id_appears_in_log_records(caplog) -> None:
    log = obs.get_logger("test.corr")

    def emit():
        with obs.correlation("cor-fixed"):
            log.info("something_happened")

    (payload,) = _capture(caplog, emit)
    assert payload["correlation_id"] == "cor-fixed"
    assert payload["msg"] == "something_happened"


def test_run_id_and_arbitrary_fields_propagate(caplog) -> None:
    log = obs.get_logger("test.fields")

    def emit():
        with obs.correlation(run="run-7", cohort_id="0193", tier=2):
            log.info("stage_started")

    (payload,) = _capture(caplog, emit)
    assert payload["run_id"] == "run-7"
    assert payload["cohort_id"] == "0193"
    assert payload["tier"] == 2


def test_extra_fields_survive_as_structured_data(caplog) -> None:
    """`extra=` is application data and must not be flattened into the message."""
    log = obs.get_logger("test.extra")

    def emit():
        log.info("llm_call", extra={"stage": "clue_writing", "input_tokens": 4200})

    (payload,) = _capture(caplog, emit)
    assert payload["stage"] == "clue_writing"
    assert payload["input_tokens"] == 4200


def test_context_is_restored_on_exit() -> None:
    assert obs.correlation_id() is None
    with obs.correlation("cor-outer"):
        assert obs.correlation_id() == "cor-outer"
        with obs.correlation("cor-inner"):
            assert obs.correlation_id() == "cor-inner"
        assert obs.correlation_id() == "cor-outer"
    assert obs.correlation_id() is None


def test_context_is_restored_after_an_exception() -> None:
    with pytest.raises(ValueError), obs.correlation("cor-boom"):
        raise ValueError("boom")
    assert obs.correlation_id() is None
    assert obs.context_fields() == {}


def test_json_formatter_emits_one_parseable_object() -> None:
    record = logging.LogRecord("n", logging.INFO, "p", 1, "msg %s", ("x",), None)
    payload = json.loads(obs.JsonFormatter().format(record))
    assert payload["msg"] == "msg x"
    assert payload["level"] == "INFO"
    assert payload["ts"].endswith("Z")


def test_json_formatter_survives_unserializable_values() -> None:
    """A log call must never be the thing that crashes a generation run."""
    record = logging.LogRecord("n", logging.INFO, "p", 1, "m", (), None)
    record.weird = object()  # type: ignore[attr-defined]
    payload = json.loads(obs.JsonFormatter().format(record))
    assert "weird" in payload


def test_spans_are_noops_without_tracing() -> None:
    with obs.span("generate.theme_ideation", stage="theme_ideation") as current:
        assert current is None


def test_span_attributes_become_log_fields_without_a_collector(caplog) -> None:
    """Attributes stay useful even when no tracer is configured."""
    log = obs.get_logger("test.span")

    def emit():
        with obs.span("generate.fill", stage="fill", tier=3):
            log.info("fill_started")

    (payload,) = _capture(caplog, emit)
    assert payload["stage"] == "fill"
    assert payload["tier"] == 3


def test_span_fields_do_not_leak_after_exit(caplog) -> None:
    log = obs.get_logger("test.leak")

    def emit():
        with obs.span("inner", tier=3):
            pass
        log.info("after")

    (payload,) = _capture(caplog, emit)
    assert "tier" not in payload


def test_configure_tracing_is_idempotent_and_never_raises() -> None:
    first = obs.configure_tracing(service_name="test")
    second = obs.configure_tracing(service_name="test")
    assert first == second


def test_set_span_attributes_is_safe_without_a_tracer() -> None:
    obs.set_span_attributes(input_tokens=10)  # must not raise


def test_new_id_is_prefixed_and_unique() -> None:
    ids = {obs.new_id("run") for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("run-") for i in ids)
