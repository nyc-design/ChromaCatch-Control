"""Internal utilities shared by all CV tools."""

from __future__ import annotations

import cv2
import numpy as np
from sklearn.cluster import KMeans

from cv_toolkit.models import Region


def extract_region(image: np.ndarray, region: Region | None) -> np.ndarray:
    """Crop image to a normalized Region. Returns a copy.

    If region is None, returns the full image (copy to avoid mutation).
    """
    if region is None:
        return image.copy()
    h, w = image.shape[:2]
    x1 = int(region.x * w)
    y1 = int(region.y * h)
    x2 = int((region.x + region.w) * w)
    y2 = int((region.y + region.h) * h)
    x1, x2 = max(0, x1), min(w, x2)
    y1, y2 = max(0, y1), min(h, y2)
    return image[y1:y2, x1:x2].copy()


def compute_grid_size(
    image: np.ndarray,
    reference: np.ndarray | None,
    default_min: int = 500,
    override: int | None = None,
) -> int:
    """Determine the faux-pixel grid size (longest axis pixel count).

    Uses the HIGHER resolution of the two images as baseline.
    Always scales UP, never down — the grid is at least as large
    as the larger image's longest axis, with a minimum floor.
    """
    if override is not None:
        return max(1, override)
    max_dim = max(image.shape[:2])
    if reference is not None:
        max_dim = max(max_dim, max(reference.shape[:2]))
    return max(max_dim, default_min)


def resample_to_grid(image: np.ndarray, grid_size: int) -> np.ndarray:
    """Resample an image to a faux-pixel grid.

    The longest axis is mapped to grid_size pixels; the shorter axis
    is scaled proportionally. Uses INTER_CUBIC for upscaling,
    INTER_AREA for downscaling.
    """
    h, w = image.shape[:2]
    if max(h, w) == 0:
        return image
    scale = grid_size / max(h, w)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_AREA
    return cv2.resize(image, (new_w, new_h), interpolation=interp)


def bgr_to_lab(image: np.ndarray) -> np.ndarray:
    """Convert BGR image to CIELAB color space (float32)."""
    return cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)


def bgr_to_hsv(image: np.ndarray) -> np.ndarray:
    """Convert BGR to HSV."""
    return cv2.cvtColor(image, cv2.COLOR_BGR2HSV)


