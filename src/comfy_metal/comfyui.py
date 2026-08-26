"""Minimal ComfyUI process and HTTP API adapter.

ComfyUI is treated as an external runtime. This module starts an existing
checkout, waits until its HTTP API is ready, submits one API-format workflow,
waits for completion, downloads the first output image, and tears the runtime
down again.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime_requirements import check_required_nodes


@dataclass(frozen=True)
class ComfyUIServerConfig:
    """Pinned configuration for one isolated ComfyUI server process."""

    root: Path
    port: int
    python: Path | None = None
    host: str = "127.0.0.1"
    base_directory: Path | None = None
    output_directory: Path | None = None
    telemetry_host: str = "127.0.0.1"
    telemetry_port: int | None = None
    telemetry_token: str | None = None
    extra_model_paths: tuple[Path, ...] = ()
    extra_args: tuple[str, ...] = ()
    startup_timeout_s: float = 120.0
    poll_interval_s: float = 0.2

    def resolved_python(self) -> Path:
        return self.python or self.root / ".venv" / "bin" / "python"

    def command(self) -> list[str]:
        """Build the exact external ComfyUI command for this worker."""

        comfy_command = [
            str(self.root / "main.py"),
            "--listen",
            self.host,
            "--port",
            str(self.port),
            "--disable-auto-launch",
            "--preview-method",
            "none",
        ]
        if self.telemetry_port is None:
            command = [str(self.resolved_python()), *comfy_command]
        else:
            wrapper = Path(__file__).with_name("runtime_entry.py")
            command = [
                str(self.resolved_python()),
                str(wrapper),
                "--telemetry-host",
                self.telemetry_host,
                "--telemetry-port",
                str(self.telemetry_port),
                "--",
                *comfy_command,
            ]
        if self.base_directory is not None:
            command += ["--base-directory", str(self.base_directory)]
        if self.output_directory is not None:
            command += ["--output-directory", str(self.output_directory)]
        if self.extra_model_paths:
            command.append("--extra-model-paths-config")
            command.extend(str(path) for path in self.extra_model_paths)
        command.extend(self.extra_args)
        return command

    def validate(self) -> None:
        if not self.root.is_dir():
            raise FileNotFoundError(f"ComfyUI root does not exist: {self.root}")
        main = self.root / "main.py"
        if not main.is_file():
            raise FileNotFoundError(f"ComfyUI main.py does not exist: {main}")
        python = self.resolved_python()
        if not python.is_file():
            raise FileNotFoundError(f"ComfyUI Python does not exist: {python}")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"invalid ComfyUI port: {self.port}")
        if self.telemetry_port is not None and not 1 <= self.telemetry_port <= 65535:
            raise ValueError(f"invalid telemetry port: {self.telemetry_port}")
        if self.telemetry_port is not None and not self.telemetry_token:
            raise ValueError("telemetry_token is required when telemetry is enabled")


class MPSMemoryClient:
    """Loopback client for same-process MPS allocator instrumentation."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        timeout_s: float = 5.0,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.token = token
        self.timeout_s = timeout_s

    def _request_json(self, path: str, *, method: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method=method,
            headers={"X-Comfy-Metal-Token": self.token},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError(f"memory telemetry {path} returned non-object JSON")
        return parsed

    def reset_peak(self) -> dict[str, Any]:
        return self._request_json("/reset", method="POST")

    def snapshot(self) -> dict[str, Any]:
        return self._request_json("/snapshot", method="GET")


class ComfyUIClient:
    """Small standard-library client for the ComfyUI prompt/history API."""

    def __init__(self, *, host: str, port: int, timeout_s: float = 5.0) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout_s = timeout_s

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError(f"ComfyUI {path} returned non-object JSON")
        return parsed

    def system_stats(self) -> dict[str, Any]:
        return self._request_json("/system_stats")

    def object_info(self) -> dict[str, Any]:
        return self._request_json("/object_info")

    def submit_prompt(self, workflow: dict[str, Any]) -> str:
        response = self._request_json(
            "/prompt",
            method="POST",
            payload={"prompt": workflow, "client_id": str(uuid.uuid4())},
        )
        node_errors = response.get("node_errors")
        if node_errors:
            raise RuntimeError(f"ComfyUI rejected workflow: {node_errors}")
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt response has no prompt_id: {response}")
        return prompt_id

    def wait_for_completion(
        self,
        prompt_id: str,
        *,
        timeout_s: float,
        poll_interval_s: float = 0.2,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            history = self._request_json(f"/history/{urllib.parse.quote(prompt_id)}")
            entry = history.get(prompt_id)
            if isinstance(entry, dict):
                status = entry.get("status", {})
                if isinstance(status, dict):
                    status_str = status.get("status_str")
                    if status_str == "error":
                        raise RuntimeError(
                            f"ComfyUI prompt {prompt_id} failed: {status.get('messages', [])}"
                        )
                    if status.get("completed") is True or status_str == "success":
                        return entry
                if entry.get("outputs"):
                    return entry
            time.sleep(poll_interval_s)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} did not complete within {timeout_s:.1f}s")

    @staticmethod
    def output_image(
        history_entry: dict[str, Any],
        *,
        node_id: str | None = None,
        index: int = 0,
    ) -> dict[str, str]:
        """Select one output image, optionally from an explicit history node."""

        if index < 0:
            raise ValueError("output image index must be non-negative")
        outputs = history_entry.get("outputs", {})
        if not isinstance(outputs, dict):
            raise RuntimeError("ComfyUI history outputs is not an object")

        node_ids = [node_id] if node_id is not None else sorted(outputs, key=str)
        for selected_node_id in node_ids:
            node_output = outputs.get(str(selected_node_id))
            if not isinstance(node_output, dict):
                if node_id is not None:
                    raise RuntimeError(f"ComfyUI history has no output for node {node_id}")
                continue
            images = node_output.get("images", [])
            if not isinstance(images, list):
                if node_id is not None:
                    raise RuntimeError(f"ComfyUI output node {node_id} has no image list")
                continue
            if node_id is not None:
                if index >= len(images):
                    raise RuntimeError(
                        f"ComfyUI output node {node_id} has no image at index {index}"
                    )
                candidates = [images[index]]
            else:
                candidates = images
            for image in candidates:
                if not isinstance(image, dict):
                    continue
                filename = image.get("filename")
                if isinstance(filename, str) and filename:
                    return {
                        "filename": filename,
                        "subfolder": str(image.get("subfolder", "")),
                        "type": str(image.get("type", "output")),
                    }
            if node_id is not None:
                raise RuntimeError(f"ComfyUI output node {node_id} image {index} is malformed")
        raise RuntimeError("ComfyUI prompt completed without an output image")

    @staticmethod
    def first_output_image(history_entry: dict[str, Any]) -> dict[str, str]:
        return ComfyUIClient.output_image(history_entry)

    def download_image(self, image: dict[str, str], destination: Path) -> None:
        query = urllib.parse.urlencode(image)
        request = urllib.request.Request(f"{self.base_url}/view?{query}")
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            body = response.read()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)


