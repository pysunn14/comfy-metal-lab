"""Command-line entry point for Comfy Metal Lab."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .benchmark import run_benchmark
from .compare import compare_benchmarks
from .config import load_profile, load_runtime, load_workload
from .doctor import format_doctor_report, run_doctor
from .inspection import inspect_workflow, render_workload_toml
from .preflight import run_preflight
from .run import run_once
from .workspace import (
    allocate_result_dir,
    import_workload,
    init_workspace,
    resolve_managed_config,
)

DEFAULT_WORKSPACE = Path(".comfy-metal")


def _add_workspace_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comfy-metal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Initialize a project-local .comfy-metal managed workspace"
    )
    _add_workspace_arg(init_parser)

    import_parser = subparsers.add_parser(
        "import-workload", help="Import an external API workflow into the managed workspace"
    )
    import_parser.add_argument("workflow", type=Path)
    import_parser.add_argument("--name", default=None)
    _add_workspace_arg(import_parser)


    doctor_parser = subparsers.add_parser(
        "doctor", help="Check benchmark readiness using the selected ComfyUI runtime/profile"
    )
    doctor_parser.add_argument("--comfyui-root", type=Path, default=None)
    doctor_parser.add_argument("--runtime", type=Path, default=Path("local"))
    doctor_parser.add_argument("--profile", type=Path, default=Path("stock"))
    doctor_parser.add_argument("--startup-timeout", type=float, default=60.0)
    doctor_parser.add_argument("--json", action="store_true", dest="json_output")
    _add_workspace_arg(doctor_parser)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect an API-format ComfyUI workflow and suggest a workload manifest"
    )
    inspect_parser.add_argument("workflow", type=Path)
    inspect_parser.add_argument("--name", default=None)
    inspect_parser.add_argument("--write", type=Path, default=None)

    preflight_parser = subparsers.add_parser(
        "preflight", help="Validate Workload × Runtime × Profile compatibility without generation"
    )
    preflight_parser.add_argument("--comfyui-root", type=Path, default=None)
    preflight_parser.add_argument("--workload", type=Path, required=True)
    preflight_parser.add_argument("--runtime", type=Path, required=True)
    preflight_parser.add_argument("--profile", type=Path, required=True)
    _add_workspace_arg(preflight_parser)

    run_parser = subparsers.add_parser("run", help="Run one isolated workload/profile generation")
    run_parser.add_argument("--comfyui-root", type=Path, default=None)
    run_parser.add_argument("--workload", type=Path, required=True)
    run_parser.add_argument("--runtime", type=Path, required=True)
    run_parser.add_argument("--profile", type=Path, required=True)
    run_parser.add_argument("--output-dir", type=Path, default=None)
    _add_workspace_arg(run_parser)

    bench_parser = subparsers.add_parser("bench", help="Run repeated cold/warm benchmark sessions")
    bench_parser.add_argument("--comfyui-root", type=Path, default=None)
    bench_parser.add_argument("--workload", type=Path, required=True)
    bench_parser.add_argument("--runtime", type=Path, required=True)
    bench_parser.add_argument("--profile", type=Path, required=True)
    bench_parser.add_argument("--output-dir", type=Path, default=None)
    bench_parser.add_argument("--sessions", type=int, default=3)
    bench_parser.add_argument("--swap-interval", type=float, default=1.0)
    _add_workspace_arg(bench_parser)

    compare_parser = subparsers.add_parser("compare", help="Compare two completed benchmark runs")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--comparison", type=Path, required=True)
    compare_parser.add_argument("--output-dir", type=Path, required=True)
    _add_workspace_arg(compare_parser)
    return parser


def _inspect(args: argparse.Namespace) -> int:
    inspection = inspect_workflow(args.workflow)
    if args.write is not None:
        if args.write.exists():
            raise FileExistsError(f"workload manifest already exists: {args.write}")
        name = args.name or args.workflow.stem
        manifest = render_workload_toml(
            name=name,
            workflow_name=args.workflow.name,
            inspection=inspection,
        )
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(manifest, encoding="utf-8")
    print(json.dumps(inspection.to_dict(), indent=2, sort_keys=True))
    return 0


def _resolve_experiment_configs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    return (
        resolve_managed_config(args.workload, kind="workload", workspace_root=args.workspace),
        resolve_managed_config(args.runtime, kind="runtime", workspace_root=args.workspace),
        resolve_managed_config(args.profile, kind="profile", workspace_root=args.workspace),
    )


def _resolve_comfyui_root(cli_root: Path | None, runtime_path: Path) -> Path:
    if cli_root is not None:
        return cli_root.expanduser()
    runtime = load_runtime(runtime_path)
    if runtime.comfyui_root is not None:
        return runtime.comfyui_root
    raise ValueError(
        "ComfyUI root is not configured; set comfyui_root in the selected runtime "
        "or pass --comfyui-root"
    )


def _managed_output_dir(
    args: argparse.Namespace,
    *,
    workload_path: Path,
    profile_path: Path,
    prefix: str,
) -> Path:
    if args.output_dir is not None:
        return args.output_dir
    workload = load_workload(workload_path)
    profile = load_profile(profile_path)
    output = allocate_result_dir(
        args.workspace,
        workload_name=workload.name,
        profile_name=profile.name,
        prefix=prefix,
    )
    print(f"[comfy-metal] output: {output}", file=sys.stderr, flush=True)
    return output


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        result = init_workspace(args.workspace)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "import-workload":
        result = import_workload(
            args.workflow, workspace_root=args.workspace, name=args.name
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "doctor":
        runtime = resolve_managed_config(
            args.runtime, kind="runtime", workspace_root=args.workspace
        )
        profile = resolve_managed_config(
            args.profile, kind="profile", workspace_root=args.workspace
        )
        comfyui_root = _resolve_comfyui_root(args.comfyui_root, runtime)
        report = run_doctor(
            comfyui_root=comfyui_root,
            runtime_path=runtime,
            profile_path=profile,
            workspace_root=args.workspace,
            startup_timeout_s=args.startup_timeout,
        )
        if args.json_output:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
            print(format_doctor_report(report, color=use_color))
        return 2 if report.readiness == "BLOCKED" else 0
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "preflight":
        workload, runtime, profile = _resolve_experiment_configs(args)
        comfyui_root = _resolve_comfyui_root(args.comfyui_root, runtime)
        report = run_preflight(
            comfyui_root=comfyui_root,
            workload_path=workload,
            runtime_path=runtime,
            profile_path=profile,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        workload, runtime, profile = _resolve_experiment_configs(args)
        comfyui_root = _resolve_comfyui_root(args.comfyui_root, runtime)
        output_dir = _managed_output_dir(
            args, workload_path=workload, profile_path=profile, prefix="run"
        )
        report = run_once(
            comfyui_root=comfyui_root,
            workload_path=workload,
            runtime_path=runtime,
            profile_path=profile,
            output_dir=output_dir,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "bench":
        workload, runtime, profile = _resolve_experiment_configs(args)
        comfyui_root = _resolve_comfyui_root(args.comfyui_root, runtime)
        output_dir = _managed_output_dir(
            args, workload_path=workload, profile_path=profile, prefix="bench"
        )
        report = run_benchmark(
            comfyui_root=comfyui_root,
            workload_path=workload,
            runtime_path=runtime,
            profile_path=profile,
            output_dir=output_dir,
            sessions=args.sessions,
            swap_interval_seconds=args.swap_interval,
            progress=lambda message: print(f"[comfy-metal] {message}", file=sys.stderr, flush=True),
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "compare":
        comparison = resolve_managed_config(
            args.comparison, kind="comparison", workspace_root=args.workspace
        )
        report = compare_benchmarks(
            baseline_dir=args.baseline,
            candidate_dir=args.candidate,
            comparison_path=comparison,
            output_dir=args.output_dir,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
