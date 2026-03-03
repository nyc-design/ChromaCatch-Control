"""Color distance tool - Delta-E from dominant color to target."""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_lab, delta_e_to_score, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("color_distance")
def color_distance(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Compute Delta-E (CIELAB) between region's mean color and a target.

    Target is determined by (in priority order):
    1. params["target_lab"] — direct LAB values [L, a, b]
    2. params["target_bgr"] — BGR values [B, G, R], converted to LAB
    3. reference image — mean LAB of reference region

    Params:
        target_lab (list[float, float, float]): Target in LAB space
        target_bgr (list[int, int, int]): Target in BGR space
        max_delta_e (float): Delta-E value that maps to score=0. Default 100.
    """
    max_delta_e = float(tool_input.params.get("max_delta_e", 100.0))

    img_crop = extract_region(image, tool_input.region)
    img_lab = bgr_to_lab(img_crop)
    img_mean_lab = np.mean(img_lab.reshape(-1, 3), axis=0)

    if "target_lab" in tool_input.params:
        target_lab = np.array(tool_input.params["target_lab"], dtype=np.float32)
    elif "target_bgr" in tool_input.params:
        bgr = np.array([[tool_input.params["target_bgr"]]], dtype=np.uint8)
        target_lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)[0, 0]
    elif reference is not None:
        ref_crop = extract_region(reference, tool_input.region)
        ref_lab = bgr_to_lab(ref_crop)
        target_lab = np.mean(ref_lab.reshape(-1, 3), axis=0)
    else:
        raise ValueError(
            "color_distance requires a target: "
            "params['target_lab'], params['target_bgr'], or a reference image"
        )

    diff = img_mean_lab.astype(np.float64) - target_lab.astype(np.float64)
    de = float(np.sqrt(np.sum(diff**2)))
    score = delta_e_to_score(de, max_delta_e)

    return ToolResult(
        tool="color_distance",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "delta_e": de,
            "image_mean_lab": img_mean_lab.tolist(),
            "target_lab": target_lab.tolist(),
        },
    )
