"""Tests for object_detect tool (waterfill/connected component detection)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestObjectDetect:
    def test_finds_single_red_object(self):
        """Red rectangle on black background should be detected as one object."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)  # BGR red
        ti = ToolInput(
            tool="object_detect",
            threshold=0.5,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.details["num_objects"] == 1
        assert result.score >= 1.0

    def test_finds_multiple_objects(self):
        """Two separate red rectangles should be detected as two objects."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (50, 50), (0, 0, 255), -1)
        cv2.rectangle(img, (120, 120), (180, 180), (0, 0, 255), -1)
        ti = ToolInput(
            tool="object_detect",
            threshold=0.5,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.details["num_objects"] == 2

    def test_no_objects_in_wrong_color_range(self):
        """Red rectangle with blue color range should find nothing."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="object_detect",
            threshold=0.5,
            params={"hsv_lower": [100, 200, 200], "hsv_upper": [130, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.details["num_objects"] == 0
        assert result.score == 0.0

    def test_expected_count_exact(self):
        """Expected count = actual count should give high score."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (50, 50), (0, 0, 255), -1)
        cv2.rectangle(img, (120, 120), (180, 180), (0, 0, 255), -1)
        ti = ToolInput(
            tool="object_detect",
            threshold=0.9,
            params={
                "hsv_lower": [0, 200, 200],
                "hsv_upper": [10, 255, 255],
                "expected_count": 2,
            },
        )
        result = run_tool(img, ti)
        assert result.score > 0.9

    def test_expected_count_mismatch(self):
        """Expected count differs from actual should give low score."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="object_detect",
            threshold=0.9,
            params={
                "hsv_lower": [0, 200, 200],
                "hsv_upper": [10, 255, 255],
                "expected_count": 5,
            },
        )
        result = run_tool(img, ti)
        assert result.score < 0.5

    def test_with_reference_same_objects(self):
        """Same image as reference should give high score."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="object_detect",
            threshold=0.8,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti, reference=img)
        assert result.score > 0.8

    def test_with_reference_different_count(self):
        """Different object counts from reference should lower score."""
        img1 = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img1, (50, 50), (150, 150), (0, 0, 255), -1)

        img2 = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img2, (10, 10), (50, 50), (0, 0, 255), -1)
        cv2.rectangle(img2, (60, 60), (100, 100), (0, 0, 255), -1)
        cv2.rectangle(img2, (120, 120), (180, 180), (0, 0, 255), -1)

        ti = ToolInput(
            tool="object_detect",
            threshold=0.9,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img1, ti, reference=img2)
        assert result.score < 0.9

    def test_area_filtering(self):
        """Min area ratio should filter out tiny objects."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (90, 90), (92, 92), (0, 0, 255), -1)  # Tiny 2x2 red
        ti = ToolInput(
            tool="object_detect",
            threshold=0.5,
            params={
                "hsv_lower": [0, 200, 200],
                "hsv_upper": [10, 255, 255],
                "min_area_ratio": 0.01,  # 1% of image = 400px, way bigger than 4px
            },
        )
        result = run_tool(img, ti)
        assert result.details["num_objects"] == 0

    def test_missing_params_raises(self, solid_red):
        ti = ToolInput(tool="object_detect", threshold=0.5)
        with pytest.raises(ValueError, match="hsv_lower"):
            run_tool(solid_red, ti)

    def test_details_contain_bbox(self):
        """Object details should include bounding box, area, aspect ratio."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="object_detect",
            threshold=0.0,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        assert len(result.details["objects"]) == 1
        obj = result.details["objects"][0]
        assert "bbox" in obj
        assert "area" in obj
        assert "aspect_ratio" in obj
        assert len(obj["bbox"]) == 4
