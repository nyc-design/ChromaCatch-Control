"""Locate color tool - find colored blobs via HSV range and waterfill.

When a reference is provided, auto-derives HSV ranges and scores candidates
by histogram similarity. Without a reference, requires explicit HSV params
and scores by compactness (how circular/regular the blob is).
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from cv_toolkit._utils import (
    auto_color_ranges,
    bgr_to_hsv,
    extract_region,
    waterfill_candidates,
)
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("locate_color")
def locate_color(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Find colored blobs matching an HSV range or a reference image.

    When a reference is provided, auto-derives HSV ranges from it and
    scores candidates by histogram similarity to the reference.
    Without a reference, requires hsv_lower/hsv_upper params and
    scores candidates by blob compactness.

    Params (required if no reference):
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
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.0005))
    max_area_ratio = float(tool_input.params.get("max_area_ratio", 0.5))
    min_aspect = float(tool_input.params.get("min_aspect_ratio", 0.1))
    max_aspect = float(tool_input.params.get("max_aspect_ratio", 10.0))
    morph_size = int(tool_input.params.get("morph_size", 3))
    max_matches = int(tool_input.params.get("max_matches", 10))

    img_crop = extract_region(image, tool_input.region)

    # Determine color filters: from reference or explicit params
    if reference is not None:
        color_filters = auto_color_ranges(reference, k=3)
        if not color_filters:
            return ToolResult(
                tool="locate_color", score=0.0, match=False,
                threshold=tool_input.threshold,
                details={"matches": [], "num_matches": 0},
            )
    else:
        if "hsv_lower" not in tool_input.params or "hsv_upper" not in tool_input.params:
            raise ValueError("locate_color requires 'hsv_lower' and 'hsv_upper' in params (or a reference image)")
        hsv_lower = tool_input.params["hsv_lower"]
        hsv_upper = tool_input.params["hsv_upper"]
        color_filters = [(np.array(hsv_lower, dtype=np.uint8), np.array(hsv_upper, dtype=np.uint8))]

    candidates = waterfill_candidates(
        img_crop,
        color_filters=color_filters,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        min_aspect=min_aspect,
        max_aspect=max_aspect,
        morph_size=morph_size,
    )

    # Score candidates
    if reference is not None:
        # Reference-aware: score by histogram similarity
        ref_hsv = bgr_to_hsv(reference)
        ref_hist = cv2.calcHist([ref_hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(ref_hist, ref_hist, alpha=1.0, norm_type=cv2.NORM_L1)

        matches = []
        for c in candidates[:max_matches]:
            x, y, w, h = c["bbox_px"]
            cand_region = img_crop[y : y + h, x : x + w]
            if cand_region.size == 0:
                continue
            cand_hsv = bgr_to_hsv(cand_region)
            cand_hist = cv2.calcHist([cand_hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
            cv2.normalize(cand_hist, cand_hist, alpha=1.0, norm_type=cv2.NORM_L1)
            dist = cv2.compareHist(ref_hist, cand_hist, cv2.HISTCMP_BHATTACHARYYA)
            confidence = max(0.0, 1.0 - dist)
            matches.append({"bbox": c["bbox_norm"], "confidence": confidence})
    else:
        # No reference: score by compactness (4*pi*area/perimeter^2)
        matches = []
        for c in candidates[:max_matches]:
            contour = c["contour"]
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            if perimeter > 0:
                compactness = min(1.0, 4.0 * math.pi * area / (perimeter * perimeter))
            else:
                compactness = 0.0
            matches.append({"bbox": c["bbox_norm"], "confidence": compactness})

    # Sort by confidence descending
    matches.sort(key=lambda m: m["confidence"], reverse=True)

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
