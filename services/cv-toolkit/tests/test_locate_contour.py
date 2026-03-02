"""Tests for locate_contour tool (Hu moment shape matching)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestLocateContour:
    def test_finds_matching_shape(self):
        """Circle in scene should match circle reference."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.circle(scene, (100, 100), 40, (255, 255, 255), -1)

        ref = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.circle(ref, (50, 50), 30, (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_contour",
            threshold=0.3,
            params={"min_area_ratio": 0.005},
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.match is True
        assert result.details["num_matches"] >= 1

    def test_finds_shape_with_color_filter(self):
        """Red circle with HSV filter should be located."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.circle(scene, (100, 100), 40, (0, 0, 255), -1)

        ref = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.circle(ref, (50, 50), 30, (0, 0, 255), -1)

        ti = ToolInput(
            tool="locate_contour",
            threshold=0.3,
            params={
                "hsv_lower": [0, 200, 200],
                "hsv_upper": [10, 255, 255],
                "min_area_ratio": 0.005,
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.match is True
        assert result.details["num_matches"] >= 1

    def test_no_contours_on_blank_image(self):
        """Blank image should find no contours."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        ref = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.circle(ref, (50, 50), 30, (255, 255, 255), -1)

        ti = ToolInput(tool="locate_contour", threshold=0.5)
        result = run_tool(scene, ti, reference=ref)
        assert result.match is False
        assert result.details["num_matches"] == 0

    def test_requires_reference(self, solid_red):
        """Missing reference should raise ValueError."""
        ti = ToolInput(tool="locate_contour", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_bbox_normalized(self):
        """Returned bboxes should have normalized coordinates."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(scene, (50, 50), (150, 150), (255, 255, 255), -1)

        ref = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.rectangle(ref, (10, 10), (90, 90), (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_contour",
            threshold=0.3,
            params={"min_area_ratio": 0.005},
        )
        result = run_tool(scene, ti, reference=ref)
        if result.details["matches"]:
            bbox = result.details["matches"][0]["bbox"]
            for key in ["x", "y", "w", "h"]:
                assert 0.0 <= bbox[key] <= 1.0

    def test_confidence_calculation(self):
        """Identical shapes should have very high confidence."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(scene, (50, 50), (150, 150), (255, 255, 255), -1)

        ref = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(ref, (50, 50), (150, 150), (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_contour",
            threshold=0.5,
            params={"min_area_ratio": 0.005},
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.score > 0.5

    def test_max_matches_limit(self):
        """max_matches should cap the number of results."""
        scene = np.zeros((300, 300, 3), dtype=np.uint8)
        for i in range(6):
            x = 10 + i * 45
            cv2.rectangle(scene, (x, 100), (x + 30, 160), (255, 255, 255), -1)

        ref = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.rectangle(ref, (10, 10), (70, 60), (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_contour",
            threshold=0.1,
            params={"max_matches": 2, "min_area_ratio": 0.001},
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.details["num_matches"] <= 2

    def test_min_hu_similarity_filter(self):
        """Very different shapes should be filtered by min_hu_similarity."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        # Draw a very thin line (very different Hu moments from a circle)
        cv2.line(scene, (10, 100), (190, 100), (255, 255, 255), 3)

        ref = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.circle(ref, (50, 50), 40, (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_contour",
            threshold=0.8,
            params={"min_hu_similarity": 0.8, "min_area_ratio": 0.001},
        )
        result = run_tool(scene, ti, reference=ref)
        # High min_hu_similarity should filter out dissimilar shapes
        assert result.match is False
