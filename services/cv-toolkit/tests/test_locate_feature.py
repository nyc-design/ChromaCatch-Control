"""Tests for locate_feature tool (ORB/AKAZE keypoint matching)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestLocateFeature:
    def _make_textured_image(self, w=200, h=200, seed=42):
        """Create a textured image with enough features for detection."""
        rng = np.random.RandomState(seed)
        img = rng.randint(0, 256, (h, w, 3), dtype=np.uint8)
        # Add some structure
        cv2.rectangle(img, (40, 40), (160, 160), (0, 0, 255), 2)
        cv2.circle(img, (100, 100), 30, (255, 0, 0), 2)
        cv2.line(img, (20, 20), (180, 180), (0, 255, 0), 2)
        return img

    def test_finds_same_image(self):
        """Image used as its own reference should be found."""
        img = self._make_textured_image()
        ti = ToolInput(
            tool="locate_feature",
            threshold=0.3,
            params={"min_matches": 5},
        )
        result = run_tool(img, ti, reference=img)
        assert result.score > 0.0
        assert result.details["good_matches"] > 0

    def test_no_features_on_blank(self):
        """Blank image should produce no matches."""
        blank = np.zeros((200, 200, 3), dtype=np.uint8)
        ref = self._make_textured_image()
        ti = ToolInput(
            tool="locate_feature",
            threshold=0.5,
            params={"min_matches": 10},
        )
        result = run_tool(blank, ti, reference=ref)
        assert result.details["good_matches"] == 0

    def test_requires_reference(self, solid_red):
        """Missing reference should raise ValueError."""
        ti = ToolInput(tool="locate_feature", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_akaze_detector(self):
        """Should work with AKAZE detector."""
        img = self._make_textured_image()
        ti = ToolInput(
            tool="locate_feature",
            threshold=0.3,
            params={"detector": "akaze", "min_matches": 5},
        )
        result = run_tool(img, ti, reference=img)
        assert result.score > 0.0

    def test_confidence_capped_at_one(self):
        """Confidence should not exceed 1.0."""
        img = self._make_textured_image()
        ti = ToolInput(
            tool="locate_feature",
            threshold=0.0,
            params={"min_matches": 3},
        )
        result = run_tool(img, ti, reference=img)
        assert result.score <= 1.0

    def test_bbox_normalized_when_found(self):
        """When a match is found, bbox should be normalized."""
        img = self._make_textured_image(300, 300)
        ti = ToolInput(
            tool="locate_feature",
            threshold=0.0,
            params={"min_matches": 5},
        )
        result = run_tool(img, ti, reference=img)
        if result.details["matches"]:
            bbox = result.details["matches"][0]["bbox"]
            for key in ["x", "y", "w", "h"]:
                assert 0.0 <= bbox[key] <= 1.0

    def test_ratio_threshold(self):
        """Stricter ratio threshold should reduce good matches."""
        img = self._make_textured_image()
        ti_loose = ToolInput(
            tool="locate_feature",
            threshold=0.0,
            params={"ratio_threshold": 0.95, "min_matches": 3},
        )
        ti_strict = ToolInput(
            tool="locate_feature",
            threshold=0.0,
            params={"ratio_threshold": 0.3, "min_matches": 3},
        )
        result_loose = run_tool(img, ti_loose, reference=img)
        result_strict = run_tool(img, ti_strict, reference=img)
        assert result_loose.details["good_matches"] >= result_strict.details["good_matches"]

    def test_details_structure(self):
        """Details should contain matches, num_matches, good_matches."""
        img = self._make_textured_image()
        ti = ToolInput(
            tool="locate_feature",
            threshold=0.0,
            params={"min_matches": 5},
        )
        result = run_tool(img, ti, reference=img)
        assert "matches" in result.details
        assert "num_matches" in result.details
        assert "good_matches" in result.details
