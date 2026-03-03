"""Motion detect tool - frame-to-frame change detection via faux-pixel RMSD."""

from __future__ import annotations

import numpy as np

from cv_toolkit._utils import compute_grid_size, extract_region, resample_to_grid
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("motion_detect")
def motion_detect(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Compare two frames to measure how much changed.

    Score is INVERTED: high score = screens are similar (no motion),
    low score = lots of change. Use for action verification — snapshot
    before action, act, compare after.

    Reference = previous frame (before action).

    Params:
        grid_size (int): Override faux-pixel grid size.
        sensitivity (float): Scaling factor for change detection. Default 1.0.
    """
    if reference is None:
        raise ValueError("motion_detect requires a reference (previous frame)")

    sensitivity = float(tool_input.params.get("sensitivity", 1.0))
    override = tool_input.params.get("grid_size")
    if override is not None:
        override = int(override)

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    grid_size = compute_grid_size(img_crop, ref_crop, override=override)
    img_grid = resample_to_grid(img_crop, grid_size).astype(np.float64)
    ref_grid = resample_to_grid(ref_crop, grid_size).astype(np.float64)

    # Ensure same shape
    min_h = min(img_grid.shape[0], ref_grid.shape[0])
    min_w = min(img_grid.shape[1], ref_grid.shape[1])
    img_grid = img_grid[:min_h, :min_w]
    ref_grid = ref_grid[:min_h, :min_w]

    rmsd = float(np.sqrt(np.mean((img_grid - ref_grid) ** 2)))
    change_ratio = rmsd / (255.0 * sensitivity)
    score = max(0.0, min(1.0, 1.0 - change_ratio))

    return ToolResult(
        tool="motion_detect",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "rmsd": rmsd,
            "change_ratio": change_ratio,
            "grid_shape": [min_h, min_w],
        },
    )
