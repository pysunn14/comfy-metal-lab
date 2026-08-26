"""Compatibility preflight for Workload × Runtime × Profile without generation."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from .comfyui import ComfyUIClient, ComfyUIProcess, ComfyUIServerConfig
from .config import load_profile, load_runtime, load_workload
from .runtime_requirements import check_required_nodes, required_node_types
from .workflow import apply_overrides, validate_api_workflow


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _load_workflow(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("workflow JSON must contain an object")
    validate_api_workflow(parsed)
    return parsed


def run_preflight(
    *,
    comfyui_root: Path,
    workload_path: Path,
    runtime_path: Path,
    profile_path: Path,
) -> dict[str, Any]:
    """Start ComfyUI, verify required node classes, and exit before prompt execution."""

    workload = load_workload(workload_path)
    runtime = load_runtime(runtime_path)
    profile = load_profile(profile_path)
    workflow = _load_workflow(workload.workflow)
    materialized_workflow = apply_overrides(workflow, overrides=workload.overrides)
    required_nodes = required_node_types(
        materialized_workflow,
        declared_nodes=workload.requirements.nodes,
    )

    host = "127.0.0.1"
    port = _free_port(host)
    config = ComfyUIServerConfig(
        root=comfyui_root,
        python=runtime.python,
        host=host,
        port=port,
        base_directory=runtime.base_directory,
        extra_model_paths=runtime.extra_model_paths,
        extra_args=(*runtime.server_args, *profile.server_args),
    )

    with ComfyUIProcess(config) as server:
        startup_s = server.wait_until_ready()
        client = ComfyUIClient(host=host, port=port)
        evidence = check_required_nodes(
            object_info=client.object_info(),
            required_nodes=required_nodes,
        )

    return {
        "schema_version": 1,
        "status": "passed",
        "workload": workload.name,
        "runtime": runtime.name,
        "profile": profile.name,
        "server_startup_s": startup_s,
        "runtime_preflight": evidence,
    }
