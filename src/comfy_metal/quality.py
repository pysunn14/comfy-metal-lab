"""Image determinism and SSIM quality evaluation."""

from __future__ import annotations

import hashlib
import statistics
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_rgb(path: Path) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(f"image does not exist: {path}")
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def compute_ssim(reference: Path, candidate: Path) -> float:
    """Return RGB SSIM without resizing or otherwise normalizing dimensions."""

    if _sha256(reference) == _sha256(candidate):
        return 1.0

    left = _load_rgb(reference)
    right = _load_rgb(candidate)
    if left.shape != right.shape:
        raise ValueError(
            "image dimensions must match for SSIM: "
            f"{reference}={left.shape[1]}x{left.shape[0]}, "
            f"{candidate}={right.shape[1]}x{right.shape[0]}"
        )

    return float(
        structural_similarity(
            left,
            right,
            channel_axis=2,
            data_range=255,
        )
    )


def summarize_determinism(
    images: Sequence[Path],
    *,
    unit: str = "session",
) -> dict[str, Any]:
    """Describe same-profile repeat stability without treating mismatch as failure."""

    if not images:
        raise ValueError("determinism summary requires at least one image")

    hashes = [_sha256(path) for path in images]
    pairwise: list[dict[str, Any]] = []
    for left_index, right_index in combinations(range(len(images)), 2):
        score = (
            1.0
            if hashes[left_index] == hashes[right_index]
            else compute_ssim(images[left_index], images[right_index])
        )
        pairwise.append(
            {
                "left_index": left_index,
                "right_index": right_index,
                "ssim": score,
            }
        )

    scores = [float(pair["ssim"]) for pair in pairwise]
    return {
        "unit": unit,
        "count": len(images),
        "exact_hash_match": len(set(hashes)) == 1,
        "sha256": hashes,
        "pairwise_ssim": pairwise,
        "min_intra_ssim": min(scores) if scores else None,
        "median_intra_ssim": statistics.median(scores) if scores else None,
    }


def compare_image_sets(
    baseline_images: Sequence[Path],
    candidate_images: Sequence[Path],
    *,
    min_ssim: float = 0.90,
    unit: str = "session",
) -> dict[str, Any]:
    """Evaluate paired baseline/candidate images with a comparison-local SSIM gate."""

    if not 0.0 <= min_ssim <= 1.0:
        raise ValueError("min_ssim must be between 0 and 1")
    if not baseline_images or not candidate_images:
        raise ValueError("quality comparison requires non-empty image sets")
    if len(baseline_images) != len(candidate_images):
        raise ValueError("baseline and candidate must have the same number of images")

    paired = [
        {
            "index": index,
            "baseline": str(baseline),
            "candidate": str(candidate),
            "ssim": compute_ssim(baseline, candidate),
        }
        for index, (baseline, candidate) in enumerate(
            zip(baseline_images, candidate_images, strict=True)
        )
    ]
    scores = [float(pair["ssim"]) for pair in paired]
    minimum = min(scores)
    return {
        "metric": "ssim",
        "unit": unit,
        "threshold": min_ssim,
        "paired_ssim": paired,
        "min_ssim": minimum,
        "median_ssim": statistics.median(scores),
        "passed": minimum >= min_ssim,
    }
