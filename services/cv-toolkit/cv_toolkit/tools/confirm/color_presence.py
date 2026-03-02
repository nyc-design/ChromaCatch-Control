"""Color presence tool - HSV range pixel ratio."""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("color_presence")
def color_presence(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Check what percentage of pixels in a region fall within a HSV color range.

    Params (required):
        hsv_lower (list[int, int, int]): Lower HSV bound
        hsv_upper (list[int, int, int]): Upper HSV bound
    """
    if "hsv_lower" not in tool_input.params or "hsv_upper" not in tool_input.params:
        raise ValueError("color_presence requires 'hsv_lower' and 'hsv_upper' in params")

    hsv_lower = np.array(tool_input.params["hsv_lower"], dtype=np.uint8)
    hsv_upper = np.array(tool_input.params["hsv_upper"], dtype=np.uint8)

    img_crop = extract_region(image, tool_input.region)
    hsv = bgr_to_hsv(img_crop)
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)

    matching = int(np.count_nonzero(mask))
    total = mask.size
    score = matching / total if total > 0 else 0.0

    return ToolResult(
        tool="color_presence",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matching_pixels": matching,
            "total_pixels": total,
            "hsv_lower": hsv_lower.tolist(),
            "hsv_upper": hsv_upper.tolist(),
        },
    )
