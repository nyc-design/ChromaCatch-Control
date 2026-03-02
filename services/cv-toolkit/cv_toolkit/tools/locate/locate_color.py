"""Locate color tool - find colored blobs via HSV range and waterfill."""

from __future__ import annotations

import numpy as np

from cv_toolkit._utils import extract_region, waterfill_candidates
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("locate_color")
def locate_color(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Find colored blobs matching an HSV range.

    Uses waterfill_candidates() to find connected components within the
    specified color range. Returns bounding boxes sorted by area descending.

    Params (required):
        hsv_lower (list[int,int,int]): Lower HSV bound.
        hsv_upper (list[int,int,int]): Upper HSV bound.

    Params (optional):
        min_area_ratio (float): Min blob area as fraction of region. Default 0.0005.
        max_area_ratio (float): Max blob area as fraction of region. Default 0.5.
        min_aspect_ratio (float): Min width/height ratio. Default 0.1.
        max_aspect_ratio (float): Max width/height ratio. Default 10.0.
        morph_size (int): Morphological cleanup kernel size. Default 3.
        max_matches (int): Maximum number of matches to return. Default 10.
    """
    if "hsv_lower" not in tool_input.params or "hsv_upper" not in tool_input.params:
        raise ValueError("locate_color requires 'hsv_lower' and 'hsv_upper' in params")

    hsv_lower = tool_input.params["hsv_lower"]
    hsv_upper = tool_input.params["hsv_upper"]
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.0005))
    max_area_ratio = float(tool_input.params.get("max_area_ratio", 0.5))
    min_aspect = float(tool_input.params.get("min_aspect_ratio", 0.1))
    max_aspect = float(tool_input.params.get("max_aspect_ratio", 10.0))
    morph_size = int(tool_input.params.get("morph_size", 3))
    max_matches = int(tool_input.params.get("max_matches", 10))

    img_crop = extract_region(image, tool_input.region)

    color_filters = [(np.array(hsv_lower, dtype=np.uint8),
                      np.array(hsv_upper, dtype=np.uint8))]

    candidates = waterfill_candidates(
        img_crop,
        color_filters=color_filters,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        min_aspect=min_aspect,
        max_aspect=max_aspect,
        morph_size=morph_size,
    )

    # Limit to max_matches, confidence is 1.0 for all (binary color match)
    matches = [
        {"bbox": c["bbox_norm"], "confidence": 1.0}
        for c in candidates[:max_matches]
    ]

    score = matches[0]["confidence"] if matches else 0.0

    return ToolResult(
        tool="locate_color",
        score=score,
        match=len(matches) > 0 and score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches,
            "num_matches": len(matches),
        },
    )
