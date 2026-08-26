import json
import sys

from comfy_metal.orchestrator import run_worker_process
from comfy_metal.protocol import RESULT_SENTINEL


def test_run_worker_process_parses_result_and_preserves_logs(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import json\n"
        "print('worker log')\n"
        f"print({RESULT_SENTINEL!r} + json.dumps({{'condition': 'stock', 'rep': 0, 'elapsed_s': 1.25, 'peak_memory_mb': 42.0, 'metrics': {{'forwards': 36}}}}))\n"
    )

    execution = run_worker_process([sys.executable, str(script)], label="stock/rep0")

    assert execution.result["elapsed_s"] == 1.25
    assert execution.result["metrics"] == {"forwards": 36}
    assert "worker log" in execution.stdout
