"""Locate contour tool - find shapes via Hu moment matching.

Finds contours in the image and matches each against reference contours
using cv2.matchShapes, then verifies matches via HSV histogram comparison
to prevent shape-only false positives (e.g. white circle matching red circle).
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region, normalize_bbox
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("locate_contour")
def locate_contour(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate shapes matching a reference via Hu moment + color histogram.

    Extracts contours from the image (via HSV color filter or Canny edges),
    matches each against reference contours via Hu moments, then verifies
    with HSV histogram comparison. Returns bounding boxes of best matches.

    Requires reference image.

    Params (optional):
        hsv_lower (list[int,int,int]): Lower HSV bound for color filtering.
        hsv_upper (list[int,int,int]): Upper HSV bound for color filtering.
        canny_low (int): Canny lower threshold (used if no HSV). Default 50.
        canny_high (int): Canny upper threshold (used if no HSV). Default 150.
        min_hu_similarity (float): Minimum similarity to keep. Default 0.3.
        min_area_ratio (float): Min contour area as fraction of image. Default 0.001.
        max_matches (int): Maximum number of matches to return. Default 10.
    """
    if reference is None:
        raise ValueError("locate_contour requires a reference image")

    hsv_lower = tool_input.params.get("hsv_lower")
    hsv_upper = tool_input.params.get("hsv_upper")
    canny_low = int(tool_input.params.get("canny_low", 50))
    canny_high = int(tool_input.params.get("canny_high", 150))
    min_hu_similarity = float(tool_input.params.get("min_hu_similarity", 0.3))
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.001))
    max_matches = int(tool_input.params.get("max_matches", 10))

    use_color = hsv_lower is not None and hsv_upper is not None

    img_crop = extract_region(image, tool_input.region)
    # Use full reference — region specifies where to search in the image.
    ref_crop = reference.copy()
    img_h, img_w = img_crop.shape[:2]

    # Extract contours from image and reference
    img_contours = _extract_contours(
        img_crop, use_color, hsv_lower, hsv_upper, canny_low, canny_high,
        min_area_ratio,
    )
    ref_contours = _extract_contours(
        ref_crop, use_color, hsv_lower, hsv_upper, canny_low, canny_high,
        min_area_ratio,
    )

    if not img_contours or not ref_contours:
        return ToolResult(
            tool="locate_contour",
            score=0.0,
            match=False,
            threshold=tool_input.threshold,
            details={"matches": [], "num_matches": 0},
        )

    # Compute reference histogram for color verification
    ref_hsv = bgr_to_hsv(ref_crop)
    ref_hist = cv2.calcHist([ref_hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(ref_hist, ref_hist, alpha=1.0, norm_type=cv2.NORM_L1)

    # Match each image contour against all reference contours
    scored: list[dict] = []
    for contour in img_contours:
        best_dist = float("inf")
        for ref_c in ref_contours:
            dist = cv2.matchShapes(contour, ref_c, cv2.CONTOURS_MATCH_I2, 0.0)
            best_dist = min(best_dist, dist)

        hu_score = 1.0 / (1.0 + best_dist)
        if hu_score < min_hu_similarity:
            continue

        x, y, w, h = cv2.boundingRect(contour)

        # Color histogram verification: compare candidate region to reference
        cand_region = img_crop[y : y + h, x : x + w]
        if cand_region.size == 0:
            continue
        cand_hsv = bgr_to_hsv(cand_region)
        cand_hist = cv2.calcHist([cand_hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(cand_hist, cand_hist, alpha=1.0, norm_type=cv2.NORM_L1)
        hist_dist = cv2.compareHist(ref_hist, cand_hist, cv2.HISTCMP_BHATTACHARYYA)
        hist_score = 1.0 - hist_dist  # 0=no overlap, 1=identical

        # Combined score: shape + color
        confidence = hu_score * 0.5 + hist_score * 0.5

        if confidence >= min_hu_similarity:
            bbox = normalize_bbox(x, y, w, h, img_h, img_w)
            scored.append({"bbox": bbox, "confidence": confidence})

    # Sort by confidence descending, limit results
    scored.sort(key=lambda m: m["confidence"], reverse=True)
    matches = scored[:max_matches]

    best_score = matches[0]["confidence"] if matches else 0.0

    return ToolResult(
        tool="locate_contour",
        score=best_score,
        match=len(matches) > 0 and best_score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches,
            "num_matches": len(matches),
        },
    )


def _extract_contours(
    image: np.ndarray,
    use_color: bool,
    hsv_lower: list | None,
    hsv_upper: list | None,
    canny_low: int,
    canny_high: int,
    min_area_ratio: float,
) -> list[np.ndarray]:
    """Extract filtered contours from an image via color mask or Canny edges."""
    total_area = image.shape[0] * image.shape[1]
    min_area = total_area * min_area_ratio

    if use_color:
        hsv = bgr_to_hsv(image)
        lower = np.array(hsv_lower, dtype=np.uint8)
        upper = np.array(hsv_upper, dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, canny_low, canny_high)
        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

    filtered = [c for c in contours if cv2.contourArea(c) >= min_area]
    filtered.sort(key=cv2.contourArea, reverse=True)
    return filtered
