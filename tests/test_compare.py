import json
from pathlib import Path

import numpy as np
from PIL import Image

from comfy_metal.compare import compare_benchmarks


def _image(path: Path, value: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.full((16, 16, 3), value, dtype=np.uint8)
    Image.fromarray(pixels, mode="RGB").save(path)
    return str(path)


def _phase(root: Path, *, session: int, phase: str, elapsed: float, value: int) -> dict:
    image_path = _image(root / "sessions" / f"session-{session:03d}" / f"{phase}.png", value)
    return {
        "phase": phase,
        "elapsed_s": elapsed,
        "peak_memory_mb": 100.0,
        "metrics": {"output_image": image_path},
    }


def _benchmark(
    root: Path,
    *,
    profile: str,
    runtime: str = "local",
    runtime_sha: str = "runtime-sha",
    cold_seconds: list[float],
    warm_seconds: list[float],
    cold_values: list[int],
    warm_values: list[int],
) -> Path:
    root.mkdir(parents=True)
    sessions = []
    for index in range(len(cold_seconds)):
        sessions.append(
            {
                "condition": profile,
                "session": index,
                "server_startup_s": 3.0,
                "cold": _phase(
                    root, session=index, phase="cold",
                    elapsed=cold_seconds[index], value=cold_values[index],
                ),
                "warm": _phase(
                    root, session=index, phase="warm",
                    elapsed=warm_seconds[index], value=warm_values[index],
                ),
            }
        )
    report = {
        "schema_version": 4,
        "status": "completed",
        "workload": "apple",
        "runtime": runtime,
        "profile": profile,
        "session_contract": {
            "session": {
                "mutations": [
                    {"node": "7", "input": "seed", "cold": 42, "warm": 43, "format": "value"}
                ]
            },
            "output": {"node": "9", "index": 0},
        },
        "sessions": sessions,
        "environment": {
            "pre": {
                "provenance": {
                    "workload_sha256": "workload-sha",
                    "workflow_sha256": "workflow-sha",
                    "runtime_sha256": runtime_sha,
                }
            }
        },
    }
    (root / "report.json").write_text(json.dumps(report))
    return root


def test_compare_benchmarks_reports_cold_and_warm_speedups_and_quality(tmp_path: Path) -> None:
    baseline = _benchmark(
        tmp_path / "baseline",
        profile="stock",
        cold_seconds=[12.0, 10.0, 11.0],
        warm_seconds=[6.0, 5.0, 4.0],
        cold_values=[100, 100, 100],
        warm_values=[110, 110, 110],
    )
    candidate = _benchmark(
        tmp_path / "candidate",
        profile="metal",
        cold_seconds=[9.0, 8.0, 7.0],
        warm_seconds=[3.0, 4.0, 2.0],
        cold_values=[100, 100, 100],
        warm_values=[110, 110, 110],
    )
    comparison = tmp_path / "comparison.toml"
    comparison.write_text('name = "stock-vs-metal"\n[quality]\nmetric = "ssim"\nmin_ssim = 0.90\n')
    output = tmp_path / "comparison-output"

    report = compare_benchmarks(
        baseline_dir=baseline,
        candidate_dir=candidate,
        comparison_path=comparison,
        output_dir=output,
    )

    assert report["comparison"] == "stock-vs-metal"
    assert report["primary_metric"] == "warm_generation"
    assert report["speedup"]["cold_generation"] == 11.0 / 8.0
    assert report["speedup_median"] == 5.0 / 3.0
    assert report["quality"]["passed"] is True
    assert report["quality"]["phases"]["cold"]["min_ssim"] == 1.0
    assert report["quality"]["phases"]["warm"]["min_ssim"] == 1.0
    assert report["mechanism"] == {"status": "unknown"}
    assert report["determinism"]["baseline"]["cold"]["exact_hash_match"] is True
    assert report["determinism"]["candidate"]["warm"]["exact_hash_match"] is True
    assert report["valid_speedup"] is True
    assert json.loads((output / "report.json").read_text()) == report


def test_compare_rejects_different_runtime_configs(tmp_path: Path) -> None:
    baseline = _benchmark(
        tmp_path / "baseline-runtime", profile="stock",
        cold_seconds=[10.0], warm_seconds=[5.0], cold_values=[100], warm_values=[110],
    )
    candidate = _benchmark(
        tmp_path / "candidate-runtime", profile="metal", runtime="other", runtime_sha="other-sha",
        cold_seconds=[9.0], warm_seconds=[4.0], cold_values=[100], warm_values=[110],
    )
    comparison = tmp_path / "comparison-runtime.toml"
    comparison.write_text('name = "stock-vs-metal"\n')

    import pytest
    with pytest.raises(ValueError, match="runtime"):
        compare_benchmarks(
            baseline_dir=baseline, candidate_dir=candidate, comparison_path=comparison,
            output_dir=tmp_path / "comparison-runtime-output",
        )
