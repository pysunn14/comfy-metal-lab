"""Shared helpers for benchmark comparison paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ComparisonFactor
PHASES = ("cold", "warm")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_report(directory: Path) -> dict[str, Any]:
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


def provenance(report: dict[str, Any]) -> dict[str, Any]:
    environment = report.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("benchmark report is missing environment provenance")
    pre = environment.get("pre")
    if not isinstance(pre, dict):
        raise ValueError("benchmark report is missing preflight environment provenance")
    value = pre.get("provenance")
    if not isinstance(value, dict):
        raise ValueError("benchmark report is missing benchmark provenance")
    return value


def validate_same_workload(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    if baseline.get("workload") != candidate.get("workload"):
        raise ValueError("baseline and candidate workload names differ")
    if baseline.get("session_contract") != candidate.get("session_contract"):
        raise ValueError("baseline and candidate session contracts differ")
    left = provenance(baseline)
    right = provenance(candidate)
    for key in ("workload_sha256", "workflow_sha256"):
        if not left.get(key) or left.get(key) != right.get(key):
            raise ValueError(f"baseline and candidate {key} differ")


def validate_same_runtime(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    if baseline.get("runtime") != candidate.get("runtime"):
        raise ValueError("baseline and candidate runtime names differ")
    left = provenance(baseline)
    right = provenance(candidate)
    if not left.get("runtime_sha256") or left.get("runtime_sha256") != right.get("runtime_sha256"):
        raise ValueError("baseline and candidate runtime configs differ")


def validate_same_profile(baseline: dict[str, Any], candidate: dict[str, Any]) -> None:
    if baseline.get("profile") != candidate.get("profile"):
        raise ValueError("baseline and candidate profile names differ")
    left = provenance(baseline)
    right = provenance(candidate)
    if not left.get("profile_sha256") or left.get("profile_sha256") != right.get("profile_sha256"):
        raise ValueError("baseline and candidate profile configs differ")


def validate_contract(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    vary: tuple[ComparisonFactor, ...],
) -> None:
    allowed = set(vary)
    if "workload" not in allowed:
        validate_same_workload(baseline, candidate)
    if "runtime" not in allowed:
        validate_same_runtime(baseline, candidate)
    if "profile" not in allowed:
        validate_same_profile(baseline, candidate)


def sorted_sessions(report: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = report.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("benchmark report has no sessions")
    copied = [dict(session) for session in sessions if isinstance(session, dict)]
    if len(copied) != len(sessions):
        raise ValueError("benchmark sessions must be objects")
    return sorted(copied, key=lambda session: int(session["session"]))


def validate_session_indices(
    baseline_sessions: list[dict[str, Any]],
    candidate_sessions: list[dict[str, Any]],
) -> None:
    if [session["session"] for session in baseline_sessions] != [
        session["session"] for session in candidate_sessions
    ]:
        raise ValueError("baseline and candidate session indices differ")


def phase_results(sessions: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for session in sessions:
        value = session.get(phase)
        if not isinstance(value, dict):
            raise ValueError(f"benchmark session is missing {phase} result")
        results.append(dict(value))
    return results


def images(results: list[dict[str, Any]]) -> list[Path]:
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


def run_identity(report: dict[str, Any]) -> dict[str, str]:
    return {
        "workload": str(report["workload"]),
        "runtime": str(report["runtime"]),
        "profile": str(report["profile"]),
    }
