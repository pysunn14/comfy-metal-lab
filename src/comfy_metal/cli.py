"""Command-line entry point for Comfy Metal Lab."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .benchmark import run_benchmark
from .compare import compare_benchmarks
from .inspection import inspect_workflow, render_workload_toml
from .preflight import run_preflight
from .run import run_once


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comfy-metal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect an API-format ComfyUI workflow and suggest a workload manifest"
    )
    inspect_parser.add_argument("workflow", type=Path)
    inspect_parser.add_argument("--name", default=None)
    inspect_parser.add_argument("--write", type=Path, default=None)

    preflight_parser = subparsers.add_parser(
        "preflight", help="Validate Workload × Runtime × Profile compatibility without generation"
    )
    preflight_parser.add_argument("--comfyui-root", type=Path, required=True)
    preflight_parser.add_argument("--workload", type=Path, required=True)
    preflight_parser.add_argument("--runtime", type=Path, required=True)
    preflight_parser.add_argument("--profile", type=Path, required=True)

    run_parser = subparsers.add_parser("run", help="Run one isolated workload/profile generation")
    run_parser.add_argument("--comfyui-root", type=Path, required=True)
    run_parser.add_argument("--workload", type=Path, required=True)
    run_parser.add_argument("--runtime", type=Path, required=True)
    run_parser.add_argument("--profile", type=Path, required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)

    bench_parser = subparsers.add_parser("bench", help="Run repeated cold/warm benchmark sessions")
    bench_parser.add_argument("--comfyui-root", type=Path, required=True)
    bench_parser.add_argument("--workload", type=Path, required=True)
    bench_parser.add_argument("--runtime", type=Path, required=True)
    bench_parser.add_argument("--profile", type=Path, required=True)
    bench_parser.add_argument("--output-dir", type=Path, required=True)
    bench_parser.add_argument("--sessions", type=int, default=3)
    bench_parser.add_argument("--swap-interval", type=float, default=1.0)

    compare_parser = subparsers.add_parser("compare", help="Compare two completed benchmark runs")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--comparison", type=Path, required=True)
    compare_parser.add_argument("--output-dir", type=Path, required=True)
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "preflight":
        report = run_preflight(
            comfyui_root=args.comfyui_root,
            workload_path=args.workload,
            runtime_path=args.runtime,
            profile_path=args.profile,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        report = run_once(
            comfyui_root=args.comfyui_root,
            workload_path=args.workload,
            runtime_path=args.runtime,
            profile_path=args.profile,
            output_dir=args.output_dir,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "bench":
        report = run_benchmark(
            comfyui_root=args.comfyui_root,
            workload_path=args.workload,
            runtime_path=args.runtime,
            profile_path=args.profile,
            output_dir=args.output_dir,
            sessions=args.sessions,
            swap_interval_seconds=args.swap_interval,
            progress=lambda message: print(f"[comfy-metal] {message}", file=sys.stderr, flush=True),
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "compare":
        report = compare_benchmarks(
            baseline_dir=args.baseline,
            candidate_dir=args.candidate,
            comparison_path=args.comparison,
            output_dir=args.output_dir,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
