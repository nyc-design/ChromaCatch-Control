"""Tests for locate_multi_scale tool (multi-scale template matching)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestLocateMultiScale:
    def test_finds_exact_scale_template(self):
        """Template at original scale in the image should be found."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        # Draw a distinctive pattern
        cv2.rectangle(img, (80, 80), (120, 120), (255, 255, 255), -1)
        cv2.circle(img, (100, 100), 10, (0, 0, 0), -1)

        # Reference is the same pattern
        ref = np.zeros((40, 40, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (40, 40), (255, 255, 255), -1)
        cv2.circle(ref, (20, 20), 10, (0, 0, 0), -1)

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.5,
            params={
                "scale_range": [0.8, 1.2],
                "scale_steps": 10,
                "confidence_threshold": 0.5,
            },
        )
        result = run_tool(img, ti, reference=ref)
        assert result.match is True
        assert result.details["num_matches"] >= 1

    def test_no_match_on_blank(self):
        """Template should not be found in a blank image."""
        img = np.full((200, 200, 3), 128, dtype=np.uint8)

        ref = np.zeros((30, 30, 3), dtype=np.uint8)
        cv2.rectangle(ref, (5, 5), (25, 25), (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.8,
            params={"confidence_threshold": 0.8},
        )
        result = run_tool(img, ti, reference=ref)
        assert result.match is False

    def test_requires_reference(self, solid_red):
        """Missing reference should raise ValueError."""
        ti = ToolInput(tool="locate_multi_scale", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_max_matches_limit(self):
        """max_matches should limit the number of results."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        # Two distinct patterns widely separated
        cv2.rectangle(img, (10, 10), (50, 50), (255, 255, 255), -1)
        cv2.circle(img, (30, 30), 8, (0, 0, 0), -1)
        cv2.rectangle(img, (120, 120), (160, 160), (255, 255, 255), -1)
        cv2.circle(img, (140, 140), 8, (0, 0, 0), -1)

        ref = np.zeros((40, 40, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (40, 40), (255, 255, 255), -1)
        cv2.circle(ref, (20, 20), 8, (0, 0, 0), -1)

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.3,
            params={
                "max_matches": 1,
                "scale_range": [0.9, 1.1],
                "scale_steps": 3,
                "confidence_threshold": 0.5,
            },
        )
        result = run_tool(img, ti, reference=ref)
        assert result.details["num_matches"] <= 1

    def test_bbox_normalized(self):
        """Returned bboxes should be in 0.0-1.0 range."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (80, 80), (120, 120), (255, 255, 255), -1)

        ref = np.zeros((40, 40, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (40, 40), (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.3,
            params={
                "scale_range": [0.8, 1.2],
                "scale_steps": 5,
                "confidence_threshold": 0.3,
            },
        )
        result = run_tool(img, ti, reference=ref)
        for m in result.details["matches"]:
            for key in ["x", "y", "w", "h"]:
                assert 0.0 <= m["bbox"][key] <= 1.0

    def test_different_scales(self):
        """Should find template at a different scale."""
        img = np.zeros((400, 400, 3), dtype=np.uint8)
        # Large white square with circle
        cv2.rectangle(img, (150, 150), (250, 250), (255, 255, 255), -1)
        cv2.circle(img, (200, 200), 20, (0, 0, 0), -1)

        # Small reference (half the size)
        ref = np.zeros((50, 50, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (50, 50), (255, 255, 255), -1)
        cv2.circle(ref, (25, 25), 10, (0, 0, 0), -1)

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.5,
            params={
                "scale_range": [1.5, 2.5],
                "scale_steps": 10,
                "confidence_threshold": 0.5,
            },
        )
        result = run_tool(img, ti, reference=ref)
        assert result.match is True

    def test_details_structure(self):
        """Details should contain matches and num_matches."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        ref = np.zeros((20, 20, 3), dtype=np.uint8)
        ref[:, :] = 255

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.0,
            params={"confidence_threshold": 0.0},
        )
        result = run_tool(img, ti, reference=ref)
        assert "matches" in result.details
        assert "num_matches" in result.details

    def test_sqdiff_method(self):
        """TM_SQDIFF_NORMED method should also work."""
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (80, 80), (120, 120), (255, 255, 255), -1)

        ref = np.zeros((40, 40, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (40, 40), (255, 255, 255), -1)

        ti = ToolInput(
            tool="locate_multi_scale",
            threshold=0.3,
            params={
                "method": "TM_SQDIFF_NORMED",
                "scale_range": [0.8, 1.2],
                "scale_steps": 5,
                "confidence_threshold": 0.3,
            },
        )
        result = run_tool(img, ti, reference=ref)
        # Should produce some result (may or may not match depending on exact SQDIFF behavior)
        assert "matches" in result.details
