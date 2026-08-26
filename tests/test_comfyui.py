from __future__ import annotations

import socket
import urllib.error
import urllib.request
import sys
from pathlib import Path

from comfy_metal.comfyui import (
    ComfyUIClient,
    ComfyUIProcess,
    ComfyUIServerConfig,
    run_workflow_once,
    run_workflow_session,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_fake_comfyui(root: Path) -> None:
    (root / "main.py").write_text(
        r'''
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--listen", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
args, _ = parser.parse_known_args()

PROMPTS = {}
COUNTER = 0

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/system_stats":
            self._json({"system": {"os": "fake"}})
            return
        if path == "/object_info":
            self._json({
                "FakeNode": {},
                "KSampler": {},
                "SaveImage": {},
            })
            return
        if path.startswith("/history/"):
            prompt_id = path.rsplit("/", 1)[-1]
            if prompt_id not in PROMPTS:
                self._json({})
                return
            filename = f"{prompt_id}.png"
            self._json({
                prompt_id: {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {
                        "9": {"images": [{"filename": filename, "subfolder": "smoke", "type": "output"}]}
                    },
                }
            })
            return
        if path == "/view":
            filename = parse_qs(parsed.query).get("filename", ["unknown"])[0]
            body = filename.encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        global COUNTER
        if urlparse(self.path).path != "/prompt":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if "prompt" not in payload:
            self._json({"error": "missing prompt"}, 400)
            return
        COUNTER += 1
        prompt_id = f"prompt-{COUNTER}"
        PROMPTS[prompt_id] = payload["prompt"]
        self._json({"prompt_id": prompt_id, "number": COUNTER, "node_errors": {}})

HTTPServer((args.listen, args.port), Handler).serve_forever()
'''.lstrip()
    )


def test_server_command_is_external_and_pinned(tmp_path: Path) -> None:
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("")
    base = tmp_path / "base"
    output = tmp_path / "output"
    cfg = ComfyUIServerConfig(
        root=root,
        python=Path(sys.executable),
        port=8189,
        base_directory=base,
        output_directory=output,
        extra_args=("--disable-all-custom-nodes",),
    )

    command = cfg.command()

    assert command[:2] == [sys.executable, str(root / "main.py")]
    assert command[command.index("--listen") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8189"
    assert command[command.index("--base-directory") + 1] == str(base)
    assert command[command.index("--output-directory") + 1] == str(output)
    assert "--disable-auto-launch" in command
    assert "--preview-method" in command
    assert "--disable-all-custom-nodes" in command


def test_fake_comfyui_single_round_trip_and_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "fake-comfyui"
    root.mkdir()
    _write_fake_comfyui(root)
    port = _free_port()
    cfg = ComfyUIServerConfig(
        root=root,
        python=Path(sys.executable),
        port=port,
        startup_timeout_s=5,
        poll_interval_s=0.01,
    )
    output = tmp_path / "apple.png"

    result = run_workflow_once(
        config=cfg,
        workflow={"1": {"class_type": "FakeNode", "inputs": {"seed": 42}}},
        output_image=output,
        execution_timeout_s=5,
    )

    assert result.prompt_id == "prompt-1"
    assert result.generation_seconds >= 0
    assert result.server_startup_seconds >= 0
    assert output.read_bytes() == b"prompt-1.png"
    _assert_server_stopped(port)


def test_cold_and_warm_prompts_share_one_comfyui_process(tmp_path: Path) -> None:
    root = tmp_path / "fake-comfyui"
    root.mkdir()
    _write_fake_comfyui(root)
    port = _free_port()
    cfg = ComfyUIServerConfig(
        root=root,
        python=Path(sys.executable),
        port=port,
        startup_timeout_s=5,
        poll_interval_s=0.01,
    )

    result = run_workflow_session(
        config=cfg,
        cold_workflow={"7": {"inputs": {"seed": 42}}},
        warm_workflow={"7": {"inputs": {"seed": 43}}},
        cold_output_image=tmp_path / "cold.png",
        warm_output_image=tmp_path / "warm.png",
        execution_timeout_s=5,
    )

    # Incrementing IDs prove both prompts were accepted by the same server process.
    assert result.cold.prompt_id == "prompt-1"
    assert result.warm.prompt_id == "prompt-2"
    assert (tmp_path / "cold.png").read_bytes() == b"prompt-1.png"
    assert (tmp_path / "warm.png").read_bytes() == b"prompt-2.png"
    assert result.cold.generation_seconds >= 0
    assert result.warm.generation_seconds >= 0
    _assert_server_stopped(port)


def test_session_validates_required_nodes_before_generation(tmp_path: Path) -> None:
    root = tmp_path / "fake-comfyui"
    root.mkdir()
    _write_fake_comfyui(root)
    cfg = ComfyUIServerConfig(
        root=root, python=Path(sys.executable), port=_free_port(),
        startup_timeout_s=5, poll_interval_s=0.01,
    )

    result = run_workflow_session(
        config=cfg,
        cold_workflow={"7": {"class_type": "KSampler", "inputs": {"seed": 42}}},
        warm_workflow={"7": {"class_type": "KSampler", "inputs": {"seed": 43}}},
        cold_output_image=tmp_path / "cold.png",
        warm_output_image=tmp_path / "warm.png",
        required_nodes=("KSampler", "SaveImage"),
        execution_timeout_s=5,
    )

    assert result.runtime_preflight["passed"] is True
    assert result.runtime_preflight["missing_nodes"] == []


def test_session_stops_before_prompt_when_required_node_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "fake-comfyui"
    root.mkdir()
    _write_fake_comfyui(root)
    cfg = ComfyUIServerConfig(
        root=root, python=Path(sys.executable), port=_free_port(),
        startup_timeout_s=5, poll_interval_s=0.01,
    )

    try:
        run_workflow_session(
            config=cfg,
            cold_workflow={"7": {"class_type": "KSampler", "inputs": {"seed": 42}}},
            warm_workflow={"7": {"class_type": "KSampler", "inputs": {"seed": 43}}},
            cold_output_image=tmp_path / "cold.png",
            warm_output_image=tmp_path / "warm.png",
            required_nodes=("DiTSpectrumPatchAdvanced",),
            execution_timeout_s=5,
        )
    except RuntimeError as exc:
        assert "DiTSpectrumPatchAdvanced" in str(exc)
    else:
        raise AssertionError("missing required node was accepted")

    assert not (tmp_path / "cold.png").exists()
    assert not (tmp_path / "warm.png").exists()


def test_server_command_uses_memory_wrapper_when_telemetry_enabled(tmp_path: Path) -> None:
    root = tmp_path / "ComfyUI"
    root.mkdir()
    (root / "main.py").write_text("")
    cfg = ComfyUIServerConfig(
        root=root,
        python=Path(sys.executable),
        port=8189,
        telemetry_port=8190,
        telemetry_token="test-token",
    )

    command = cfg.command()

    assert command[0] == sys.executable
    assert command[1].endswith("runtime_entry.py")
    assert command[command.index("--telemetry-port") + 1] == "8190"
    separator = command.index("--")
    assert command[separator + 1] == str(root / "main.py")


def test_session_snapshots_memory_for_both_phases(tmp_path: Path) -> None:
    root = tmp_path / "fake-comfyui"
    root.mkdir()
    _write_fake_comfyui(root)
    cfg = ComfyUIServerConfig(
        root=root,
        python=Path(sys.executable),
        port=_free_port(),
        telemetry_port=_free_port(),
        telemetry_token="test-token",
        startup_timeout_s=5,
        poll_interval_s=0.01,
    )

    result = run_workflow_session(
        config=cfg,
        cold_workflow={"7": {"inputs": {"seed": 42}}},
        warm_workflow={"7": {"inputs": {"seed": 43}}},
        cold_output_image=tmp_path / "cold.png",
        warm_output_image=tmp_path / "warm.png",
        execution_timeout_s=5,
    )

    assert "available" in result.cold.mps_memory
    assert "available" in result.warm.mps_memory



def test_memory_endpoint_rejects_unauthorized_observers(tmp_path: Path) -> None:
    root = tmp_path / "fake-comfyui-auth"
    root.mkdir()
    _write_fake_comfyui(root)
    telemetry_port = _free_port()
    cfg = ComfyUIServerConfig(
        root=root,
        python=Path(sys.executable),
        port=_free_port(),
        telemetry_port=telemetry_port,
        telemetry_token="test-token",
        startup_timeout_s=5,
        poll_interval_s=0.01,
    )

    with ComfyUIProcess(cfg) as process:
        process.wait_until_ready()
        request = urllib.request.Request(f"http://127.0.0.1:{telemetry_port}/snapshot")
        try:
            urllib.request.urlopen(request, timeout=1)
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("unauthorized telemetry request was accepted")

def _assert_server_stopped(port: int) -> None:
    client = ComfyUIClient(host="127.0.0.1", port=port, timeout_s=0.1)
    try:
        client.system_stats()
    except OSError:
        return
    raise AssertionError("fake ComfyUI server was still reachable after cleanup")


def test_output_image_selects_declared_node_and_index() -> None:
    history = {
        "outputs": {
            "2": {"images": [{"filename": "preview.png", "subfolder": "", "type": "output"}]},
            "9": {"images": [
                {"filename": "final-0.png", "subfolder": "", "type": "output"},
                {"filename": "final-1.png", "subfolder": "", "type": "output"},
            ]},
        }
    }

    image = ComfyUIClient.output_image(history, node_id="9", index=1)

    assert image["filename"] == "final-1.png"
