"""Locate multi-scale tool - multi-scale normalized cross-correlation.

Resizes the template to multiple scales, runs cv2.matchTemplate at each
scale, collects peaks, applies NMS, and returns the best matches.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import extract_region, non_max_suppression, normalize_bbox
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool

# Map of method name strings to OpenCV constants
_TM_METHODS = {
    "TM_CCOEFF_NORMED": cv2.TM_CCOEFF_NORMED,
    "TM_CCORR_NORMED": cv2.TM_CCORR_NORMED,
    "TM_SQDIFF_NORMED": cv2.TM_SQDIFF_NORMED,
}


@register_tool("locate_multi_scale")
def locate_multi_scale(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate a reference via multi-scale template matching.

    Resizes the reference template to N scales within a range, runs
    cv2.matchTemplate at each scale, collects peaks above the confidence
    threshold, applies NMS, and returns the best matches.

    Requires reference image.

    Params (optional):
        scale_range (list[float, float]): Min and max scale factors. Default [0.5, 2.0].
        scale_steps (int): Number of scales to test. Default 20.
        method (str): Template matching method name. Default "TM_CCOEFF_NORMED".
        nms_overlap (float): NMS IoU overlap threshold. Default 0.5.
        max_matches (int): Maximum number of matches to return. Default 5.
        confidence_threshold (float): Minimum confidence to keep a peak. Default 0.5.
    """
    if reference is None:
        raise ValueError("locate_multi_scale requires a reference image")

    scale_range = tool_input.params.get("scale_range", [0.5, 2.0])
    scale_steps = int(tool_input.params.get("scale_steps", 20))
    method_name = str(tool_input.params.get("method", "TM_CCOEFF_NORMED"))
    nms_overlap = float(tool_input.params.get("nms_overlap", 0.5))
    max_matches = int(tool_input.params.get("max_matches", 5))
    confidence_threshold = float(tool_input.params.get("confidence_threshold", 0.5))

    method = _TM_METHODS.get(method_name, cv2.TM_CCOEFF_NORMED)
    is_sqdiff = method == cv2.TM_SQDIFF_NORMED

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)
    img_h, img_w = img_crop.shape[:2]

    # Convert to grayscale for template matching
    img_gray = cv2.cvtColor(img_crop, cv2.COLOR_BGR2GRAY)
    ref_gray = cv2.cvtColor(ref_crop, cv2.COLOR_BGR2GRAY)
    ref_h, ref_w = ref_gray.shape[:2]

    scales = np.linspace(scale_range[0], scale_range[1], scale_steps)

    all_peaks: list[dict] = []

    for scale in scales:
        new_w = max(1, int(ref_w * scale))
        new_h = max(1, int(ref_h * scale))

        # Skip if template is larger than image
        if new_w > img_w or new_h > img_h:
            continue
        # Skip if template is too small
        if new_w < 3 or new_h < 3:
            continue

        interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
        scaled_ref = cv2.resize(ref_gray, (new_w, new_h), interpolation=interp)

        result = cv2.matchTemplate(img_gray, scaled_ref, method)

        # Find peaks above threshold
        if is_sqdiff:
            # For SQDIFF, lower is better; invert for consistency
            confidence_map = 1.0 - result
        else:
            confidence_map = result

        # Collect peaks above threshold, capped to avoid combinatorial explosion
        max_peaks_per_scale = max_matches * 10
        locations = np.where(confidence_map >= confidence_threshold)
        if len(locations[0]) == 0:
            continue

        # If too many peaks, keep only the top ones
        confs = confidence_map[locations]
        if len(confs) > max_peaks_per_scale:
            top_idx = np.argpartition(confs, -max_peaks_per_scale)[-max_peaks_per_scale:]
            locations = (locations[0][top_idx], locations[1][top_idx])

        for pt_y, pt_x in zip(*locations):
            conf = float(confidence_map[pt_y, pt_x])
            bbox = normalize_bbox(int(pt_x), int(pt_y), new_w, new_h, img_h, img_w)
            all_peaks.append({"bbox": bbox, "confidence": conf})

    # NMS to deduplicate overlapping detections across scales
    if all_peaks:
        all_peaks = non_max_suppression(all_peaks, overlap_thresh=nms_overlap)

    # Sort by confidence descending, limit results
    all_peaks.sort(key=lambda m: m["confidence"], reverse=True)
    matches = [
        {"bbox": p["bbox"], "confidence": p["confidence"]}
        for p in all_peaks[:max_matches]
    ]

    best_score = matches[0]["confidence"] if matches else 0.0

    return ToolResult(
        tool="locate_multi_scale",
        score=best_score,
        match=len(matches) > 0 and best_score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches,
            "num_matches": len(matches),
        },
    )
