"""Pure aggregation helpers for session-based benchmark reports."""

from __future__ import annotations

import statistics
from typing import Any, Sequence


def _timing_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        raise ValueError("timing summary requires at least one value")
    seconds = [float(value) for value in values]
    return {
        "seconds": seconds,
        "median_seconds": statistics.median(seconds),
        "min_seconds": min(seconds),
        "max_seconds": max(seconds),
    }


def summarize_sessions(sessions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Separate server startup, model-cold, model-warm, and first-image costs."""

    if not sessions:
        raise ValueError("benchmark must contain at least one session")

    startup = [float(session["server_startup_s"]) for session in sessions]
    cold = [float(session["cold"]["elapsed_s"]) for session in sessions]
    warm = [float(session["warm"]["elapsed_s"]) for session in sessions]
    first_image = [start + generation for start, generation in zip(startup, cold, strict=True)]

    cold_peaks = [session["cold"].get("peak_memory_mb") for session in sessions]
    warm_peaks = [session["warm"].get("peak_memory_mb") for session in sessions]
    available_cold = [float(value) for value in cold_peaks if value is not None]
    available_warm = [float(value) for value in warm_peaks if value is not None]

    return {
        "server_startup": _timing_summary(startup),
        "cold_generation": _timing_summary(cold),
        "warm_generation": _timing_summary(warm),
        "time_to_first_image": _timing_summary(first_image),
        "memory": {
            "cold_peak_memory_mb": cold_peaks,
            "warm_peak_memory_mb": warm_peaks,
            "max_cold_peak_memory_mb": max(available_cold) if available_cold else None,
            "max_warm_peak_memory_mb": max(available_warm) if available_warm else None,
        },
    }


def _speedup(baseline_seconds: float, candidate_seconds: float) -> float:
    if candidate_seconds <= 0:
        raise ValueError("candidate median must be greater than zero")
    return baseline_seconds / candidate_seconds


def build_session_comparison_report(
    *,
    baseline_name: str,
    candidate_name: str,
    baseline: Sequence[dict[str, Any]],
    candidate: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Compare session summaries, using model-warm generation as the primary metric."""

    baseline_summary = summarize_sessions(baseline)
    candidate_summary = summarize_sessions(candidate)
    metrics = ("cold_generation", "warm_generation", "time_to_first_image")
    speedup = {
        metric: _speedup(
            float(baseline_summary[metric]["median_seconds"]),
            float(candidate_summary[metric]["median_seconds"]),
        )
        for metric in metrics
    }
    return {
        "schema_version": 4,
        "protocol": "cold-warm-session",
        "baseline_name": baseline_name,
        "candidate_name": candidate_name,
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "speedup": speedup,
        "primary_metric": "warm_generation",
        "speedup_median": speedup["warm_generation"],
    }
