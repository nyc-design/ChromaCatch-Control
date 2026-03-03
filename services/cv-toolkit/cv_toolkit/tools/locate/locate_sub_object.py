"""Locate sub-object tool - PA's sub-feature localization.

Finds a distinctive sub-feature of an object via waterfill, then infers
the full object position based on the known spatial relationship between
the sub-feature and the full object in the reference.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import (
    brightness_scale,
    compute_rmsd,
    extract_region,
    non_max_suppression,
    normalize_bbox,
    waterfill_candidates,
)
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("locate_sub_object")
def locate_sub_object(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate an object by finding a distinctive sub-feature.

    Finds the sub-feature via waterfill in the image, then infers the full
    object bounding box from the known spatial relationship between sub-feature
    and full object in the reference.

    Requires reference (full object image).

    Params (required):
        sub_reference_region (dict): Normalized region {x, y, w, h} of the
            sub-feature within the reference image.
        sub_color_filters (list[list[list[int]]]): Color filters for finding
            the sub-feature, as list of [hsv_lower, hsv_upper] pairs.

    Params (optional):
        sub_rmsd_threshold (float): RMSD threshold for sub-feature matching. Default 80.0.
        brightness_scale_enabled (bool): Apply brightness scaling. Default True.
        brightness_clamp (float): Brightness scale clamp factor. Default 0.15.
        max_matches (int): Maximum number of matches to return. Default 5.
        nms_overlap (float): NMS IoU overlap threshold. Default 0.5.
        min_area_ratio (float): Min blob area ratio. Default 0.0005.
        max_area_ratio (float): Max blob area ratio. Default 0.5.
    """
    if reference is None:
        raise ValueError("locate_sub_object requires a reference image")
    if "sub_reference_region" not in tool_input.params:
        raise ValueError("locate_sub_object requires 'sub_reference_region' in params")
    if "sub_color_filters" not in tool_input.params:
        raise ValueError("locate_sub_object requires 'sub_color_filters' in params")

    sub_region = tool_input.params["sub_reference_region"]
    raw_filters = tool_input.params["sub_color_filters"]
    color_filters = [
        (np.array(f[0], dtype=np.uint8), np.array(f[1], dtype=np.uint8))
        for f in raw_filters
    ]

    sub_rmsd_threshold = float(tool_input.params.get("sub_rmsd_threshold", 80.0))
    brightness_enabled = bool(tool_input.params.get("brightness_scale_enabled", True))
    brightness_clamp = float(tool_input.params.get("brightness_clamp", 0.15))
    max_matches = int(tool_input.params.get("max_matches", 5))
    nms_overlap = float(tool_input.params.get("nms_overlap", 0.5))
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.0005))
    max_area_ratio = float(tool_input.params.get("max_area_ratio", 0.5))

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)
    img_h, img_w = img_crop.shape[:2]
    ref_h, ref_w = ref_crop.shape[:2]

    # Extract the sub-feature from the reference
    sub_x = int(sub_region["x"] * ref_w)
    sub_y = int(sub_region["y"] * ref_h)
    sub_w = int(sub_region["w"] * ref_w)
    sub_h = int(sub_region["h"] * ref_h)
    sub_w = max(1, sub_w)
    sub_h = max(1, sub_h)
    sub_ref = ref_crop[sub_y : sub_y + sub_h, sub_x : sub_x + sub_w]

    # Find sub-feature candidates in the image via waterfill
    candidates = waterfill_candidates(
        img_crop,
        color_filters=color_filters,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
    )

    scored: list[dict] = []
    for cand in candidates:
        cx, cy, cw, ch = cand["bbox_px"]
        if cw < 1 or ch < 1:
            continue

        # Crop candidate from image
        cand_crop = img_crop[cy : cy + ch, cx : cx + cw]

        # Resize sub-reference to candidate size
        sub_ref_resized = cv2.resize(sub_ref, (cw, ch), interpolation=cv2.INTER_AREA)

        # Brightness scaling
        if brightness_enabled:
            cand_crop = brightness_scale(
                cand_crop, sub_ref_resized, clamp=brightness_clamp
            )

        # Compute RMSD
        rmsd = compute_rmsd(cand_crop, sub_ref_resized)
        confidence = max(0.0, 1.0 - rmsd / sub_rmsd_threshold)

        if confidence <= 0:
            continue

        # Infer full object position from sub-feature location
        # sub_region tells us where the sub-feature sits within the full reference
        # So the full object extends from the sub-feature position based on the ratio
        sx = sub_region["x"]
        sy = sub_region["y"]
        sw = sub_region["w"]
        sh = sub_region["h"]

        # Avoid division by zero
        if sw <= 0 or sh <= 0:
            continue

        # Scale from sub-feature bbox to full object bbox
        obj_w_px = cw / sw
        obj_h_px = ch / sh
        obj_x_px = cx - sx * obj_w_px
        obj_y_px = cy - sy * obj_h_px

        # Clamp to image bounds
        obj_x_px = max(0, int(obj_x_px))
        obj_y_px = max(0, int(obj_y_px))
        obj_w_px = min(img_w - obj_x_px, int(obj_w_px))
        obj_h_px = min(img_h - obj_y_px, int(obj_h_px))

        if obj_w_px <= 0 or obj_h_px <= 0:
            continue

        bbox = normalize_bbox(obj_x_px, obj_y_px, obj_w_px, obj_h_px, img_h, img_w)
        scored.append({"bbox": bbox, "confidence": confidence})

    # NMS to deduplicate overlapping detections
    if scored:
        scored = non_max_suppression(scored, overlap_thresh=nms_overlap)

    # Sort by confidence descending, limit results
    scored.sort(key=lambda m: m["confidence"], reverse=True)
    matches = [
        {"bbox": m["bbox"], "confidence": m["confidence"]}
        for m in scored[:max_matches]
    ]

    best_score = matches[0]["confidence"] if matches else 0.0

    return ToolResult(
        tool="locate_sub_object",
        score=best_score,
        match=len(matches) > 0 and best_score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches,
            "num_matches": len(matches),
        },
    )
