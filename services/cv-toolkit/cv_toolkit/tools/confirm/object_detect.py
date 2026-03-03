"""Object detect tool - color-filtered connected component detection (waterfill).

This is the equivalent of Pokemon Automation's "waterfill" pipeline:
color filter → binary mask → connected components → filter by geometry → match shapes.

Unlike contour_detect (which uses Canny edge detection), this tool works on
color-filtered binary masks to find discrete colored objects/blobs.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("object_detect")
def object_detect(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Find discrete colored objects via connected component analysis.

    Creates a binary mask from a color range, finds connected components,
    filters by area/aspect ratio, and optionally matches against reference.

    Without reference: score = normalized object count (objects found / expected).
    With reference: runs same pipeline on reference, matches objects by shape
    similarity (Hu moments) and relative area.

    Params (required):
        hsv_lower (list[int,int,int]): Lower HSV bound for object color.
        hsv_upper (list[int,int,int]): Upper HSV bound for object color.

    Params (optional):
        min_area_ratio (float): Min object area as fraction of region. Default 0.0005.
        max_area_ratio (float): Max object area as fraction of region. Default 0.5.
        min_aspect_ratio (float): Min width/height ratio. Default 0.1.
        max_aspect_ratio (float): Max width/height ratio. Default 10.0.
        expected_count (int): Expected number of objects. Score based on match.
        morph_size (int): Morphological cleanup kernel size. Default 3.
    """
    if "hsv_lower" not in tool_input.params or "hsv_upper" not in tool_input.params:
        raise ValueError("object_detect requires 'hsv_lower' and 'hsv_upper' in params")

    hsv_lower = np.array(tool_input.params["hsv_lower"], dtype=np.uint8)
    hsv_upper = np.array(tool_input.params["hsv_upper"], dtype=np.uint8)
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.0005))
    max_area_ratio = float(tool_input.params.get("max_area_ratio", 0.5))
    min_aspect = float(tool_input.params.get("min_aspect_ratio", 0.1))
    max_aspect = float(tool_input.params.get("max_aspect_ratio", 10.0))
    expected_count = tool_input.params.get("expected_count")
    morph_size = int(tool_input.params.get("morph_size", 3))

    img_crop = extract_region(image, tool_input.region)
    img_objects = _find_objects(
        img_crop, hsv_lower, hsv_upper,
        min_area_ratio, max_area_ratio, min_aspect, max_aspect, morph_size,
    )

    details: dict = {
        "num_objects": len(img_objects),
        "objects": [
            {
                "bbox": obj["bbox"],
                "area": obj["area"],
                "aspect_ratio": obj["aspect_ratio"],
            }
            for obj in img_objects
        ],
    }

    if reference is not None:
        ref_crop = extract_region(reference, tool_input.region)
        ref_objects = _find_objects(
            ref_crop, hsv_lower, hsv_upper,
            min_area_ratio, max_area_ratio, min_aspect, max_aspect, morph_size,
        )
        details["ref_num_objects"] = len(ref_objects)

        if not img_objects or not ref_objects:
            score = 0.0
        else:
            # Match objects by shape similarity (Hu moments)
            best_similarities = []
            for img_obj in img_objects:
                best_sim = 0.0
                for ref_obj in ref_objects:
                    dist = cv2.matchShapes(
                        img_obj["contour"], ref_obj["contour"],
                        cv2.CONTOURS_MATCH_I2, 0.0,
                    )
                    sim = 1.0 / (1.0 + dist)
                    best_sim = max(best_sim, sim)
                best_similarities.append(best_sim)

            # Count ratio similarity
            count_sim = 1.0 - abs(len(img_objects) - len(ref_objects)) / max(
                len(img_objects), len(ref_objects)
            )
            shape_sim = sum(best_similarities) / len(best_similarities)
            score = 0.5 * count_sim + 0.5 * shape_sim
            details["count_similarity"] = count_sim
            details["shape_similarity"] = shape_sim

    elif expected_count is not None:
        expected = int(expected_count)
        if expected == 0:
            score = 1.0 if len(img_objects) == 0 else 0.0
        else:
            score = max(0.0, 1.0 - abs(len(img_objects) - expected) / expected)
    else:
        # No reference or expected count — score is 1.0 if any objects found
        score = min(1.0, float(len(img_objects)))

    return ToolResult(
        tool="object_detect",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )


def _find_objects(
    image: np.ndarray,
    hsv_lower: np.ndarray,
    hsv_upper: np.ndarray,
    min_area_ratio: float,
    max_area_ratio: float,
    min_aspect: float,
    max_aspect: float,
    morph_size: int,
) -> list[dict]:
    """Color filter → binary → connected components → filtered objects."""
    hsv = bgr_to_hsv(image)
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)

    # Morphological cleanup (close small gaps, remove noise)
    if morph_size > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (morph_size, morph_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    # Connected components via findContours on binary mask
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    total_area = image.shape[0] * image.shape[1]
    min_area = total_area * min_area_ratio
    max_area = total_area * max_area_ratio

    objects = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area or area > max_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        if h == 0:
            continue
        aspect = w / h

        if aspect < min_aspect or aspect > max_aspect:
            continue

        objects.append({
            "contour": contour,
            "bbox": [x, y, w, h],
            "area": float(area),
            "aspect_ratio": float(aspect),
        })

    objects.sort(key=lambda o: o["area"], reverse=True)
    return objects
