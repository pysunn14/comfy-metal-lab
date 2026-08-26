from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from comfy_metal.quality import compare_image_sets, compute_ssim, summarize_determinism


def _image(path: Path, array: np.ndarray) -> Path:
    Image.fromarray(array.astype(np.uint8), mode="RGB").save(path)
    return path


def test_identical_images_short_circuit_to_one(tmp_path: Path) -> None:
    pixels = np.zeros((16, 16, 3), dtype=np.uint8)
    left = _image(tmp_path / "left.png", pixels)
    right = tmp_path / "right.png"
    right.write_bytes(left.read_bytes())

    assert compute_ssim(left, right) == 1.0


def test_ssim_rejects_dimension_mismatch(tmp_path: Path) -> None:
    left = _image(tmp_path / "left.png", np.zeros((16, 16, 3), dtype=np.uint8))
    right = _image(tmp_path / "right.png", np.zeros((20, 16, 3), dtype=np.uint8))

    with pytest.raises(ValueError, match="dimensions"):
        compute_ssim(left, right)


def test_same_phase_hash_mismatch_is_observed_not_invalidated(tmp_path: Path) -> None:
    base = np.full((16, 16, 3), 120, dtype=np.uint8)
    changed = base.copy()
    changed[8, 8] = [121, 120, 120]
    first = _image(tmp_path / "session0.png", base)
    second = _image(tmp_path / "session1.png", changed)

    summary = summarize_determinism([first, second])

    assert summary["unit"] == "session"
    assert summary["exact_hash_match"] is False
    assert summary["count"] == 2
    assert len(summary["pairwise_ssim"]) == 1
    assert summary["min_intra_ssim"] > 0.99


def test_cross_profile_gate_uses_min_paired_ssim(tmp_path: Path) -> None:
    baseline_pixels = np.full((16, 16, 3), 100, dtype=np.uint8)
    candidate_good = baseline_pixels.copy()
    candidate_bad = baseline_pixels.copy()
    candidate_bad[4:12, 4:12] = 180

    baseline = [
        _image(tmp_path / "base0.png", baseline_pixels),
        _image(tmp_path / "base1.png", baseline_pixels),
    ]
    candidate = [
        _image(tmp_path / "cand0.png", candidate_good),
        _image(tmp_path / "cand1.png", candidate_bad),
    ]

    result = compare_image_sets(baseline, candidate, min_ssim=0.90)

    assert result["unit"] == "session"
    assert result["threshold"] == 0.90
    assert result["paired_ssim"][0]["ssim"] == 1.0
    assert result["min_ssim"] == min(pair["ssim"] for pair in result["paired_ssim"])
    assert result["passed"] is (result["min_ssim"] >= 0.90)


def test_cross_profile_gate_can_fail(tmp_path: Path) -> None:
    baseline = [_image(tmp_path / "base.png", np.zeros((16, 16, 3), dtype=np.uint8))]
    candidate = [_image(tmp_path / "candidate.png", np.full((16, 16, 3), 255, dtype=np.uint8))]

    result = compare_image_sets(baseline, candidate, min_ssim=0.90)

    assert result["min_ssim"] < 0.90
    assert result["passed"] is False
