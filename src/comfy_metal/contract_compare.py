"""Contract-driven comparison of completed benchmark runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .comparison_common import (
    PHASES,
    images,
    load_report,
    phase_results,
    run_identity,
    sorted_sessions,
    validate_contract,
    validate_session_indices,
    write_json,
)
from .config import load_comparison_contract
from .quality import compare_image_sets, describe_image_sets, summarize_determinism
from .report import build_session_comparison_report


def compare_by_contract(
    *,
    baseline_dir: Path,
    candidate_dir: Path,
    comparison_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare benchmark runs under an explicit set of factors allowed to vary."""

    if output_dir.exists():
        raise FileExistsError(f"comparison output directory already exists: {output_dir}")

    config = load_comparison_contract(comparison_path)
    baseline_report = load_report(baseline_dir)
    candidate_report = load_report(candidate_dir)
    validate_contract(baseline_report, candidate_report, vary=config.vary)

    baseline_sessions = sorted_sessions(baseline_report)
    candidate_sessions = sorted_sessions(candidate_report)
    validate_session_indices(baseline_sessions, candidate_sessions)

    output_dir.mkdir(parents=True)
    try:
        timing = build_session_comparison_report(
            baseline_name=str(baseline_report["workload"]),
            candidate_name=str(candidate_report["workload"]),
            baseline=baseline_sessions,
            candidate=candidate_sessions,
        )

        determinism: dict[str, dict[str, Any]] = {"baseline": {}, "candidate": {}}
        quality_phases: dict[str, Any] = {}
        workload_varies = "workload" in config.vary
        for phase in PHASES:
            baseline_images = images(phase_results(baseline_sessions, phase))
            candidate_images = images(phase_results(candidate_sessions, phase))
            determinism["baseline"][phase] = summarize_determinism(baseline_images)
            determinism["candidate"][phase] = summarize_determinism(candidate_images)
            if workload_varies:
                quality_phases[phase] = describe_image_sets(baseline_images, candidate_images)
            else:
                quality_phases[phase] = compare_image_sets(
                    baseline_images,
                    candidate_images,
                    min_ssim=config.min_ssim,
                )

        if workload_varies:
            quality: dict[str, Any] = {
                "metric": "ssim",
                "role": "descriptive",
                "phases": quality_phases,
            }
        else:
            quality = {
                "metric": "ssim",
                "role": "correctness_gate",
                "threshold": config.min_ssim,
                "phases": quality_phases,
                "passed": all(bool(quality_phases[phase]["passed"]) for phase in PHASES),
            }

        report: dict[str, Any] = {
            "schema_version": 1,
            "status": "completed",
            "comparison_type": "contract",
            "comparison": config.name,
            "contract": {"vary": list(config.vary)},
            "baseline": run_identity(baseline_report),
            "candidate": run_identity(candidate_report),
            "primary_metric": timing["primary_metric"],
            "speedup_median": timing["speedup_median"],
            "speedup": timing["speedup"],
            "timing": {
                "baseline": timing["baseline"],
                "candidate": timing["candidate"],
            },
            "determinism": determinism,
            "quality": quality,
        }
        if len(config.vary) > 1:
            report["attribution"] = {
                "scope": "combined",
                "message": "Multiple factors may vary; this pair measures the combined stack effect.",
            }
        if not workload_varies:
            report["valid_speedup"] = bool(quality["passed"])

        write_json(output_dir / "report.json", report)
        return report
    except Exception as exc:
        failed = {
            "schema_version": 1,
            "status": "failed",
            "comparison_type": "contract",
            "comparison": config.name,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        write_json(output_dir / "report.json", failed)
        raise
