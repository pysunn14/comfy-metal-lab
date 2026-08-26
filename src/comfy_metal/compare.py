"""Compare two completed cold/warm session benchmarks with SSIM gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import load_comparison
from .quality import compare_image_sets, summarize_determinism
from .report import build_session_comparison_report

_PHASES = ("cold", "warm")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_report(directory: Path) -> dict[str, Any]:
    report_path = directory / "report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"benchmark report does not exist: {report_path}")
    parsed = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"benchmark report must contain an object: {report_path}")
    if parsed.get("status") != "completed":
        raise ValueError(f"benchmark is not completed: {report_path}")
    if parsed.get("schema_version") != 4:
        raise ValueError(f"benchmark is not a cold/warm session report: {report_path}")
    return parsed


def _provenance(report: dict[str, Any]) -> dict[str, Any]:
    environment = report.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("benchmark report is missing environment provenance")
    pre = environment.get("pre")
    if not isinstance(pre, dict):
        raise ValueError("benchmark report is missing preflight environment provenance")
    provenance = pre.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("benchmark report is missing workload provenance")
    return provenance


def _validate_same_workload(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    if baseline.get("workload") != candidate.get("workload"):
        raise ValueError("baseline and candidate workload names differ")
    if baseline.get("session_contract") != candidate.get("session_contract"):
        raise ValueError("baseline and candidate session contracts differ")
    left = _provenance(baseline)
    right = _provenance(candidate)
    for key in ("workload_sha256", "workflow_sha256"):
        if not left.get(key) or left.get(key) != right.get(key):
            raise ValueError(f"baseline and candidate {key} differ")




def _validate_same_runtime(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    if baseline.get("runtime") != candidate.get("runtime"):
        raise ValueError("baseline and candidate runtime names differ")
    left = _provenance(baseline)
    right = _provenance(candidate)
    if not left.get("runtime_sha256") or left.get("runtime_sha256") != right.get("runtime_sha256"):
        raise ValueError("baseline and candidate runtime configs differ")

def _sorted_sessions(report: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = report.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("benchmark report has no sessions")
    copied = [dict(session) for session in sessions if isinstance(session, dict)]
    if len(copied) != len(sessions):
        raise ValueError("benchmark sessions must be objects")
    return sorted(copied, key=lambda session: int(session["session"]))


def _phase_results(sessions: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for session in sessions:
        value = session.get(phase)
        if not isinstance(value, dict):
            raise ValueError(f"benchmark session is missing {phase} result")
        results.append(dict(value))
    return results


def _images(results: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for result in results:
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError("generation result is missing metrics")
        value = metrics.get("output_image")
        if not isinstance(value, str) or not value:
            raise ValueError("generation result is missing output_image")
        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(f"benchmark output image does not exist: {path}")
        paths.append(path)
    return paths


def compare_benchmarks(
    *,
    baseline_dir: Path,
    candidate_dir: Path,
    comparison_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare completed session benchmark directories and write one evidence report."""

    if output_dir.exists():
        raise FileExistsError(f"comparison output directory already exists: {output_dir}")

    config = load_comparison(comparison_path)
    baseline_report = _load_report(baseline_dir)
    candidate_report = _load_report(candidate_dir)
    _validate_same_workload(baseline_report, candidate_report)
    _validate_same_runtime(baseline_report, candidate_report)

    baseline_sessions = _sorted_sessions(baseline_report)
    candidate_sessions = _sorted_sessions(candidate_report)
    if [session["session"] for session in baseline_sessions] != [
        session["session"] for session in candidate_sessions
    ]:
        raise ValueError("baseline and candidate session indices differ")

    output_dir.mkdir(parents=True)
    try:
        timing = build_session_comparison_report(
            baseline_name=str(baseline_report["profile"]),
            candidate_name=str(candidate_report["profile"]),
            baseline=baseline_sessions,
            candidate=candidate_sessions,
        )

        determinism: dict[str, dict[str, Any]] = {"baseline": {}, "candidate": {}}
        quality_phases: dict[str, Any] = {}
        for phase in _PHASES:
            baseline_images = _images(_phase_results(baseline_sessions, phase))
            candidate_images = _images(_phase_results(candidate_sessions, phase))
            determinism["baseline"][phase] = summarize_determinism(baseline_images)
            determinism["candidate"][phase] = summarize_determinism(candidate_images)
            quality_phases[phase] = compare_image_sets(
                baseline_images,
                candidate_images,
                min_ssim=config.min_ssim,
            )

        quality = {
            "metric": "ssim",
            "threshold": config.min_ssim,
            "phases": quality_phases,
            "passed": all(bool(quality_phases[phase]["passed"]) for phase in _PHASES),
        }
        report = {
            "schema_version": 4,
            "status": "completed",
            "comparison": config.name,
            "workload": baseline_report["workload"],
            "runtime": baseline_report["runtime"],
            "baseline_profile": baseline_report["profile"],
            "candidate_profile": candidate_report["profile"],
            "primary_metric": timing["primary_metric"],
            "speedup_median": timing["speedup_median"],
            "speedup": timing["speedup"],
            "timing": {
                "baseline": timing["baseline"],
                "candidate": timing["candidate"],
            },
            "determinism": determinism,
            "quality": quality,
            "mechanism": {"status": "unknown"},
            "valid_speedup": bool(quality["passed"]),
        }
        _write_json(output_dir / "report.json", report)
        return report
    except Exception as exc:
        failed = {
            "schema_version": 4,
            "status": "failed",
            "comparison": config.name,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        _write_json(output_dir / "report.json", failed)
        raise