@dataclass
class ComfyUIProcess:
    """Lifecycle owner for one isolated external ComfyUI server process."""

    config: ComfyUIServerConfig
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)

    def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("ComfyUI process already started")
        self.config.validate()
        env = os.environ.copy()
        if self.config.telemetry_token is not None:
            env["COMFY_METAL_TELEMETRY_TOKEN"] = self.config.telemetry_token
        self.process = subprocess.Popen(
            self.config.command(),
            cwd=self.config.root,
            env=env,
        )

    def wait_until_ready(self) -> float:
        if self.process is None:
            raise RuntimeError("ComfyUI process has not been started")
        started = time.perf_counter()
        deadline = time.monotonic() + self.config.startup_timeout_s
        client = ComfyUIClient(host=self.config.host, port=self.config.port, timeout_s=1.0)
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            returncode = self.process.poll()
            if returncode is not None:
                raise RuntimeError(f"ComfyUI exited before becoming ready: exit {returncode}")
            try:
                client.system_stats()
                return time.perf_counter() - started
            except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                last_error = exc
                time.sleep(self.config.poll_interval_s)
        raise TimeoutError(
            f"ComfyUI did not become ready on {self.config.host}:{self.config.port} "
            f"within {self.config.startup_timeout_s:.1f}s; last error={last_error!r}"
        )

    def stop(self, *, grace_s: float = 10.0) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=grace_s)
        self.process = None

    def __enter__(self) -> ComfyUIProcess:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.stop()


@dataclass(frozen=True)
class PromptExecutionResult:
    prompt_id: str
    generation_seconds: float
    image: dict[str, str]
    mps_memory: dict[str, Any]


@dataclass(frozen=True)
class WorkflowExecutionResult:
    prompt_id: str
    generation_seconds: float
    server_startup_seconds: float
    runtime_preflight: dict[str, Any]
    image: dict[str, str]
    mps_memory: dict[str, Any]


@dataclass(frozen=True)
class WorkflowSessionResult:
    server_startup_seconds: float
    runtime_preflight: dict[str, Any]
    cold: PromptExecutionResult
    warm: PromptExecutionResult


def _disabled_memory() -> dict[str, Any]:
    return {
        "available": False,
        "backend": "mps",
        "error": "memory telemetry disabled",
    }


