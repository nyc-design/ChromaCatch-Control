"""Grid similarity tool - faux-pixel absolute color comparison."""

from __future__ import annotations

import numpy as np

from cv_toolkit._utils import (
    bgr_to_lab,
    compute_grid_size,
    delta_e_cie76,
    delta_e_to_score,
    extract_region,
    resample_to_grid,
)
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("grid_similarity")
def grid_similarity(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Faux-pixel grid comparison with absolute colors.

    Resamples both images to the same faux-pixel grid, converts to CIELAB,
    computes per-cell Delta-E, and returns average similarity score.

    Params:
        grid_size (int): Override faux-pixel grid longest axis. Auto-calculated if omitted.
        max_delta_e (float): Delta-E value mapping to score=0. Default 100.
    """
    if reference is None:
        raise ValueError("grid_similarity requires a reference image")

    max_delta_e = float(tool_input.params.get("max_delta_e", 100.0))
    override = tool_input.params.get("grid_size")
    if override is not None:
        override = int(override)

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    grid_size = compute_grid_size(img_crop, ref_crop, override=override)
    img_grid = resample_to_grid(img_crop, grid_size)
    ref_grid = resample_to_grid(ref_crop, grid_size)

    # Ensure same shape (may differ by 1px due to rounding)
    min_h = min(img_grid.shape[0], ref_grid.shape[0])
    min_w = min(img_grid.shape[1], ref_grid.shape[1])
    img_grid = img_grid[:min_h, :min_w]
    ref_grid = ref_grid[:min_h, :min_w]

    img_lab = bgr_to_lab(img_grid)
    ref_lab = bgr_to_lab(ref_grid)

    per_pixel_de = delta_e_cie76(img_lab, ref_lab)
    mean_de = float(np.mean(per_pixel_de))
    score = delta_e_to_score(mean_de, max_delta_e)

    return ToolResult(
        tool="grid_similarity",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "mean_delta_e": mean_de,
            "grid_shape": list(img_grid.shape[:2]),
        },
    )
