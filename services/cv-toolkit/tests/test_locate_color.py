"""Tests for locate_color tool (HSV range blob detection)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestLocateColor:
    def test_finds_single_red_blob(self):
        """Red rectangle on black background should be located."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.match is True
        assert result.score == 1.0
        assert result.details["num_matches"] == 1
        bbox = result.details["matches"][0]["bbox"]
        assert bbox["x"] > 0.2
        assert bbox["y"] > 0.2

    def test_finds_multiple_blobs(self):
        """Two separated red rectangles should produce two matches."""
        img = np.zeros((200, 300, 3), dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (60, 60), (0, 0, 255), -1)
        cv2.rectangle(img, (200, 100), (280, 180), (0, 0, 255), -1)
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.details["num_matches"] == 2

    def test_no_match_wrong_color(self):
        """Red rectangle with blue HSV range should find nothing."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [100, 200, 200], "hsv_upper": [130, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.match is False
        assert result.score == 0.0
        assert result.details["num_matches"] == 0

    def test_missing_params_raises(self, solid_red):
        """Missing hsv_lower/hsv_upper should raise ValueError."""
        ti = ToolInput(tool="locate_color", threshold=0.5)
        with pytest.raises(ValueError, match="hsv_lower"):
            run_tool(solid_red, ti)

    def test_missing_hsv_upper_raises(self, solid_red):
        """Missing hsv_upper only should raise ValueError."""
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [0, 0, 0]},
        )
        with pytest.raises(ValueError, match="hsv_upper"):
            run_tool(solid_red, ti)

    def test_max_matches_limit(self):
        """max_matches should limit the number of returned matches."""
        img = np.zeros((300, 300, 3), dtype=np.uint8)
        # Create several distinct red blobs
        for y in range(0, 300, 50):
            for x in range(0, 300, 50):
                cv2.rectangle(img, (x + 5, y + 5), (x + 20, y + 20), (0, 0, 255), -1)
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={
                "hsv_lower": [0, 200, 200],
                "hsv_upper": [10, 255, 255],
                "max_matches": 3,
            },
        )
        result = run_tool(img, ti)
        assert result.details["num_matches"] <= 3

    def test_area_filtering(self):
        """Tiny blob should be filtered by min_area_ratio."""
        img = np.zeros((400, 400, 3), dtype=np.uint8)
        cv2.rectangle(img, (100, 100), (103, 103), (0, 0, 255), -1)  # 3x3 blob
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={
                "hsv_lower": [0, 200, 200],
                "hsv_upper": [10, 255, 255],
                "min_area_ratio": 0.01,
            },
        )
        result = run_tool(img, ti)
        assert result.details["num_matches"] == 0

    def test_confidence_always_one(self):
        """All color matches should have confidence 1.0 (binary match)."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        for m in result.details["matches"]:
            assert m["confidence"] == 1.0

    def test_bbox_normalized(self):
        """Bounding box coordinates should be normalized to 0.0-1.0."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(img, ti)
        bbox = result.details["matches"][0]["bbox"]
        for key in ["x", "y", "w", "h"]:
            assert 0.0 <= bbox[key] <= 1.0

    def test_green_blob_detection(self):
        """Green blob should be found with green HSV range."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.circle(img, (100, 100), 40, (0, 255, 0), -1)  # BGR green
        ti = ToolInput(
            tool="locate_color",
            threshold=0.5,
            params={"hsv_lower": [35, 200, 200], "hsv_upper": [85, 255, 255]},
        )
        result = run_tool(img, ti)
        assert result.match is True
        assert result.details["num_matches"] >= 1
