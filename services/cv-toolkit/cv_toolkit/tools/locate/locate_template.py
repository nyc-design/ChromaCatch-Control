"""Locate template tool - PA's waterfill-then-RMSD object matching.

This is NOT sliding-window template matching. Instead:
1. Color filter → waterfill candidates (connected components)
2. Crop each candidate from the image
3. Resize reference to candidate size
4. Brightness-scaled RMSD comparison
5. NMS to deduplicate overlapping detections
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import (
    brightness_scale,
    compute_rmsd,
    extract_region,
    non_max_suppression,
    waterfill_candidates,
)
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("locate_template")
def locate_template(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate objects matching a reference via waterfill + RMSD.

    Requires reference image. Uses color filters to find candidate blobs,
    then compares each candidate against the reference using brightness-scaled
    RMSD.

    Params (required):
        color_filters (list[list[list[int]]]): List of [hsv_lower, hsv_upper] pairs.

    Params (optional):
        min_area_ratio (float): Min blob area as fraction of region. Default 0.0005.
        max_area_ratio (float): Max blob area as fraction of region. Default 0.5.
        min_aspect_ratio (float): Min width/height ratio. Default 0.1.
        max_aspect_ratio (float): Max width/height ratio. Default 10.0.
        morph_size (int): Morphological cleanup kernel size. Default 3.
        rmsd_threshold (float): RMSD value below which a match is considered. Default 80.0.
        brightness_scale_enabled (bool): Apply brightness scaling. Default True.
        brightness_clamp (float): Brightness scale clamp factor. Default 0.15.
        max_matches (int): Maximum number of matches to return. Default 10.
        nms_overlap (float): NMS IoU overlap threshold. Default 0.5.
    """
    if reference is None:
        raise ValueError("locate_template requires a reference image")
    if "color_filters" not in tool_input.params:
        raise ValueError("locate_template requires 'color_filters' in params")

    raw_filters = tool_input.params["color_filters"]
    color_filters = [
        (np.array(f[0], dtype=np.uint8), np.array(f[1], dtype=np.uint8))
        for f in raw_filters
    ]

    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.0005))
    max_area_ratio = float(tool_input.params.get("max_area_ratio", 0.5))
    min_aspect = float(tool_input.params.get("min_aspect_ratio", 0.1))
    max_aspect = float(tool_input.params.get("max_aspect_ratio", 10.0))
    morph_size = int(tool_input.params.get("morph_size", 3))
    rmsd_threshold = float(tool_input.params.get("rmsd_threshold", 80.0))
    brightness_enabled = bool(tool_input.params.get("brightness_scale_enabled", True))
    brightness_clamp = float(tool_input.params.get("brightness_clamp", 0.15))
    max_matches = int(tool_input.params.get("max_matches", 10))
    nms_overlap = float(tool_input.params.get("nms_overlap", 0.5))

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    candidates = waterfill_candidates(
        img_crop,
        color_filters=color_filters,
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        min_aspect=min_aspect,
        max_aspect=max_aspect,
        morph_size=morph_size,
    )

    scored: list[dict] = []
    for cand in candidates:
        x, y, w, h = cand["bbox_px"]
        if w < 1 or h < 1:
            continue

        # Crop candidate from image
        cand_crop = img_crop[y : y + h, x : x + w]

        # Resize reference to candidate size
        ref_resized = cv2.resize(ref_crop, (w, h), interpolation=cv2.INTER_AREA)

        # Brightness scaling
        if brightness_enabled:
            cand_crop = brightness_scale(cand_crop, ref_resized, clamp=brightness_clamp)

        # Compute RMSD
        rmsd = compute_rmsd(cand_crop, ref_resized)
        confidence = max(0.0, 1.0 - rmsd / rmsd_threshold)

        if confidence > 0:
            scored.append({
                "bbox": cand["bbox_norm"],
                "confidence": confidence,
                "rmsd": rmsd,
            })

    # NMS to deduplicate overlapping detections
    if scored:
        scored = non_max_suppression(scored, overlap_thresh=nms_overlap)

    # Sort by confidence descending, limit results
    scored.sort(key=lambda m: m["confidence"], reverse=True)
    scored = scored[:max_matches]

    # Build output matches (strip internal fields)
    matches = [
        {"bbox": m["bbox"], "confidence": m["confidence"]}
        for m in scored
    ]

    best_score = matches[0]["confidence"] if matches else 0.0

    return ToolResult(
        tool="locate_template",
        score=best_score,
        match=len(matches) > 0 and best_score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches,
            "num_matches": len(matches),
        },
    )
