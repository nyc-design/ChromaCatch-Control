"""Histogram similarity tool - color histogram distribution comparison."""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, bgr_to_lab, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool

_COMPARE_METHODS = {
    "correlation": cv2.HISTCMP_CORREL,
    "chi_squared": cv2.HISTCMP_CHISQR,
    "intersection": cv2.HISTCMP_INTERSECT,
    "bhattacharyya": cv2.HISTCMP_BHATTACHARYYA,
}


@register_tool("histogram_similarity")
def histogram_similarity(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Compare color histogram distributions between image and reference.

    Params:
        color_space (str): "hsv" or "lab". Default "hsv".
        method (str): "correlation", "chi_squared", "intersection", "bhattacharyya".
            Default "correlation".
        bins (int): Histogram bins per channel. Default 32.
    """
    if reference is None:
        raise ValueError("histogram_similarity requires a reference image")

    color_space = tool_input.params.get("color_space", "hsv")
    method_name = tool_input.params.get("method", "correlation")
    bins = int(tool_input.params.get("bins", 32))

    cv_method = _COMPARE_METHODS.get(method_name)
    if cv_method is None:
        raise ValueError(
            f"Unknown method: {method_name!r}. "
            f"Available: {sorted(_COMPARE_METHODS.keys())}"
        )

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    if color_space == "lab":
        img_conv = bgr_to_lab(img_crop).astype(np.uint8)
        ref_conv = bgr_to_lab(ref_crop).astype(np.uint8)
        ranges = [0, 256, 0, 256, 0, 256]
    else:
        img_conv = bgr_to_hsv(img_crop)
        ref_conv = bgr_to_hsv(ref_crop)
        ranges = [0, 180, 0, 256, 0, 256]

    # Compute per-channel histograms and compare
    channel_scores = []
    for ch in range(3):
        img_hist = cv2.calcHist([img_conv], [ch], None, [bins], ranges[ch * 2 : ch * 2 + 2])
        ref_hist = cv2.calcHist([ref_conv], [ch], None, [bins], ranges[ch * 2 : ch * 2 + 2])
        cv2.normalize(img_hist, img_hist)
        cv2.normalize(ref_hist, ref_hist)
        raw = cv2.compareHist(img_hist, ref_hist, cv_method)
        channel_scores.append(float(raw))

    # Normalize method-specific output to 0.0-1.0
    avg_raw = sum(channel_scores) / len(channel_scores)

    if method_name == "correlation":
        # Range [-1, 1], higher is better
        score = (avg_raw + 1.0) / 2.0
    elif method_name == "chi_squared":
        # Range [0, inf), lower is better
        score = max(0.0, 1.0 / (1.0 + avg_raw))
    elif method_name == "intersection":
        # Range [0, 1] when normalized, higher is better
        score = min(1.0, max(0.0, avg_raw))
    elif method_name == "bhattacharyya":
        # Range [0, 1], lower is better
        score = max(0.0, 1.0 - avg_raw)
    else:
        score = 0.0

    return ToolResult(
        tool="histogram_similarity",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "method": method_name,
            "color_space": color_space,
            "per_channel_raw": channel_scores,
            "raw_score": avg_raw,
        },
    )
