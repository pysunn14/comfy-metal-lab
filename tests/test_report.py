import pytest

from comfy_metal.report import build_session_comparison_report, summarize_sessions


def _generation(phase: str, elapsed: float, peak: float) -> dict:
    return {
        "phase": phase,
        "elapsed_s": elapsed,
        "peak_memory_mb": peak,
        "metrics": {},
    }


def _session(index: int, startup: float, cold: float, warm: float) -> dict:
    return {
        "condition": "stock",
        "session": index,
        "server_startup_s": startup,
        "cold": _generation("cold", cold, 100.0 + index),
        "warm": _generation("warm", warm, 80.0 + index),
    }


def test_session_summary_separates_startup_cold_warm_and_first_image() -> None:
    summary = summarize_sessions([
        _session(0, 3.0, 12.0, 5.0),
        _session(1, 2.0, 10.0, 4.0),
        _session(2, 2.5, 11.0, 6.0),
    ])

    assert summary["server_startup"]["seconds"] == [3.0, 2.0, 2.5]
    assert summary["cold_generation"]["median_seconds"] == 11.0
    assert summary["warm_generation"]["median_seconds"] == 5.0
    assert summary["time_to_first_image"]["seconds"] == [15.0, 12.0, 13.5]
    assert summary["memory"]["cold_peak_memory_mb"] == [100.0, 101.0, 102.0]
    assert summary["memory"]["warm_peak_memory_mb"] == [80.0, 81.0, 82.0]


def test_session_comparison_uses_warm_as_primary_metric() -> None:
    baseline = [
        _session(0, 3.0, 12.0, 6.0),
        _session(1, 3.0, 10.0, 5.0),
        _session(2, 3.0, 11.0, 4.0),
    ]
    candidate = [
        _session(0, 3.0, 9.0, 3.0),
        _session(1, 3.0, 8.0, 4.0),
        _session(2, 3.0, 7.0, 2.0),
    ]

    report = build_session_comparison_report(
        baseline_name="stock",
        candidate_name="metal",
        baseline=baseline,
        candidate=candidate,
    )

    assert report["speedup"]["cold_generation"] == pytest.approx(11.0 / 8.0)
    assert report["speedup"]["warm_generation"] == pytest.approx(5.0 / 3.0)
    assert report["primary_metric"] == "warm_generation"
    assert report["speedup_median"] == pytest.approx(5.0 / 3.0)
