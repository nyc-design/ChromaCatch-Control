"""Contour detect tool - contour isolation and shape matching via Hu moments."""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("contour_detect")
def contour_detect(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Isolate contours and optionally match shapes against a reference.

    Without reference: score = normalized contour count.
    With reference: score = shape similarity of best-matching contour pair.

    Params:
        canny_low (int): Canny lower threshold. Default 50.
        canny_high (int): Canny upper threshold. Default 150.
        min_area_ratio (float): Min contour area as fraction of image area. Default 0.001.
        max_contours (int): Max contours to keep. Default 20.
    """
    canny_low = int(tool_input.params.get("canny_low", 50))
    canny_high = int(tool_input.params.get("canny_high", 150))
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.001))
    max_contours = int(tool_input.params.get("max_contours", 20))

    img_crop = extract_region(image, tool_input.region)
    img_contours = _find_contours(img_crop, canny_low, canny_high, min_area_ratio, max_contours)

    details: dict = {
        "num_contours": len(img_contours),
        "contour_areas": [float(cv2.contourArea(c)) for c in img_contours],
    }

    if reference is not None:
        ref_crop = extract_region(reference, tool_input.region)
        ref_contours = _find_contours(ref_crop, canny_low, canny_high, min_area_ratio, max_contours)

        if not img_contours or not ref_contours:
            score = 0.0
            details["match_distance"] = float("inf")
        else:
            best_dist = float("inf")
            for ic in img_contours:
                for rc in ref_contours:
                    dist = cv2.matchShapes(ic, rc, cv2.CONTOURS_MATCH_I2, 0.0)
                    best_dist = min(best_dist, dist)
            score = 1.0 / (1.0 + best_dist)
            details["match_distance"] = best_dist
            details["ref_num_contours"] = len(ref_contours)
    else:
        score = min(1.0, len(img_contours) / max(1, max_contours))

    return ToolResult(
        tool="contour_detect",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )


def _find_contours(
    image: np.ndarray,
    canny_low: int,
    canny_high: int,
    min_area_ratio: float,
    max_contours: int,
) -> list[np.ndarray]:
    """Extract filtered contours from an image."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, canny_low, canny_high)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    total_area = image.shape[0] * image.shape[1]
    min_area = total_area * min_area_ratio

    filtered = [c for c in contours if cv2.contourArea(c) >= min_area]
    filtered.sort(key=cv2.contourArea, reverse=True)
    return filtered[:max_contours]