def delta_e_cie76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Per-pixel CIE76 Delta-E between two LAB images of the same shape."""
    diff = lab1.astype(np.float64) - lab2.astype(np.float64)
    return np.sqrt(np.sum(diff**2, axis=-1))


def delta_e_to_score(delta_e: float, max_delta_e: float = 100.0) -> float:
    """Convert a Delta-E distance to a 0.0-1.0 similarity score.

    0 Delta-E => 1.0 (perfect match)
    max_delta_e or higher => 0.0
    """
    return max(0.0, 1.0 - delta_e / max_delta_e)


# ---------------------------------------------------------------------------
# Locate / matching utilities
# ---------------------------------------------------------------------------


def normalize_bbox(
    x: int, y: int, w: int, h: int, img_h: int, img_w: int
) -> dict:
    """Convert pixel bounding box to normalized Region dict (0.0-1.0)."""
    return {
        "x": max(0.0, min(1.0, x / img_w)),
        "y": max(0.0, min(1.0, y / img_h)),
        "w": max(0.0, min(1.0, w / img_w)),
        "h": max(0.0, min(1.0, h / img_h)),
    }


def brightness_scale(
    image: np.ndarray, reference: np.ndarray, clamp: float = 0.15
) -> np.ndarray:
    """Scale image brightness to match reference, clamped ±clamp.

    PA technique: compute per-channel mean ratio, clamp to avoid
    extreme corrections, apply multiplicatively.
    """
    img_f = image.astype(np.float64)
    ref_f = reference.astype(np.float64)
    img_mean = img_f.mean(axis=(0, 1))
    ref_mean = ref_f.mean(axis=(0, 1))
    # Avoid division by zero
    img_mean = np.where(img_mean < 1.0, 1.0, img_mean)
    scale = ref_mean / img_mean
    scale = np.clip(scale, 1.0 - clamp, 1.0 + clamp)
    result = img_f * scale
    return np.clip(result, 0, 255).astype(np.uint8)


def compute_rmsd(
    img1: np.ndarray, img2: np.ndarray, alpha_mask: np.ndarray | None = None
) -> float:
    """Per-pixel RMSD with optional alpha masking.

    If alpha_mask provided, only pixels where alpha > 0 are included.
    Both images must have the same shape (H, W, C).
    Returns RMSD value (0.0 = identical).
    """
    f1 = img1.astype(np.float64)
    f2 = img2.astype(np.float64)
    diff_sq = (f1 - f2) ** 2

    if alpha_mask is not None:
        mask = alpha_mask > 0
        if mask.ndim == 2 and diff_sq.ndim == 3:
            mask = np.expand_dims(mask, axis=-1)
        n = mask.sum()
        if n == 0:
            return 0.0
        return float(np.sqrt((diff_sq * mask).sum() / n))

    return float(np.sqrt(diff_sq.mean()))


def stddev_weight(image: np.ndarray) -> float:
    """Stddev-based RMSD normalization factor (PA technique).

    Complex templates (high stddev) naturally produce higher RMSD.
    Returns a multiplier: 1.0 / (sum_of_channel_stddevs * coeff + offset).
    Use: normalized_rmsd = raw_rmsd * stddev_weight(reference).
    """
    stddevs = image.astype(np.float64).std(axis=(0, 1))
    total_std = float(stddevs.sum())
    coefficient = 0.005
    offset = 1.0
    return 1.0 / (total_std * coefficient + offset)


def waterfill_candidates(
    image: np.ndarray,
    color_filters: list[tuple[np.ndarray, np.ndarray]],
    min_area_ratio: float = 0.0005,
    max_area_ratio: float = 0.5,
    min_aspect: float = 0.1,
    max_aspect: float = 10.0,
    morph_size: int = 3,
) -> list[dict]:
    """Multi-filter waterfill pipeline for locating colored blobs.

    For each color filter: HSV range → binary mask → morphological cleanup →
    connected components → filter by geometry. Results from all filters are
    merged and deduplicated by IoU.

    Returns list of dicts sorted by area (descending):
        bbox_px: [x, y, w, h] in pixel coordinates
        bbox_norm: {"x", "y", "w", "h"} normalized (0.0-1.0)
        contour: np.ndarray (contour points)
        area_ratio: float
        aspect_ratio: float
    """
    hsv = bgr_to_hsv(image)
    img_h, img_w = image.shape[:2]
    total_area = img_h * img_w
    min_area = total_area * min_area_ratio
    max_area = total_area * max_area_ratio

    all_candidates: list[dict] = []

    for hsv_lower, hsv_upper in color_filters:
        lower = np.asarray(hsv_lower, dtype=np.uint8)
        upper = np.asarray(hsv_upper, dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)

        if morph_size > 0:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (morph_size, morph_size)
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

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
            all_candidates.append({
                "bbox_px": [x, y, w, h],
                "bbox_norm": normalize_bbox(x, y, w, h, img_h, img_w),
                "contour": contour,
                "area_ratio": area / total_area,
                "aspect_ratio": float(aspect),
            })

    # Deduplicate across filters via NMS
    if len(color_filters) > 1 and len(all_candidates) > 1:
        # Convert to format expected by non_max_suppression
        nms_input = [
            {"bbox": c["bbox_norm"], "confidence": c["area_ratio"], **c}
            for c in all_candidates
        ]
        deduped = non_max_suppression(nms_input, overlap_thresh=0.5)
        all_candidates = [
            {k: v for k, v in d.items() if k != "confidence"}
            for d in deduped
        ]

    all_candidates.sort(key=lambda o: o["area_ratio"], reverse=True)
    return all_candidates


def non_max_suppression(
    matches: list[dict], overlap_thresh: float = 0.5
) -> list[dict]:
    """Non-maximum suppression for deduplicating overlapping detections.

    Each match must have "bbox" dict with {"x", "y", "w", "h"} (normalized)
    and "confidence" float. Keeps the highest-confidence match when IoU
    exceeds overlap_thresh.
    """
    if not matches:
        return []

    # Sort by confidence descending
    sorted_matches = sorted(matches, key=lambda m: m["confidence"], reverse=True)
    keep: list[dict] = []

    for match in sorted_matches:
        b = match["bbox"]
        should_keep = True
        for kept in keep:
            kb = kept["bbox"]
            iou = _compute_iou(b, kb)
            if iou > overlap_thresh:
                should_keep = False
                break
        if should_keep:
            keep.append(match)

    return keep


def auto_color_ranges(
    image: np.ndarray,
    k: int = 3,
    h_margin: int = 15,
    s_margin: int = 50,
    v_margin: int = 50,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Auto-derive HSV color filter ranges from an image via K-means clustering.

    Clusters the image pixels in HSV space, then builds (lower, upper) ranges
    around each cluster center.  Useful for locate_template / locate_color to
    auto-derive color_filters from a reference image.

    Returns list of (hsv_lower, hsv_upper) np.uint8 array tuples.
    """
    hsv = bgr_to_hsv(image)
    pixels = hsv.reshape(-1, 3).astype(np.float32)

    # Cap k to number of distinct pixels
    unique_count = min(len(np.unique(pixels, axis=0)), k)
    if unique_count < 1:
        return []
    k = min(k, unique_count)

    kmeans = KMeans(n_clusters=k, n_init=10, random_state=0)
    kmeans.fit(pixels)
    centers = kmeans.cluster_centers_

    ranges: list[tuple[np.ndarray, np.ndarray]] = []
    for center in centers:
        h_c, s_c, v_c = center
        lower = np.array([
            max(0, int(h_c) - h_margin),
            max(0, int(s_c) - s_margin),
            max(0, int(v_c) - v_margin),
        ], dtype=np.uint8)
        upper = np.array([
            min(180, int(h_c) + h_margin),
            min(255, int(s_c) + s_margin),
            min(255, int(v_c) + v_margin),
        ], dtype=np.uint8)
        ranges.append((lower, upper))
    return ranges


def _compute_iou(a: dict, b: dict) -> float:
    """Compute IoU between two normalized bbox dicts."""
    ax1, ay1 = a["x"], a["y"]
    ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
    bx1, by1 = b["x"], b["y"]
    bx2, by2 = bx1 + b["w"], by1 + b["h"]

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    inter_w = max(0.0, ix2 - ix1)
    inter_h = max(0.0, iy2 - iy1)
    inter_area = inter_w * inter_h

    area_a = a["w"] * a["h"]
    area_b = b["w"] * b["h"]
    union_area = area_a + area_b - inter_area

    if union_area <= 0:
        return 0.0
    return inter_area / union_area
