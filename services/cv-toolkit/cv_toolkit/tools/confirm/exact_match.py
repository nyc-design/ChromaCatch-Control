"""Exact match tool - brightness-scaled RMSD with alpha masking.

PA's ExactImageMatcher technique: direct pixel-level comparison with
brightness normalization, alpha channel masking, and stddev-weighted
RMSD normalization.

Different from grid_similarity (which uses per-cell Delta-E in LAB space).
This operates in BGR space with PA-specific preprocessing.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import brightness_scale, compute_rmsd, extract_region, stddev_weight
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("exact_match")
def exact_match(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Compare images using brightness-scaled, stddev-weighted RMSD.

    Params (optional):
        brightness_clamp (float): Max brightness scaling delta. Default 0.15.
        alpha_channel (bool): Use reference alpha for masking. Default False.
        use_stddev_weight (bool): Apply stddev-based normalization. Default True.
        rmsd_max (float): RMSD value that maps to score 0.0. Default 100.0.
    """
    if reference is None:
        raise ValueError("exact_match requires a reference image")

    brightness_clamp = float(tool_input.params.get("brightness_clamp", 0.15))
    use_alpha = bool(tool_input.params.get("alpha_channel", False))
    use_stddev = bool(tool_input.params.get("use_stddev_weight", True))
    rmsd_max = float(tool_input.params.get("rmsd_max", 100.0))

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    # Extract alpha mask before converting reference to BGR
    alpha_mask = None
    if use_alpha and ref_crop.shape[-1] == 4:
        alpha_mask = ref_crop[:, :, 3]
        ref_crop = ref_crop[:, :, :3]

    # Resize to common dimensions (use the larger of the two)
    target_h = max(img_crop.shape[0], ref_crop.shape[0])
    target_w = max(img_crop.shape[1], ref_crop.shape[1])
    if img_crop.shape[:2] != (target_h, target_w):
        img_crop = cv2.resize(img_crop, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
    if ref_crop.shape[:2] != (target_h, target_w):
        ref_crop = cv2.resize(ref_crop, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
    if alpha_mask is not None and alpha_mask.shape[:2] != (target_h, target_w):
        alpha_mask = cv2.resize(alpha_mask, (target_w, target_h), interpolation=cv2.INTER_NEAREST)

    # Brightness scaling — when alpha masking, only use opaque pixels for mean
    if alpha_mask is not None:
        opaque = alpha_mask > 0
        if opaque.any():
            opaque_3d = np.expand_dims(opaque, axis=-1)
            ref_for_brightness = np.where(opaque_3d, ref_crop, img_crop)
        else:
            ref_for_brightness = ref_crop
        scaled_img = brightness_scale(img_crop, ref_for_brightness, clamp=brightness_clamp)
    else:
        scaled_img = brightness_scale(img_crop, ref_crop, clamp=brightness_clamp)

    # Compute RMSD
    raw_rmsd = compute_rmsd(scaled_img, ref_crop, alpha_mask=alpha_mask)

    # Stddev-weighted normalization
    weight = stddev_weight(ref_crop) if use_stddev else 1.0
    normalized_rmsd = raw_rmsd * weight

    score = max(0.0, min(1.0, 1.0 - normalized_rmsd / rmsd_max))

    details: dict = {
        "rmsd": raw_rmsd,
        "normalized_rmsd": normalized_rmsd,
        "stddev_weight": weight,
    }
    if alpha_mask is not None:
        details["masked_pixels"] = int((alpha_mask > 0).sum())
        details["total_pixels"] = int(alpha_mask.size)

    return ToolResult(
        tool="exact_match",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )
