"""Edge density tool - Canny edge pixel ratio."""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("edge_density")
def edge_density(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Measure ratio of edge pixels to total pixels in a region.

    Useful for distinguishing busy/textured screens from flat/loading screens.

    Params:
        canny_low (int): Canny lower threshold. Default 50.
        canny_high (int): Canny upper threshold. Default 150.
        blur_ksize (int): Gaussian blur kernel size. Default 5.
    """
    canny_low = int(tool_input.params.get("canny_low", 50))
    canny_high = int(tool_input.params.get("canny_high", 150))
    blur_ksize = int(tool_input.params.get("blur_ksize", 5))

    img_crop = extract_region(image, tool_input.region)
    gray = cv2.cvtColor(img_crop, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    edges = cv2.Canny(blurred, canny_low, canny_high)

    total = edges.size
    edge_pixels = int(np.count_nonzero(edges))
    density = edge_pixels / total if total > 0 else 0.0

    details: dict = {
        "edge_density": density,
        "edge_pixels": edge_pixels,
        "total_pixels": total,
    }

    if reference is not None:
        ref_crop = extract_region(reference, tool_input.region)
        ref_gray = cv2.cvtColor(ref_crop, cv2.COLOR_BGR2GRAY)
        ref_blurred = cv2.GaussianBlur(ref_gray, (blur_ksize, blur_ksize), 0)
        ref_edges = cv2.Canny(ref_blurred, canny_low, canny_high)
        ref_density = int(np.count_nonzero(ref_edges)) / ref_edges.size
        diff = abs(density - ref_density)
        score = max(0.0, 1.0 - diff * 5.0)
        details["ref_edge_density"] = ref_density
        details["diff"] = diff
    else:
        score = density

    return ToolResult(
        tool="edge_density",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )
