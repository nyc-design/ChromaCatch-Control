"""Tests for CV toolkit utility functions."""

import numpy as np
import pytest

import cv2

from cv_toolkit._utils import (
    bgr_to_hsv,
    bgr_to_lab,
    brightness_scale,
    compute_grid_size,
    compute_rmsd,
    delta_e_cie76,
    delta_e_to_score,
    extract_region,
    non_max_suppression,
    normalize_bbox,
    resample_to_grid,
    stddev_weight,
    waterfill_candidates,
)
from cv_toolkit.models import Region


class TestExtractRegion:
    def test_none_returns_full_copy(self, solid_red):
        result = extract_region(solid_red, None)
        assert result.shape == solid_red.shape
        np.testing.assert_array_equal(result, solid_red)
        # Verify it's a copy
        result[0, 0] = (0, 0, 0)
        assert not np.array_equal(result, solid_red)

    def test_center_quarter(self, solid_red):
        region = Region(x=0.25, y=0.25, w=0.5, h=0.5)
        result = extract_region(solid_red, region)
        assert result.shape == (50, 50, 3)

    def test_top_left_corner(self, gradient):
        region = Region(x=0.0, y=0.0, w=0.5, h=0.5)
        result = extract_region(gradient, region)
        assert result.shape == (50, 50, 3)

    def test_boundary_clamping(self, solid_red):
        region = Region(x=0.9, y=0.9, w=0.1, h=0.1)
        result = extract_region(solid_red, region)
        assert result.shape[0] > 0
        assert result.shape[1] > 0


class TestComputeGridSize:
    def test_uses_higher_res(self):
        small = np.zeros((50, 50, 3), dtype=np.uint8)
        large = np.zeros((1000, 800, 3), dtype=np.uint8)
        size = compute_grid_size(small, large)
        assert size == 1000

    def test_minimum_floor(self):
        small = np.zeros((50, 50, 3), dtype=np.uint8)
        size = compute_grid_size(small, None)
        assert size == 500

    def test_override(self):
        img = np.zeros((1000, 1000, 3), dtype=np.uint8)
        size = compute_grid_size(img, None, override=300)
        assert size == 300

    def test_no_reference(self):
        img = np.zeros((800, 600, 3), dtype=np.uint8)
        size = compute_grid_size(img, None)
        assert size == 800

    def test_custom_default_min(self):
        small = np.zeros((50, 50, 3), dtype=np.uint8)
        size = compute_grid_size(small, None, default_min=200)
        assert size == 200


class TestResampleToGrid:
    def test_upscale(self):
        small = np.zeros((50, 50, 3), dtype=np.uint8)
        result = resample_to_grid(small, 200)
        assert max(result.shape[:2]) == 200

    def test_downscale(self):
        large = np.zeros((1000, 800, 3), dtype=np.uint8)
        result = resample_to_grid(large, 500)
        assert max(result.shape[:2]) == 500

    def test_preserves_aspect_ratio(self):
        img = np.zeros((100, 200, 3), dtype=np.uint8)
        result = resample_to_grid(img, 400)
        # Longest axis (200) maps to 400, so shorter (100) maps to 200
        assert result.shape == (200, 400, 3)

    def test_no_change_when_same_size(self):
        img = np.zeros((500, 300, 3), dtype=np.uint8)
        result = resample_to_grid(img, 500)
        assert max(result.shape[:2]) == 500


class TestBgrToLab:
    def test_shape_preserved(self, solid_red):
        lab = bgr_to_lab(solid_red)
        assert lab.shape == solid_red.shape
        assert lab.dtype == np.float32

    def test_white_has_high_l(self, solid_white):
        lab = bgr_to_lab(solid_white)
        # In OpenCV LAB, L ranges 0-255 (scaled), not 0-100
        assert lab[0, 0, 0] > 200


class TestBgrToHsv:
    def test_shape_preserved(self, solid_red):
        hsv = bgr_to_hsv(solid_red)
        assert hsv.shape == solid_red.shape

    def test_red_hue(self, solid_red):
        hsv = bgr_to_hsv(solid_red)
        # Pure red in OpenCV HSV: H=0 (or 179)
        assert hsv[0, 0, 0] in (0, 179)


class TestDeltaECie76:
    def test_identical_images(self, solid_red):
        lab = bgr_to_lab(solid_red)
        result = delta_e_cie76(lab, lab)
        assert np.allclose(result, 0.0)

    def test_different_colors(self, solid_red, solid_blue):
        lab1 = bgr_to_lab(solid_red)
        lab2 = bgr_to_lab(solid_blue)
        result = delta_e_cie76(lab1, lab2)
        assert np.all(result > 0)


class TestDeltaEToScore:
    def test_zero_distance(self):
        assert delta_e_to_score(0.0) == 1.0

    def test_max_distance(self):
        assert delta_e_to_score(100.0) == 0.0

    def test_over_max(self):
        assert delta_e_to_score(200.0) == 0.0

    def test_midpoint(self):
        assert delta_e_to_score(50.0) == pytest.approx(0.5)

    def test_custom_max(self):
        assert delta_e_to_score(50.0, max_delta_e=200.0) == pytest.approx(0.75)


class TestNormalizeBbox:
    def test_basic(self):
        result = normalize_bbox(50, 100, 200, 150, img_h=500, img_w=1000)
        assert result == pytest.approx({"x": 0.05, "y": 0.2, "w": 0.2, "h": 0.3})

    def test_full_image(self):
        result = normalize_bbox(0, 0, 100, 100, img_h=100, img_w=100)
        assert result == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

    def test_clamps_to_bounds(self):
        result = normalize_bbox(0, 0, 200, 200, img_h=100, img_w=100)
        assert result["w"] == 1.0
        assert result["h"] == 1.0


