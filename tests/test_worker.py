from __future__ import annotations

from pathlib import Path

from comfy_metal.worker import build_generation_result, build_worker_result


def _memory(peak_gib: int) -> dict:
    return {
        "available": True,
        "backend": "mps",
        "peak_allocated_bytes": peak_gib * 1024**3,
        "peak_reserved_bytes": (peak_gib + 1) * 1024**3,
        "allocated_end_bytes": (peak_gib - 1) * 1024**3,
        "reserved_end_bytes": peak_gib * 1024**3,
        "driver_allocated_end_bytes": (peak_gib + 2) * 1024**3,
        "recommended_max_bytes": 48 * 1024**3,
    }


def test_generation_result_preserves_phase_timing_and_memory(tmp_path: Path) -> None:
    result = build_generation_result(
        phase="cold",
        generation_seconds=12.5,
        prompt_id="abc",
        output_image=tmp_path / "cold.png",
        mps_memory=_memory(2),
    )

    assert result.phase == "cold"
    assert result.elapsed_s == 12.5
    assert result.peak_memory_mb == 2048.0
    assert result.metrics["mps_memory"]["measurement_scope"] == "prompt-submit-to-history-complete"
    assert result.metrics["output_image"] == str(tmp_path / "cold.png")


def test_session_worker_result_contains_cold_and_warm(tmp_path: Path) -> None:
    cold = build_generation_result(
        phase="cold", generation_seconds=12.0, prompt_id="c",
        output_image=tmp_path / "cold.png", mps_memory=_memory(2),
    )
    warm = build_generation_result(
        phase="warm", generation_seconds=5.0, prompt_id="w",
        output_image=tmp_path / "warm.png", mps_memory=_memory(3),
    )

    result = build_worker_result(
        condition="stock",
        session=2,
        server_startup_seconds=1.25,
        runtime_preflight={"required_nodes": ["KSampler"], "missing_nodes": [], "passed": True},
        cold=cold,
        warm=warm,
    )

    assert result.condition == "stock"
    assert result.session == 2
    assert result.server_startup_s == 1.25
    assert result.runtime_preflight["passed"] is True
    assert result.cold.phase == "cold"
    assert result.warm.phase == "warm"
