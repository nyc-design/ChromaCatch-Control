"""Dominant colors tool - K-means dominant color extraction in CIELAB."""

from __future__ import annotations

import cv2
import numpy as np
from sklearn.cluster import KMeans

from cv_toolkit._utils import bgr_to_lab, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("dominant_colors")
def dominant_colors(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Extract top-N dominant colors via K-means in CIELAB space.

    If reference provided, compares dominant color sets and returns
    a similarity score based on how well they match.

    Params:
        k (int): Number of clusters. Default 5.
        max_pixels (int): Max pixels to sample for speed. Default 10000.
    """
    k = int(tool_input.params.get("k", 5))
    max_pixels = int(tool_input.params.get("max_pixels", 10000))

    img_crop = extract_region(image, tool_input.region)
    img_colors, img_proportions = _extract_dominant(img_crop, k, max_pixels)

    details: dict = {
        "colors_lab": img_colors.tolist(),
        "colors_bgr": _lab_to_bgr_list(img_colors),
        "proportions": img_proportions.tolist(),
    }

    if reference is not None:
        ref_crop = extract_region(reference, tool_input.region)
        ref_colors, ref_proportions = _extract_dominant(ref_crop, k, max_pixels)
        details["ref_colors_lab"] = ref_colors.tolist()
        details["ref_proportions"] = ref_proportions.tolist()

        # Match each image color to nearest ref color, weighted by proportion
        score = _compare_color_sets(
            img_colors, img_proportions, ref_colors, ref_proportions
        )
    else:
        score = 1.0  # Extraction succeeded, data in details

    return ToolResult(
        tool="dominant_colors",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )


def _extract_dominant(
    image: np.ndarray, k: int, max_pixels: int
) -> tuple[np.ndarray, np.ndarray]:
    """Extract dominant colors and proportions from an image."""
    lab = bgr_to_lab(image)
    pixels = lab.reshape(-1, 3)

    if len(pixels) > max_pixels:
        rng = np.random.RandomState(42)
        indices = rng.choice(len(pixels), max_pixels, replace=False)
        pixels = pixels[indices]

    actual_k = min(k, len(pixels))
    kmeans = KMeans(n_clusters=actual_k, n_init=10, random_state=42)
    labels = kmeans.fit_predict(pixels)

    centers = kmeans.cluster_centers_
    counts = np.bincount(labels, minlength=actual_k).astype(np.float64)
    proportions = counts / counts.sum()

    # Sort by proportion descending
    order = np.argsort(-proportions)
    return centers[order], proportions[order]


def _compare_color_sets(
    colors_a: np.ndarray,
    props_a: np.ndarray,
    colors_b: np.ndarray,
    props_b: np.ndarray,
    max_delta_e: float = 100.0,
) -> float:
    """Compare two sets of dominant colors, weighted by proportion."""
    total_score = 0.0
    for i, color_a in enumerate(colors_a):
        diffs = colors_a[i].astype(np.float64) - colors_b.astype(np.float64)
        distances = np.sqrt(np.sum(diffs**2, axis=1))
        best_de = float(np.min(distances))
        similarity = max(0.0, 1.0 - best_de / max_delta_e)
        total_score += similarity * props_a[i]
    return float(total_score)


def _lab_to_bgr_list(lab_colors: np.ndarray) -> list[list[int]]:
    """Convert LAB color array to BGR int list for readability."""
    lab_img = lab_colors.reshape(1, -1, 3).astype(np.uint8)
    bgr_img = cv2.cvtColor(lab_img, cv2.COLOR_LAB2BGR)
    return bgr_img.reshape(-1, 3).tolist()