def _execute_prompt(
    *,
    client: ComfyUIClient,
    memory_client: MPSMemoryClient | None,
    workflow: dict[str, Any],
    output_image: Path,
    output_node: str | None,
    output_index: int,
    execution_timeout_s: float,
    poll_interval_s: float,
) -> PromptExecutionResult:
    """Execute one prompt against an already-running ComfyUI process."""

    mps_memory = _disabled_memory()
    if memory_client is not None:
        reset = memory_client.reset_peak()
        mps_memory = dict(reset) if reset.get("available") is False else {}

    started = time.perf_counter()
    prompt_id = client.submit_prompt(workflow)
    history_entry = client.wait_for_completion(
        prompt_id,
        timeout_s=execution_timeout_s,
        poll_interval_s=poll_interval_s,
    )
    generation_seconds = time.perf_counter() - started

    if memory_client is not None:
        snapshot = memory_client.snapshot()
        if snapshot.get("available") is True or not mps_memory:
            mps_memory = snapshot

    image = client.output_image(history_entry, node_id=output_node, index=output_index)
    client.download_image(image, output_image)
    return PromptExecutionResult(
        prompt_id=prompt_id,
        generation_seconds=generation_seconds,
        image=image,
        mps_memory=mps_memory,
    )


def _clients(config: ComfyUIServerConfig) -> tuple[ComfyUIClient, MPSMemoryClient | None]:
    client = ComfyUIClient(host=config.host, port=config.port)
    memory_client = (
        None
        if config.telemetry_port is None
        else MPSMemoryClient(
            host=config.telemetry_host,
            port=config.telemetry_port,
            token=config.telemetry_token or "",
        )
    )
    return client, memory_client


def run_workflow_once(
    *,
    config: ComfyUIServerConfig,
    workflow: dict[str, Any],
    output_image: Path,
    output_node: str | None = None,
    output_index: int = 0,
    required_nodes: tuple[str, ...] = (),
    execution_timeout_s: float = 1800.0,
) -> WorkflowExecutionResult:
    """Run one workflow against a fresh ComfyUI process and save its first image."""

    with ComfyUIProcess(config) as server:
        startup_seconds = server.wait_until_ready()
        client, memory_client = _clients(config)
        runtime_preflight = check_required_nodes(
            object_info=client.object_info(),
            required_nodes=required_nodes,
        )
        prompt = _execute_prompt(
            client=client,
            memory_client=memory_client,
            workflow=workflow,
            output_image=output_image,
            output_node=output_node,
            output_index=output_index,
            execution_timeout_s=execution_timeout_s,
            poll_interval_s=config.poll_interval_s,
        )

    return WorkflowExecutionResult(
        prompt_id=prompt.prompt_id,
        generation_seconds=prompt.generation_seconds,
        server_startup_seconds=startup_seconds,
        runtime_preflight=runtime_preflight,
        image=prompt.image,
        mps_memory=prompt.mps_memory,
    )


def run_workflow_session(
    *,
    config: ComfyUIServerConfig,
    cold_workflow: dict[str, Any],
    warm_workflow: dict[str, Any],
    cold_output_image: Path,
    warm_output_image: Path,
    output_node: str | None = None,
    output_index: int = 0,
    required_nodes: tuple[str, ...] = (),
    execution_timeout_s: float = 1800.0,
) -> WorkflowSessionResult:
    """Measure model-cold and model-warm prompts in one fresh ComfyUI session.

    The first prompt pays model loading and first-use runtime preparation. The
    second prompt runs in the same process with model residency preserved. MPS
    peak statistics are reset independently before each prompt.
    """

    with ComfyUIProcess(config) as server:
        startup_seconds = server.wait_until_ready()
        client, memory_client = _clients(config)
        runtime_preflight = check_required_nodes(
            object_info=client.object_info(),
            required_nodes=required_nodes,
        )
        cold = _execute_prompt(
            client=client,
            memory_client=memory_client,
            workflow=cold_workflow,
            output_image=cold_output_image,
            output_node=output_node,
            output_index=output_index,
            execution_timeout_s=execution_timeout_s,
            poll_interval_s=config.poll_interval_s,
        )
        warm = _execute_prompt(
            client=client,
            memory_client=memory_client,
            workflow=warm_workflow,
            output_image=warm_output_image,
            output_node=output_node,
            output_index=output_index,
            execution_timeout_s=execution_timeout_s,
            poll_interval_s=config.poll_interval_s,
        )

    return WorkflowSessionResult(
        server_startup_seconds=startup_seconds,
        runtime_preflight=runtime_preflight,
        cold=cold,
        warm=warm,
    )