class TestBrightnessScale:
    def test_identical_images_unchanged(self, solid_red):
        result = brightness_scale(solid_red, solid_red)
        np.testing.assert_array_equal(result, solid_red)

    def test_darker_image_brightened(self):
        dark = np.full((50, 50, 3), 100, dtype=np.uint8)
        bright = np.full((50, 50, 3), 200, dtype=np.uint8)
        result = brightness_scale(dark, bright)
        # Should be brighter than input (clamped at +15%)
        assert result.mean() > dark.mean()

    def test_clamp_limits(self):
        dark = np.full((50, 50, 3), 50, dtype=np.uint8)
        bright = np.full((50, 50, 3), 250, dtype=np.uint8)
        result = brightness_scale(dark, bright, clamp=0.15)
        # Scale ratio would be 5.0 but clamped to 1.15
        expected = 50 * 1.15
        assert abs(result.mean() - expected) < 2.0


class TestComputeRmsd:
    def test_identical_is_zero(self, solid_red):
        assert compute_rmsd(solid_red, solid_red) == 0.0

    def test_different_is_positive(self, solid_red, solid_blue):
        rmsd = compute_rmsd(solid_red, solid_blue)
        assert rmsd > 0.0

    def test_with_alpha_mask(self):
        img1 = np.zeros((10, 10, 3), dtype=np.uint8)
        img2 = np.full((10, 10, 3), 100, dtype=np.uint8)
        # Mask only the top row
        alpha = np.zeros((10, 10), dtype=np.uint8)
        alpha[0, :] = 255
        rmsd = compute_rmsd(img1, img2, alpha_mask=alpha)
        # Each masked pixel differs by 100 on 3 channels: sqrt(100^2 * 3) ≈ 173.2
        assert rmsd == pytest.approx(100.0 * np.sqrt(3), abs=0.5)

    def test_empty_alpha_returns_zero(self):
        img1 = np.zeros((10, 10, 3), dtype=np.uint8)
        img2 = np.full((10, 10, 3), 255, dtype=np.uint8)
        alpha = np.zeros((10, 10), dtype=np.uint8)
        assert compute_rmsd(img1, img2, alpha_mask=alpha) == 0.0


class TestStddevWeight:
    def test_uniform_image_high_weight(self):
        uniform = np.full((50, 50, 3), 128, dtype=np.uint8)
        w = stddev_weight(uniform)
        # Uniform → low stddev → high weight (close to 1.0)
        assert w == pytest.approx(1.0, abs=0.01)

    def test_complex_image_lower_weight(self):
        # Create image with high variance
        complex_img = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        w = stddev_weight(complex_img)
        assert w < 1.0

    def test_always_positive(self, solid_red):
        assert stddev_weight(solid_red) > 0.0


class TestWaterfillCandidates:
    def test_finds_red_blob(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)  # BGR red
        candidates = waterfill_candidates(
            img,
            color_filters=[(np.array([0, 200, 200]), np.array([10, 255, 255]))],
        )
        assert len(candidates) == 1
        assert candidates[0]["area_ratio"] > 0.2
        bbox = candidates[0]["bbox_norm"]
        assert 0.2 < bbox["x"] < 0.3
        assert 0.2 < bbox["y"] < 0.3

    def test_multi_filter_deduplicates(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        # Two overlapping red filters should deduplicate to 1 result
        candidates = waterfill_candidates(
            img,
            color_filters=[
                (np.array([0, 200, 200]), np.array([10, 255, 255])),
                (np.array([0, 150, 150]), np.array([15, 255, 255])),
            ],
        )
        assert len(candidates) == 1

    def test_area_filtering(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (90, 90), (92, 92), (0, 0, 255), -1)  # Tiny red
        candidates = waterfill_candidates(
            img,
            color_filters=[(np.array([0, 200, 200]), np.array([10, 255, 255]))],
            min_area_ratio=0.01,
        )
        assert len(candidates) == 0

    def test_no_matches(self, solid_blue):
        candidates = waterfill_candidates(
            solid_blue,
            color_filters=[(np.array([0, 200, 200]), np.array([10, 255, 255]))],
        )
        assert len(candidates) == 0


class TestNonMaxSuppression:
    def test_no_overlap(self):
        matches = [
            {"bbox": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}, "confidence": 0.9},
            {"bbox": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}, "confidence": 0.8},
        ]
        result = non_max_suppression(matches, overlap_thresh=0.5)
        assert len(result) == 2

    def test_full_overlap_keeps_best(self):
        matches = [
            {"bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, "confidence": 0.9},
            {"bbox": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}, "confidence": 0.5},
        ]
        result = non_max_suppression(matches, overlap_thresh=0.5)
        assert len(result) == 1
        assert result[0]["confidence"] == 0.9

    def test_empty_input(self):
        assert non_max_suppression([], overlap_thresh=0.5) == []

    def test_partial_overlap_below_threshold(self):
        matches = [
            {"bbox": {"x": 0.0, "y": 0.0, "w": 0.3, "h": 0.3}, "confidence": 0.9},
            {"bbox": {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3}, "confidence": 0.8},
        ]
        result = non_max_suppression(matches, overlap_thresh=0.5)
        # IoU is small here, both should be kept
        assert len(result) == 2
