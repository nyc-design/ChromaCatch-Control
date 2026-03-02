"""SSIM compare tool - Structural Similarity Index Measure.

Manual SSIM implementation using cv2.GaussianBlur + numpy.
No scikit-image dependency required.

SSIM is more robust to compression artifacts and minor spatial shifts
than pixel-level RMSD comparisons.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


def _compute_ssim(
    img1: np.ndarray, img2: np.ndarray,
    window_size: int = 11, k1: float = 0.01, k2: float = 0.03,
) -> tuple[float, float, float, float]:
    """Compute SSIM between two grayscale images.

    Returns (ssim, luminance, contrast, structure) components.
    """
    c1 = (k1 * 255) ** 2
    c2 = (k2 * 255) ** 2
    c3 = c2 / 2.0

    f1 = img1.astype(np.float64)
    f2 = img2.astype(np.float64)

    mu1 = cv2.GaussianBlur(f1, (window_size, window_size), 1.5)
    mu2 = cv2.GaussianBlur(f2, (window_size, window_size), 1.5)

    mu1_sq = mu1 ** 2
    mu2_sq = mu2 ** 2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = cv2.GaussianBlur(f1 ** 2, (window_size, window_size), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(f2 ** 2, (window_size, window_size), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(f1 * f2, (window_size, window_size), 1.5) - mu1_mu2

    # Clamp negative variances (numerical precision)
    sigma1_sq = np.maximum(sigma1_sq, 0.0)
    sigma2_sq = np.maximum(sigma2_sq, 0.0)

    sigma1 = np.sqrt(sigma1_sq)
    sigma2 = np.sqrt(sigma2_sq)

    luminance = (2 * mu1_mu2 + c1) / (mu1_sq + mu2_sq + c1)
    contrast = (2 * sigma1 * sigma2 + c2) / (sigma1_sq + sigma2_sq + c2)
    denom = sigma1 * sigma2 + c3
    structure = np.where(denom > 0, (sigma12 + c3) / denom, 1.0)

    ssim_map = luminance * contrast * structure

    return (
        float(ssim_map.mean()),
        float(luminance.mean()),
        float(contrast.mean()),
        float(structure.mean()),
    )


@register_tool("ssim_compare")
def ssim_compare(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Compare images using Structural Similarity Index.

    Params (optional):
        window_size (int): Gaussian window size. Default 11.
        k1 (float): SSIM constant. Default 0.01.
        k2 (float): SSIM constant. Default 0.03.
    """
    if reference is None:
        raise ValueError("ssim_compare requires a reference image")

    window_size = int(tool_input.params.get("window_size", 11))
    k1 = float(tool_input.params.get("k1", 0.01))
    k2 = float(tool_input.params.get("k2", 0.03))

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    # Resize to common dimensions
    target_h = max(img_crop.shape[0], ref_crop.shape[0])
    target_w = max(img_crop.shape[1], ref_crop.shape[1])
    if img_crop.shape[:2] != (target_h, target_w):
        img_crop = cv2.resize(img_crop, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
    if ref_crop.shape[:2] != (target_h, target_w):
        ref_crop = cv2.resize(ref_crop, (target_w, target_h), interpolation=cv2.INTER_CUBIC)

    # Convert to grayscale
    gray1 = cv2.cvtColor(img_crop, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(ref_crop, cv2.COLOR_BGR2GRAY)

    # Ensure minimum size for window
    if gray1.shape[0] < window_size or gray1.shape[1] < window_size:
        window_size = min(gray1.shape[0], gray1.shape[1])
        if window_size % 2 == 0:
            window_size = max(1, window_size - 1)

    ssim_val, luminance, contrast, structure = _compute_ssim(
        gray1, gray2, window_size, k1, k2
    )

    # Map SSIM from [-1, 1] to [0, 1]
    score = max(0.0, min(1.0, (ssim_val + 1.0) / 2.0))

    return ToolResult(
        tool="ssim_compare",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "ssim": ssim_val,
            "luminance": luminance,
            "contrast": contrast,
            "structure": structure,
        },
    )
