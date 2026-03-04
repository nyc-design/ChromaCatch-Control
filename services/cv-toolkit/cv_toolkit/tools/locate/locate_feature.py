"""Locate feature tool - ORB/AKAZE keypoint matching with homography.

Extracts keypoints and descriptors from both images, applies BFMatcher
with Lowe's ratio test, uses findHomography (or affine fallback) to
localize the reference in the image.  Small references are upscaled
before keypoint detection for better feature extraction.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import extract_region, normalize_bbox
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool

_MIN_REF_DIM = 200  # Upscale references smaller than this


@register_tool("locate_feature")
def locate_feature(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate a reference object via keypoint feature matching.

    Extracts keypoints/descriptors from both images, matches them via
    BFMatcher with Lowe's ratio test, then uses findHomography (with
    affine fallback) to localize the reference.

    Requires reference image.

    Params (optional):
        detector (str): Feature detector type ("orb" or "akaze"). Default "orb".
        max_features (int): Maximum features to detect (ORB only). Default 1000.
        min_matches (int): Minimum good matches for a valid detection. Default 8.
        ratio_threshold (float): Lowe's ratio test threshold. Default 0.75.
    """
    if reference is None:
        raise ValueError("locate_feature requires a reference image")

    detector_type = str(tool_input.params.get("detector", "orb")).lower()
    max_features = int(tool_input.params.get("max_features", 1000))
    min_matches = int(tool_input.params.get("min_matches", 8))
    ratio_threshold = float(tool_input.params.get("ratio_threshold", 0.75))

    img_crop = extract_region(image, tool_input.region)
    # Use full reference — region specifies where to search in the image.
    ref_crop = reference.copy()
    img_h, img_w = img_crop.shape[:2]

    # Upscale small references for better keypoint detection
    ref_max_dim = max(ref_crop.shape[:2])
    upscale = 1
    if ref_max_dim < _MIN_REF_DIM:
        upscale = int(np.ceil(_MIN_REF_DIM / ref_max_dim))
        ref_crop = cv2.resize(
            ref_crop,
            (ref_crop.shape[1] * upscale, ref_crop.shape[0] * upscale),
            interpolation=cv2.INTER_CUBIC,
        )

    # Create detector
    if detector_type == "akaze":
        detector = cv2.AKAZE_create()
        norm_type = cv2.NORM_HAMMING
    else:
        detector = cv2.ORB_create(nfeatures=max_features)
        norm_type = cv2.NORM_HAMMING

    # Convert to grayscale (required for ORB/AKAZE descriptors)
    img_gray = cv2.cvtColor(img_crop, cv2.COLOR_BGR2GRAY)
    ref_gray = cv2.cvtColor(ref_crop, cv2.COLOR_BGR2GRAY)

    # Detect and compute
    kp_img, desc_img = detector.detectAndCompute(img_gray, None)
    kp_ref, desc_ref = detector.detectAndCompute(ref_gray, None)

    if desc_img is None or desc_ref is None or len(kp_img) < 2 or len(kp_ref) < 2:
        return ToolResult(
            tool="locate_feature",
            score=0.0,
            match=False,
            threshold=tool_input.threshold,
            details={"matches": [], "num_matches": 0, "good_matches": 0},
        )

    # BFMatcher with knnMatch
    bf = cv2.BFMatcher(norm_type, crossCheck=False)
    try:
        knn_matches = bf.knnMatch(desc_ref, desc_img, k=2)
    except cv2.error:
        return ToolResult(
            tool="locate_feature",
            score=0.0,
            match=False,
            threshold=tool_input.threshold,
            details={"matches": [], "num_matches": 0, "good_matches": 0},
        )

    # Lowe's ratio test
    good_matches = []
    for pair in knn_matches:
        if len(pair) == 2:
            m, n = pair
            if m.distance < ratio_threshold * n.distance:
                good_matches.append(m)

    matches_out: list[dict] = []
    if len(good_matches) >= 4:
        src_pts = np.float32(
            [kp_ref[m.queryIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)
        dst_pts = np.float32(
            [kp_img[m.trainIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)

        # Scale source points back if reference was upscaled
        if upscale > 1:
            src_pts = src_pts / upscale

        ref_h_orig, ref_w_orig = reference.shape[:2]
        corners = np.float32([
            [0, 0], [ref_w_orig, 0], [ref_w_orig, ref_h_orig], [0, ref_h_orig]
        ]).reshape(-1, 1, 2)

        bbox = _try_homography(src_pts, dst_pts, corners, good_matches, img_h, img_w)

        # Affine fallback if homography fails or produces degenerate bbox
        if bbox is None:
            bbox = _try_affine(src_pts, dst_pts, corners, good_matches, img_h, img_w)

        if bbox is not None:
            matches_out.append(bbox)

    # Score is 0.0 if no location found — don't report misleading confidence
    best_score = matches_out[0]["confidence"] if matches_out else 0.0

    return ToolResult(
        tool="locate_feature",
        score=best_score,
        match=len(matches_out) > 0 and best_score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches_out,
            "num_matches": len(matches_out),
            "good_matches": len(good_matches),
        },
    )


def _try_homography(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
    corners: np.ndarray,
    good_matches: list,
    img_h: int,
    img_w: int,
) -> dict | None:
    """Try perspective homography, return bbox dict or None."""
    M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if M is None:
        return None

    inlier_count = int(mask.sum()) if mask is not None else len(good_matches)
    confidence = min(1.0, inlier_count / max(1, len(good_matches)))

    transformed = cv2.perspectiveTransform(corners, M)
    return _bbox_from_corners(transformed, confidence, img_h, img_w)


def _try_affine(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
    corners: np.ndarray,
    good_matches: list,
    img_h: int,
    img_w: int,
) -> dict | None:
    """Try affine (similarity) transform as a fallback."""
    M, mask = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.RANSAC)
    if M is None:
        return None

    inlier_count = int(mask.sum()) if mask is not None else len(good_matches)
    confidence = min(1.0, inlier_count / max(1, len(good_matches)))

    # Affine transform: add [0,0,1] row, use perspectiveTransform
    M_full = np.vstack([M, [0, 0, 1]])
    transformed = cv2.perspectiveTransform(corners, M_full)
    return _bbox_from_corners(transformed, confidence, img_h, img_w)


def _bbox_from_corners(
    transformed: np.ndarray, confidence: float, img_h: int, img_w: int
) -> dict | None:
    """Extract axis-aligned bbox from transformed corners."""
    pts = transformed.reshape(-1, 2)
    x_min = max(0, int(pts[:, 0].min()))
    y_min = max(0, int(pts[:, 1].min()))
    x_max = min(img_w, int(pts[:, 0].max()))
    y_max = min(img_h, int(pts[:, 1].max()))
    w = x_max - x_min
    h = y_max - y_min
    if w <= 0 or h <= 0:
        return None
    bbox = normalize_bbox(x_min, y_min, w, h, img_h, img_w)
    return {"bbox": bbox, "confidence": confidence}
