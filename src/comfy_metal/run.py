"""Materialize isolated single generations and cold/warm benchmark sessions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .config import OutputConfig, OverrideConfig, RuntimeConfig, SessionConfig, load_profile, load_runtime, load_workload
from .orchestrator import run_worker_process


def _base_worker_command(
    *,
    mode: str,
    comfyui_root: Path,
    workflow: Path,
    runtime: RuntimeConfig,
    profile_name: str,
    profile_server_args: tuple[str, ...],
    required_nodes: tuple[str, ...],
    overrides: tuple[OverrideConfig, ...],
    output_dir: Path,
    output: OutputConfig | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "comfy_metal.worker",
        mode,
        "--comfyui-root",
        str(comfyui_root),
        "--workflow",
        str(workflow),
        "--output-directory",
        str(output_dir / "comfyui-output"),
        "--condition",
        profile_name,
    ]
    if runtime.python is not None:
        command += ["--python", str(runtime.python)]
    if runtime.base_directory is not None:
        command += ["--base-directory", str(runtime.base_directory)]
    for extra_path in runtime.extra_model_paths:
        command += ["--extra-model-path", str(extra_path)]
    for required_node in required_nodes:
        command += ["--required-node", required_node]
    for override in overrides:
        command += [
            "--override",
            json.dumps(override.to_dict(), separators=(",", ":"), ensure_ascii=False),
        ]
    if output is not None:
        command += ["--output-node", output.node, "--output-index", str(output.index)]
    for server_arg in (*runtime.server_args, *profile_server_args):
        command.append(f"--server-arg={server_arg}")
    return command


def _single_worker_command(
    *,
    comfyui_root: Path,
    workflow: Path,
    runtime: RuntimeConfig,
    profile_name: str,
    profile_server_args: tuple[str, ...],
    required_nodes: tuple[str, ...],
    overrides: tuple[OverrideConfig, ...],
    output_dir: Path,
    output: OutputConfig | None,
    rep: int,
) -> list[str]:
    return [
        *_base_worker_command(
            mode="single",
            comfyui_root=comfyui_root,
            workflow=workflow,
            runtime=runtime,
            profile_name=profile_name,
            profile_server_args=profile_server_args,
            required_nodes=required_nodes,
            overrides=overrides,
            output_dir=output_dir,
            output=output,
        ),
        "--output-image",
        str(output_dir / "image.png"),
        "--rep",
        str(rep),
    ]


def _session_worker_command(
    *,
    comfyui_root: Path,
    workflow: Path,
    runtime: RuntimeConfig,
    profile_name: str,
    profile_server_args: tuple[str, ...],
    required_nodes: tuple[str, ...],
    overrides: tuple[OverrideConfig, ...],
    output_dir: Path,
    output: OutputConfig,
    session_index: int,
    session_config: SessionConfig,
) -> list[str]:
    command = [
        *_base_worker_command(
            mode="session",
            comfyui_root=comfyui_root,
            workflow=workflow,
            runtime=runtime,
            profile_name=profile_name,
            profile_server_args=profile_server_args,
            required_nodes=required_nodes,
            overrides=overrides,
            output_dir=output_dir,
            output=output,
        ),
        "--session-index",
        str(session_index),
        "--cold-output-image",
        str(output_dir / "cold.png"),
        "--warm-output-image",
        str(output_dir / "warm.png"),
    ]
    for mutation in session_config.mutations:
        command += [
            "--mutation",
            json.dumps(mutation.to_dict(), separators=(",", ":"), ensure_ascii=False),
        ]
    return command


def _write_run_artifacts(
    *,
    output_dir: Path,
    stdout: str,
    stderr: str,
    report: dict[str, Any],
) -> None:
    (output_dir / "worker.stdout.log").write_text(stdout)
    (output_dir / "worker.stderr.log").write_text(stderr)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )


def run_once(
    *,
    comfyui_root: Path,
    workload_path: Path,
    runtime_path: Path,
    profile_path: Path,
    output_dir: Path,
    rep: int = 0,
) -> dict[str, Any]:
    """Run one workload/profile pair in a fresh worker subprocess."""

    if rep < 0:
        raise ValueError("rep must be non-negative")
    if output_dir.exists():
        raise FileExistsError(f"run output directory already exists: {output_dir}")

    workload = load_workload(workload_path)
    runtime = load_runtime(runtime_path)
    profile = load_profile(profile_path)
    output_dir.mkdir(parents=True)
    command = _single_worker_command(
        comfyui_root=comfyui_root,
        workflow=workload.workflow,
        runtime=runtime,
        profile_name=profile.name,
        profile_server_args=profile.server_args,
        required_nodes=workload.requirements.nodes,
        overrides=workload.overrides,
        output_dir=output_dir,
        output=workload.output,
        rep=rep,
    )
    execution = run_worker_process(command, label=f"{workload.name}/{profile.name}/rep{rep}")
    report: dict[str, Any] = {
        "schema_version": 2,
        "isolation": "subprocess-per-rep",
        "workload": workload.name,
        "runtime": runtime.name,
        "profile": profile.name,
        "requirements": workload.requirements.to_dict(),
        "overrides": [override.to_dict() for override in workload.overrides],
        "result": execution.result,
    }
    _write_run_artifacts(
        output_dir=output_dir,
        stdout=execution.stdout,
        stderr=execution.stderr,
        report=report,
    )
    return report


def run_session(
    *,
    comfyui_root: Path,
    workload_path: Path,
    runtime_path: Path,
    profile_path: Path,
    output_dir: Path,
    session: int,
) -> dict[str, Any]:
    """Run cold and warm prompts in one fresh worker/ComfyUI session."""

    if session < 0:
        raise ValueError("session must be non-negative")
    if output_dir.exists():
        raise FileExistsError(f"session output directory already exists: {output_dir}")

    workload = load_workload(workload_path)
    session_config = workload.require_session()
    output_config = workload.require_output()
    runtime = load_runtime(runtime_path)
    profile = load_profile(profile_path)
    output_dir.mkdir(parents=True)
    command = _session_worker_command(
        comfyui_root=comfyui_root,
        workflow=workload.workflow,
        runtime=runtime,
        profile_name=profile.name,
        profile_server_args=profile.server_args,
        required_nodes=workload.requirements.nodes,
        overrides=workload.overrides,
        output_dir=output_dir,
        output=output_config,
        session_index=session,
        session_config=session_config,
    )
    execution = run_worker_process(
        command,
        label=f"{workload.name}/{profile.name}/session{session}",
    )
    report: dict[str, Any] = {
        "schema_version": 4,
        "protocol": "cold-warm-session",
        "isolation": "fresh-worker-and-comfyui-per-session",
        "workload": workload.name,
        "runtime": runtime.name,
        "profile": profile.name,
        "requirements": workload.requirements.to_dict(),
        "overrides": [override.to_dict() for override in workload.overrides],
        "session": execution.result,
    }
    _write_run_artifacts(
        output_dir=output_dir,
        stdout=execution.stdout,
        stderr=execution.stderr,
        report=report,
    )
    return report
