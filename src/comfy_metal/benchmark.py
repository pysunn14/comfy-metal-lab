"""Cold/warm session benchmark orchestration with environment and swap evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import load_profile, load_runtime, load_workload
from .environment import collect_environment_snapshot
from .report import summarize_sessions
from .run import run_session
from .telemetry import MacSwapWatcher

ProgressCallback = Callable[[str], None]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _environment(
    *,
    comfyui_root: Path,
    workload_path: Path,
    workflow_path: Path,
    runtime_path: Path,
    profile_path: Path,
) -> dict[str, Any]:
    return collect_environment_snapshot(
        comfyui_root=comfyui_root,
        workload_path=workload_path,
        workflow_path=workflow_path,
        runtime_path=runtime_path,
        profile_path=profile_path,
    )


def run_benchmark(
    *,
    comfyui_root: Path,
    workload_path: Path,
    runtime_path: Path,
    profile_path: Path,
    output_dir: Path,
    sessions: int = 3,
    swap_interval_seconds: float = 1.0,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run fresh sessions, measuring one model-cold and one model-warm prompt each."""

    if sessions < 1:
        raise ValueError("sessions must be positive")
    if output_dir.exists():
        raise FileExistsError(f"benchmark output directory already exists: {output_dir}")

    workload = load_workload(workload_path)
    workload.require_session()
    workload.require_output()
    session_contract = workload.benchmark_contract()
    runtime = load_runtime(runtime_path)
    profile = load_profile(profile_path)
    output_dir.mkdir(parents=True)

    emit = progress or (lambda _message: None)
    emit("preflight: capturing environment")
    pre_environment = _environment(
        comfyui_root=comfyui_root,
        workload_path=workload_path,
        workflow_path=workload.workflow,
        runtime_path=runtime_path,
        profile_path=profile_path,
    )
    _write_json(output_dir / "environment.pre.json", pre_environment)

    session_results: list[dict[str, Any]] = []
    telemetry: list[dict[str, Any]] = []

    try:
        for session_index in range(sessions):
            emit(
                f"session {session_index + 1}/{sessions}: "
                "fresh worker + fresh ComfyUI, then cold + warm prompts"
            )
            swap_path = output_dir / "telemetry" / f"swap-session-{session_index:03d}.jsonl"
            watcher = MacSwapWatcher(swap_path, interval_seconds=swap_interval_seconds)
            watcher.start()
            try:
                session_report = run_session(
                    comfyui_root=comfyui_root,
                    workload_path=workload_path,
                    runtime_path=runtime_path,
                    profile_path=profile_path,
                    output_dir=output_dir / "sessions" / f"session-{session_index:03d}",
                    session=session_index,
                )
            finally:
                swap_summary = watcher.stop()
                telemetry.append(swap_summary)
                _write_json(
                    output_dir / "telemetry" / f"swap-session-{session_index:03d}.summary.json",
                    swap_summary,
                )

            result = dict(session_report["session"])
            session_results.append(result)
            emit(
                f"session {session_index + 1}/{sessions}: "
                f"cold {float(result['cold']['elapsed_s']):.3f}s, "
                f"warm {float(result['warm']['elapsed_s']):.3f}s"
            )
    except Exception as exc:
        post_environment = _environment(
            comfyui_root=comfyui_root,
            workload_path=workload_path,
            workflow_path=workload.workflow,
            runtime_path=runtime_path,
            profile_path=profile_path,
        )
        _write_json(output_dir / "environment.post.json", post_environment)
        failed = {
            "schema_version": 4,
            "status": "failed",
            "protocol": {
                "unit": "session",
                "isolation": "fresh-worker-and-comfyui-per-session",
                "generations": ["cold", "warm"],
            },
            "probe_session": 0,
            "workload": workload.name,
            "runtime": runtime.name,
            "profile": profile.name,
            "requirements": workload.requirements.to_dict(),
            "overrides": [override.to_dict() for override in workload.overrides],
            "session_contract": session_contract,
            "requested_sessions": sessions,
            "completed_sessions": len(session_results),
            "sessions": session_results,
            "telemetry": telemetry,
            "environment": {"pre": pre_environment, "post": post_environment},
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        _write_json(output_dir / "report.json", failed)
        raise

    emit("postflight: capturing environment")
    post_environment = _environment(
        comfyui_root=comfyui_root,
        workload_path=workload_path,
        workflow_path=workload.workflow,
        runtime_path=runtime_path,
        profile_path=profile_path,
    )
    _write_json(output_dir / "environment.post.json", post_environment)

    report = {
        "schema_version": 4,
        "status": "completed",
        "protocol": {
            "unit": "session",
            "isolation": "fresh-worker-and-comfyui-per-session",
            "generations": ["cold", "warm"],
        },
        "probe_session": 0,
        "workload": workload.name,
        "runtime": runtime.name,
        "profile": profile.name,
        "requirements": workload.requirements.to_dict(),
        "overrides": [override.to_dict() for override in workload.overrides],
        "session_contract": session_contract,
        "requested_sessions": sessions,
        "completed_sessions": len(session_results),
        "timing": summarize_sessions(session_results),
        "sessions": session_results,
        "telemetry": telemetry,
        "environment": {"pre": pre_environment, "post": post_environment},
        "quality_gate": "not-run",
    }
    _write_json(output_dir / "report.json", report)
    emit(
        "completed: "
        f"cold median {float(report['timing']['cold_generation']['median_seconds']):.3f}s, "
        f"warm median {float(report['timing']['warm_generation']['median_seconds']):.3f}s"
    )
    return report
