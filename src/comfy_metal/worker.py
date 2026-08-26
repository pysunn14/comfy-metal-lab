"""Comfy Metal Lab isolated single-run and cold/warm session worker."""

from __future__ import annotations

import argparse
import json
import socket
import uuid
from pathlib import Path
from typing import Any

from .comfyui import ComfyUIServerConfig, run_workflow_once, run_workflow_session
from .config import MutationConfig, OverrideConfig
from .protocol import GenerationResult, SessionWorkerResult, WorkerResult, emit_worker_result
from .runtime_requirements import required_node_types
from .workflow import apply_overrides, apply_session_mutations, validate_api_workflow

__all__ = [
    "GenerationResult",
    "SessionWorkerResult",
    "WorkerResult",
    "build_generation_result",
    "build_worker_result",
    "emit_worker_result",
]


def _memory_metrics(mps_memory: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    gib = float(1024**3)
    peak_bytes = mps_memory.get("peak_allocated_bytes") if mps_memory.get("available") else None
    if not mps_memory.get("available"):
        return None, dict(mps_memory)
    return (
        float(peak_bytes) / (1024**2) if peak_bytes is not None else None,
        {
            "available": True,
            "backend": str(mps_memory.get("backend", "mps")),
            "measurement_scope": "prompt-submit-to-history-complete",
            "peak_allocated_gib": float(mps_memory["peak_allocated_bytes"]) / gib,
            "peak_reserved_gib": float(mps_memory["peak_reserved_bytes"]) / gib,
            "allocated_end_gib": float(mps_memory["allocated_end_bytes"]) / gib,
            "reserved_end_gib": float(mps_memory["reserved_end_bytes"]) / gib,
            "driver_allocated_end_gib": float(mps_memory["driver_allocated_end_bytes"]) / gib,
            "recommended_max_gib": float(mps_memory["recommended_max_bytes"]) / gib,
        },
    )


def build_generation_result(
    *,
    phase: str,
    generation_seconds: float,
    prompt_id: str,
    output_image: Path,
    mps_memory: dict[str, Any],
) -> GenerationResult:
    """Translate one prompt execution into the stable phase result protocol."""

    if phase not in {"cold", "warm"}:
        raise ValueError(f"unknown generation phase: {phase!r}")
    peak_memory_mb, memory_metrics = _memory_metrics(mps_memory)
    return GenerationResult(
        phase=phase,
        elapsed_s=generation_seconds,
        peak_memory_mb=peak_memory_mb,
        metrics={
            "prompt_id": prompt_id,
            "output_image": str(output_image),
            "mps_memory": memory_metrics,
        },
    )


def build_worker_result(
    *,
    condition: str,
    session: int,
    server_startup_seconds: float,
    runtime_preflight: dict[str, Any],
    cold: GenerationResult,
    warm: GenerationResult,
) -> SessionWorkerResult:
    return SessionWorkerResult(
        condition=condition,
        session=session,
        server_startup_s=server_startup_seconds,
        runtime_preflight=runtime_preflight,
        cold=cold,
        warm=warm,
    )


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _load_workflow(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text())
    if not isinstance(parsed, dict):
        raise ValueError("workflow JSON must contain an object")
    validate_api_workflow(parsed)
    return parsed


def _parse_override(raw: str) -> OverrideConfig:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("worker override must be a JSON object")
    node = parsed.get("node")
    input_name = parsed.get("input")
    fmt = parsed.get("format", "value")
    path = parsed.get("path")
    if not isinstance(node, str) or not node or not isinstance(input_name, str) or not input_name:
        raise ValueError("worker override requires node and input strings")
    if "value" not in parsed:
        raise ValueError("worker override requires value")
    if fmt not in {"value", "json"}:
        raise ValueError(f"unsupported worker override format: {fmt!r}")
    if path is not None and not isinstance(path, str):
        raise ValueError("worker override path must be a string")
    return OverrideConfig(
        node=node,
        input=input_name,
        value=parsed["value"],
        path=path,
        format=fmt,
    )


def _parse_mutation(raw: str) -> MutationConfig:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("worker mutation must be a JSON object")
    node = parsed.get("node")
    input_name = parsed.get("input")
    fmt = parsed.get("format", "value")
    path = parsed.get("path")
    if not isinstance(node, str) or not node or not isinstance(input_name, str) or not input_name:
        raise ValueError("worker mutation requires node and input strings")
    if fmt not in {"value", "json"}:
        raise ValueError(f"unsupported worker mutation format: {fmt!r}")
    if path is not None and not isinstance(path, str):
        raise ValueError("worker mutation path must be a string")
    return MutationConfig(
        node=node,
        input=input_name,
        cold=parsed.get("cold"),
        warm=parsed.get("warm"),
        path=path,
        format=fmt,
    )


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--comfyui-root", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--condition", required=True)
    parser.add_argument("--python", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--base-directory", type=Path, default=None)
    parser.add_argument("--extra-model-path", type=Path, action="append", default=[])
    parser.add_argument("--required-node", action="append", default=[])
    parser.add_argument("--override", action="append", default=[])
    parser.add_argument("--output-directory", type=Path, default=None)
    parser.add_argument("--output-node", default=None)
    parser.add_argument("--output-index", type=int, default=0)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--execution-timeout", type=float, default=1800.0)
    parser.add_argument(
        "--server-arg",
        action="append",
        default=[],
        help="Extra ComfyUI startup argument. Use --server-arg=--flag for dash-prefixed values.",
    )
    return parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    common = _common_parser()

    single = subparsers.add_parser("single", parents=[common], help="Run one prompt")
    single.add_argument("--output-image", type=Path, required=True)
    single.add_argument("--rep", type=int, default=0)

    session = subparsers.add_parser(
        "session",
        parents=[common],
        help="Run model-cold and model-warm prompts in one ComfyUI process",
    )
    session.add_argument("--session-index", type=int, required=True)
    session.add_argument("--cold-output-image", type=Path, required=True)
    session.add_argument("--warm-output-image", type=Path, required=True)
    session.add_argument("--mutation", action="append", required=True)
    return parser


def _server_config(args: argparse.Namespace) -> ComfyUIServerConfig:
    port = args.port if args.port is not None else _free_port(args.host)
    telemetry_port = _free_port(args.host)
    return ComfyUIServerConfig(
        root=args.comfyui_root,
        python=args.python,
        host=args.host,
        port=port,
        telemetry_host=args.host,
        telemetry_port=telemetry_port,
        telemetry_token=uuid.uuid4().hex,
        base_directory=args.base_directory,
        output_directory=args.output_directory,
        extra_model_paths=tuple(args.extra_model_path),
        extra_args=tuple(args.server_arg),
        startup_timeout_s=args.startup_timeout,
    )


def _run_single(args: argparse.Namespace, workflow: dict[str, Any]) -> WorkerResult:
    overrides = tuple(_parse_override(raw) for raw in args.override)
    materialized_workflow = apply_overrides(workflow, overrides=overrides)
    required_nodes = required_node_types(materialized_workflow, declared_nodes=tuple(args.required_node))
    execution = run_workflow_once(
        config=_server_config(args),
        workflow=materialized_workflow,
        output_image=args.output_image,
        output_node=args.output_node,
        output_index=args.output_index,
        required_nodes=required_nodes,
        execution_timeout_s=args.execution_timeout,
    )
    peak_memory_mb, memory_metrics = _memory_metrics(execution.mps_memory)
    return WorkerResult(
        condition=args.condition,
        rep=args.rep,
        elapsed_s=execution.generation_seconds,
        peak_memory_mb=peak_memory_mb,
        metrics={
            "server_startup_s": execution.server_startup_seconds,
            "prompt_id": execution.prompt_id,
            "output_image": str(args.output_image),
            "runtime_preflight": execution.runtime_preflight,
            "mps_memory": memory_metrics,
        },
    )


def _run_session(args: argparse.Namespace, workflow: dict[str, Any]) -> SessionWorkerResult:
    overrides = tuple(_parse_override(raw) for raw in args.override)
    materialized_workflow = apply_overrides(workflow, overrides=overrides)
    mutations = tuple(_parse_mutation(raw) for raw in args.mutation)
    cold_workflow = apply_session_mutations(materialized_workflow, mutations=mutations, phase="cold")
    warm_workflow = apply_session_mutations(materialized_workflow, mutations=mutations, phase="warm")
    required_nodes = required_node_types(materialized_workflow, declared_nodes=tuple(args.required_node))
    execution = run_workflow_session(
        config=_server_config(args),
        cold_workflow=cold_workflow,
        warm_workflow=warm_workflow,
        cold_output_image=args.cold_output_image,
        warm_output_image=args.warm_output_image,
        output_node=args.output_node,
        output_index=args.output_index,
        required_nodes=required_nodes,
        execution_timeout_s=args.execution_timeout,
    )
    cold = build_generation_result(
        phase="cold",
        generation_seconds=execution.cold.generation_seconds,
        prompt_id=execution.cold.prompt_id,
        output_image=args.cold_output_image,
        mps_memory=execution.cold.mps_memory,
    )
    warm = build_generation_result(
        phase="warm",
        generation_seconds=execution.warm.generation_seconds,
        prompt_id=execution.warm.prompt_id,
        output_image=args.warm_output_image,
        mps_memory=execution.warm.mps_memory,
    )
    return build_worker_result(
        condition=args.condition,
        session=args.session_index,
        server_startup_seconds=execution.server_startup_seconds,
        runtime_preflight=execution.runtime_preflight,
        cold=cold,
        warm=warm,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workflow = _load_workflow(args.workflow)
    result = _run_single(args, workflow) if args.mode == "single" else _run_session(args, workflow)
    print(emit_worker_result(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
