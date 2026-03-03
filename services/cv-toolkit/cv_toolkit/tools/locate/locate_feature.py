"""Locate feature tool - ORB/AKAZE keypoint matching with homography.

Extracts keypoints and descriptors from both images, applies BFMatcher
with Lowe's ratio test, and uses findHomography to localize the reference
in the image.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import extract_region, normalize_bbox
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("locate_feature")
def locate_feature(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate a reference object via keypoint feature matching.

    Extracts keypoints/descriptors from both images, matches them via
    BFMatcher with Lowe's ratio test, then uses findHomography to
    compute the perspective transform and localize the reference.

    Requires reference image.

    Params (optional):
        detector (str): Feature detector type ("orb" or "akaze"). Default "orb".
        max_features (int): Maximum features to detect (ORB only). Default 500.
        min_matches (int): Minimum good matches for a valid detection. Default 10.
        ratio_threshold (float): Lowe's ratio test threshold. Default 0.75.
    """
    if reference is None:
        raise ValueError("locate_feature requires a reference image")

    detector_type = str(tool_input.params.get("detector", "orb")).lower()
    max_features = int(tool_input.params.get("max_features", 500))
    min_matches = int(tool_input.params.get("min_matches", 10))
    ratio_threshold = float(tool_input.params.get("ratio_threshold", 0.75))

    img_crop = extract_region(image, tool_input.region)
    ref_crop = extract_region(reference, tool_input.region)
    img_h, img_w = img_crop.shape[:2]

    # Create detector
    if detector_type == "akaze":
        detector = cv2.AKAZE_create()
        norm_type = cv2.NORM_HAMMING
    else:
        detector = cv2.ORB_create(nfeatures=max_features)
        norm_type = cv2.NORM_HAMMING

    # Convert to grayscale
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

    confidence = min(1.0, len(good_matches) / min_matches)

    matches_out: list[dict] = []
    if len(good_matches) >= 4:
        # Compute homography
        src_pts = np.float32(
            [kp_ref[m.queryIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)
        dst_pts = np.float32(
            [kp_img[m.trainIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)

        M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)

        if M is not None:
            ref_h, ref_w = ref_crop.shape[:2]
            corners = np.float32([
                [0, 0], [ref_w, 0], [ref_w, ref_h], [0, ref_h]
            ]).reshape(-1, 1, 2)

            transformed = cv2.perspectiveTransform(corners, M)
            pts = transformed.reshape(-1, 2)

            x_min = max(0, int(pts[:, 0].min()))
            y_min = max(0, int(pts[:, 1].min()))
            x_max = min(img_w, int(pts[:, 0].max()))
            y_max = min(img_h, int(pts[:, 1].max()))

            w = x_max - x_min
            h = y_max - y_min

            if w > 0 and h > 0:
                bbox = normalize_bbox(x_min, y_min, w, h, img_h, img_w)
                matches_out.append({"bbox": bbox, "confidence": confidence})

    best_score = matches_out[0]["confidence"] if matches_out else confidence

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
