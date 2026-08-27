import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from comfy_metal.contract_compare import compare_by_contract


def _image(path: Path, value: int, *, size: tuple[int, int] = (16, 16)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.full((size[1], size[0], 3), value, dtype=np.uint8)
    Image.fromarray(pixels, mode="RGB").save(path)
    return str(path)


def _phase(
    root: Path,
    *,
    session: int,
    phase: str,
    elapsed: float,
    value: int,
    size: tuple[int, int] = (16, 16),
) -> dict:
    image_path = _image(
        root / "sessions" / f"session-{session:03d}" / f"{phase}.png",
        value,
        size=size,
    )
    return {
        "phase": phase,
        "elapsed_s": elapsed,
        "peak_memory_mb": 100.0,
        "metrics": {"output_image": image_path},
    }


def _benchmark(
    root: Path,
    *,
    workload: str,
    runtime: str = "local",
    profile: str = "stock",
    workload_sha: str | None = None,
    workflow_sha: str | None = None,
    runtime_sha: str = "runtime-sha",
    profile_sha: str | None = None,
    session_contract: dict | None = None,
    cold_seconds: list[float] | None = None,
    warm_seconds: list[float] | None = None,
    cold_values: list[int] | None = None,
    warm_values: list[int] | None = None,
    image_size: tuple[int, int] = (16, 16),
) -> Path:
    root.mkdir(parents=True)
    cold_seconds = cold_seconds or [10.0, 11.0, 9.0]
    warm_seconds = warm_seconds or [5.0, 6.0, 4.0]
    cold_values = cold_values or [0, 0, 0]
    warm_values = warm_values or [0, 0, 0]
    workload_sha = workload_sha or f"{workload}-sha"
    workflow_sha = workflow_sha or f"{workload}-workflow-sha"
    profile_sha = profile_sha or f"{profile}-sha"
    session_contract = session_contract or {
        "session": {
            "mutations": [
                {"node": "7", "input": "seed", "cold": 42, "warm": 43, "format": "value"}
            ]
        },
        "output": {"node": "9", "index": 0},
    }

    sessions = []
    for index in range(len(cold_seconds)):
        sessions.append(
            {
                "condition": profile,
                "session": index,
                "server_startup_s": 3.0,
                "cold": _phase(
                    root,
                    session=index,
                    phase="cold",
                    elapsed=cold_seconds[index],
                    value=cold_values[index],
                    size=image_size,
                ),
                "warm": _phase(
                    root,
                    session=index,
                    phase="warm",
                    elapsed=warm_seconds[index],
                    value=warm_values[index],
                    size=image_size,
                ),
            }
        )

    report = {
        "schema_version": 4,
        "status": "completed",
        "workload": workload,
        "runtime": runtime,
        "profile": profile,
        "session_contract": session_contract,
        "sessions": sessions,
        "environment": {
            "pre": {
                "provenance": {
                    "workload_sha256": workload_sha,
                    "workflow_sha256": workflow_sha,
                    "runtime_sha256": runtime_sha,
                    "profile_sha256": profile_sha,
                }
            }
        },
    }
    (root / "report.json").write_text(json.dumps(report))
    return root


def _contract(path: Path, vary: list[str], *, min_ssim: float = 0.90) -> Path:
    values = ", ".join(json.dumps(item) for item in vary)
    path.write_text(
        f'name = "contract-test"\nvary = [{values}]\n\n'
        f'[quality]\nmetric = "ssim"\nmin_ssim = {min_ssim}\n'
    )
    return path


def test_workload_comparison_allows_different_workloads_and_keeps_low_ssim_descriptive(tmp_path: Path) -> None:
    baseline = _benchmark(tmp_path / "base", workload="anima-base")
    candidate = _benchmark(
        tmp_path / "turbo",
        workload="anima-turbo",
        warm_seconds=[2.0, 2.5, 1.5],
        cold_values=[255, 255, 255],
        warm_values=[255, 255, 255],
    )
    comparison = _contract(tmp_path / "workload.toml", ["workload"])

    report = compare_by_contract(
        baseline_dir=baseline,
        candidate_dir=candidate,
        comparison_path=comparison,
        output_dir=tmp_path / "out",
    )

    assert report["contract"] == {"vary": ["workload"]}
    assert report["baseline"]["workload"] == "anima-base"
    assert report["candidate"]["workload"] == "anima-turbo"
    assert report["speedup_median"] == 5.0 / 2.0
    assert report["quality"]["role"] == "descriptive"
    assert report["quality"]["phases"]["warm"]["status"] == "available"
    assert report["quality"]["phases"]["warm"]["median_ssim"] < 0.90
    assert "valid_speedup" not in report


def test_workload_comparison_rejects_undeclared_profile_change(tmp_path: Path) -> None:
    baseline = _benchmark(tmp_path / "base", workload="base", profile="stock")
    candidate = _benchmark(tmp_path / "turbo", workload="turbo", profile="metal")
    comparison = _contract(tmp_path / "workload.toml", ["workload"])

    with pytest.raises(ValueError, match="profile"):
        compare_by_contract(
            baseline_dir=baseline,
            candidate_dir=candidate,
            comparison_path=comparison,
            output_dir=tmp_path / "out",
        )


def test_combined_contract_allows_workload_and_profile_changes(tmp_path: Path) -> None:
    baseline = _benchmark(tmp_path / "base", workload="base", profile="stock")
    candidate = _benchmark(
        tmp_path / "turbo-metal", workload="turbo", profile="metal", warm_seconds=[2.0, 2.5, 1.5]
    )
    comparison = _contract(tmp_path / "combined.toml", ["workload", "profile"])

    report = compare_by_contract(
        baseline_dir=baseline,
        candidate_dir=candidate,
        comparison_path=comparison,
        output_dir=tmp_path / "out",
    )

    assert report["contract"]["vary"] == ["workload", "profile"]
    assert report["attribution"]["scope"] == "combined"
    assert report["quality"]["role"] == "descriptive"


def test_contract_rejects_undeclared_runtime_change(tmp_path: Path) -> None:
    baseline = _benchmark(tmp_path / "base", workload="base", runtime="local")
    candidate = _benchmark(
        tmp_path / "turbo", workload="turbo", runtime="other", runtime_sha="other-runtime-sha"
    )
    comparison = _contract(tmp_path / "combined.toml", ["workload", "profile"])

    with pytest.raises(ValueError, match="runtime"):
        compare_by_contract(
            baseline_dir=baseline,
            candidate_dir=candidate,
            comparison_path=comparison,
            output_dir=tmp_path / "out",
        )


def test_profile_contract_keeps_ssim_as_correctness_gate(tmp_path: Path) -> None:
    baseline = _benchmark(
        tmp_path / "stock",
        workload="same",
        workload_sha="same-workload",
        workflow_sha="same-workflow",
        profile="stock",
    )
    candidate = _benchmark(
        tmp_path / "metal",
        workload="same",
        workload_sha="same-workload",
        workflow_sha="same-workflow",
        profile="metal",
        cold_values=[255, 255, 255],
        warm_values=[255, 255, 255],
    )
    comparison = _contract(tmp_path / "profile.toml", ["profile"])

    report = compare_by_contract(
        baseline_dir=baseline,
        candidate_dir=candidate,
        comparison_path=comparison,
        output_dir=tmp_path / "out",
    )

    assert report["quality"]["role"] == "correctness_gate"
    assert report["quality"]["passed"] is False
    assert report["valid_speedup"] is False


def test_workload_comparison_marks_ssim_not_applicable_for_different_dimensions(tmp_path: Path) -> None:
    baseline = _benchmark(tmp_path / "base", workload="base", image_size=(16, 16))
    candidate = _benchmark(tmp_path / "turbo", workload="turbo", image_size=(20, 20))
    comparison = _contract(tmp_path / "workload.toml", ["workload"])

    report = compare_by_contract(
        baseline_dir=baseline,
        candidate_dir=candidate,
        comparison_path=comparison,
        output_dir=tmp_path / "out",
    )

    assert report["quality"]["phases"]["cold"]["status"] == "not_applicable"
    assert report["quality"]["phases"]["warm"]["status"] == "not_applicable"
    assert report["status"] == "completed"
