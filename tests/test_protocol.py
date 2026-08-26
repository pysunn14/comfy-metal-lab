import json

import pytest

from comfy_metal.protocol import (
    RESULT_SENTINEL,
    GenerationResult,
    SessionWorkerResult,
    WorkerResult,
    parse_worker_result,
)


def test_parse_single_worker_result_ignores_regular_stdout() -> None:
    payload = WorkerResult(condition="stock", rep=1, elapsed_s=12.5, peak_memory_mb=512.0).to_dict()
    stdout = "loading model...\n" + RESULT_SENTINEL + json.dumps(payload) + "\n"

    assert parse_worker_result(stdout) == payload


def test_parse_session_worker_result_preserves_nested_phases() -> None:
    payload = SessionWorkerResult(
        condition="stock",
        session=1,
        server_startup_s=3.0,
        runtime_preflight={"required_nodes": ["KSampler"], "missing_nodes": [], "passed": True},
        cold=GenerationResult(phase="cold", elapsed_s=12.0),
        warm=GenerationResult(phase="warm", elapsed_s=5.0),
    ).to_dict()
    stdout = RESULT_SENTINEL + json.dumps(payload) + "\n"

    assert parse_worker_result(stdout) == payload
    assert payload["warm"]["phase"] == "warm"
    assert payload["runtime_preflight"]["passed"] is True


def test_parse_worker_result_rejects_missing_sentinel() -> None:
    with pytest.raises(ValueError, match="did not emit"):
        parse_worker_result("normal log only\n")
