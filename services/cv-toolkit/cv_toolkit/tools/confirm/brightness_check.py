"""Brightness check tool - average luminosity analysis."""

from __future__ import annotations

import numpy as np

from cv_toolkit._utils import bgr_to_lab, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("brightness_check")
def brightness_check(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Compute average luminosity of a region.

    If reference provided, score = how close brightness matches reference.
    If params["expected_brightness"] set (0.0-1.0), compare against that.
    Otherwise score = normalized brightness (0=black, 1=white).

    Params:
        expected_brightness (float): Target brightness 0.0-1.0
        tolerance (float): Max difference before score=0. Default 0.5.
    """
    region = tool_input.region
    tolerance = tool_input.params.get("tolerance", 0.5)

    img_crop = extract_region(image, region)
    lab = bgr_to_lab(img_crop)
    # OpenCV LAB L-channel is 0-255 (not 0-100)
    mean_brightness = float(np.mean(lab[:, :, 0])) / 255.0

    details: dict = {"mean_brightness": mean_brightness}

    if reference is not None:
        ref_crop = extract_region(reference, region)
        ref_lab = bgr_to_lab(ref_crop)
        ref_brightness = float(np.mean(ref_lab[:, :, 0])) / 255.0
        diff = abs(mean_brightness - ref_brightness)
        score = max(0.0, 1.0 - diff / tolerance)
        details["ref_brightness"] = ref_brightness
        details["diff"] = diff
    elif "expected_brightness" in tool_input.params:
        expected = float(tool_input.params["expected_brightness"])
        diff = abs(mean_brightness - expected)
        score = max(0.0, 1.0 - diff / tolerance)
        details["expected_brightness"] = expected
        details["diff"] = diff
    else:
        score = mean_brightness

    return ToolResult(
        tool="brightness_check",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )
