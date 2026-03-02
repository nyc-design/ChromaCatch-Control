"""Grid structure tool - lighting-invariant relative color structure comparison."""

from __future__ import annotations

import numpy as np
from scipy.spatial.distance import cdist

from cv_toolkit._utils import bgr_to_lab, compute_grid_size, extract_region, resample_to_grid
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("grid_structure")
def grid_structure(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Lighting-invariant relative color structure comparison.

    Instead of comparing absolute colors, computes the pairwise color distance
    matrix between subsampled faux-pixels in each image, then compares those
    matrices via Pearson correlation. Two images of the same scene under
    different lighting will have different absolute colors but similar
    relative color relationships.

    Params:
        grid_size (int): Override faux-pixel grid size.
        subsample_step (int): Take every Nth pixel for pairwise distances. Default 10.
        max_sample_points (int): Cap on sample points. Default 1500.
    """
    if reference is None:
        raise ValueError("grid_structure requires a reference image")

    override = tool_input.params.get("grid_size")
    if override is not None:
        override = int(override)
    subsample_step = int(tool_input.params.get("subsample_step", 10))
    max_sample_points = int(tool_input.params.get("max_sample_points", 1500))

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)

    grid_size = compute_grid_size(img_crop, ref_crop, override=override)
    img_grid = resample_to_grid(img_crop, grid_size)
    ref_grid = resample_to_grid(ref_crop, grid_size)

    # Ensure same shape
    min_h = min(img_grid.shape[0], ref_grid.shape[0])
    min_w = min(img_grid.shape[1], ref_grid.shape[1])
    img_grid = img_grid[:min_h, :min_w]
    ref_grid = ref_grid[:min_h, :min_w]

    # Convert to LAB and flatten to pixel array
    img_lab = bgr_to_lab(img_grid).reshape(-1, 3)
    ref_lab = bgr_to_lab(ref_grid).reshape(-1, 3)

    # Subsample for tractable pairwise distance computation
    indices = np.arange(0, len(img_lab), subsample_step)
    if len(indices) > max_sample_points:
        indices = indices[:max_sample_points]
    if len(indices) < 3:
        # Not enough points for meaningful comparison
        return ToolResult(
            tool="grid_structure",
            score=0.0,
            match=False,
            threshold=tool_input.threshold,
            details={"error": "Too few sample points", "sample_points": len(indices)},
        )

    img_samples = img_lab[indices]
    ref_samples = ref_lab[indices]

    # Compute pairwise distance matrices
    img_dists = cdist(img_samples, img_samples, metric="euclidean")
    ref_dists = cdist(ref_samples, ref_samples, metric="euclidean")

    # Flatten upper triangle for correlation (avoid diagonal zeros)
    triu_idx = np.triu_indices(len(indices), k=1)
    img_flat = img_dists[triu_idx]
    ref_flat = ref_dists[triu_idx]

    # Pearson correlation
    if np.std(img_flat) < 1e-10 or np.std(ref_flat) < 1e-10:
        # One or both images are uniform color — can't correlate
        correlation = 1.0 if np.std(img_flat) < 1e-10 and np.std(ref_flat) < 1e-10 else 0.0
    else:
        correlation = float(np.corrcoef(img_flat, ref_flat)[0, 1])

    # Map correlation [-1, 1] to score [0, 1]
    score = (correlation + 1.0) / 2.0

    return ToolResult(
        tool="grid_structure",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "correlation": correlation,
            "sample_points": len(indices),
            "grid_shape": [min_h, min_w],
        },
    )
