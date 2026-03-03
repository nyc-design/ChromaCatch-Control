"""Tests for locate_sub_object tool (PA's sub-feature localization)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestLocateSubObject:
    def _make_object_with_feature(self, img, x, y, w, h, feat_color=(0, 0, 255)):
        """Draw an object with a distinctive sub-feature.

        Object is a gray rectangle, sub-feature is a colored square
        in the top-left quarter of the object.
        """
        cv2.rectangle(img, (x, y), (x + w, y + h), (128, 128, 128), -1)
        fw = w // 4
        fh = h // 4
        cv2.rectangle(img, (x, y), (x + fw, y + fh), feat_color, -1)
        return img

    def test_finds_object_via_sub_feature(self):
        """Should locate full object by finding its distinctive sub-feature."""
        scene = np.zeros((300, 300, 3), dtype=np.uint8)
        self._make_object_with_feature(scene, 100, 100, 80, 80)

        # Reference is the same object (80x80)
        ref = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (80, 80), (128, 128, 128), -1)
        cv2.rectangle(ref, (0, 0), (20, 20), (0, 0, 255), -1)  # Sub-feature

        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.3,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.25},
                "sub_color_filters": [[[0, 200, 200], [10, 255, 255]]],
                "sub_rmsd_threshold": 100.0,
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.details["num_matches"] >= 1

    def test_no_match_without_feature(self):
        """Scene without the sub-feature color should find nothing."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        # Only gray rectangles, no red sub-feature
        cv2.rectangle(scene, (50, 50), (150, 150), (128, 128, 128), -1)

        ref = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (80, 80), (128, 128, 128), -1)
        cv2.rectangle(ref, (0, 0), (20, 20), (0, 0, 255), -1)

        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.5,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.25},
                "sub_color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.details["num_matches"] == 0

    def test_requires_reference(self, solid_red):
        """Missing reference should raise ValueError."""
        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.5,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
                "sub_color_filters": [[[0, 0, 0], [180, 255, 255]]],
            },
        )
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_requires_sub_reference_region(self, solid_red):
        """Missing sub_reference_region should raise ValueError."""
        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.5,
            params={
                "sub_color_filters": [[[0, 0, 0], [180, 255, 255]]],
            },
        )
        with pytest.raises(ValueError, match="sub_reference_region"):
            run_tool(solid_red, ti, reference=solid_red)

    def test_requires_sub_color_filters(self, solid_red):
        """Missing sub_color_filters should raise ValueError."""
        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.5,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
            },
        )
        with pytest.raises(ValueError, match="sub_color_filters"):
            run_tool(solid_red, ti, reference=solid_red)

    def test_bbox_normalized(self):
        """Returned bboxes should be normalized."""
        scene = np.zeros((300, 300, 3), dtype=np.uint8)
        self._make_object_with_feature(scene, 100, 100, 80, 80)

        ref = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (80, 80), (128, 128, 128), -1)
        cv2.rectangle(ref, (0, 0), (20, 20), (0, 0, 255), -1)

        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.0,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.25},
                "sub_color_filters": [[[0, 200, 200], [10, 255, 255]]],
                "sub_rmsd_threshold": 100.0,
            },
        )
        result = run_tool(scene, ti, reference=ref)
        for m in result.details["matches"]:
            for key in ["x", "y", "w", "h"]:
                assert 0.0 <= m["bbox"][key] <= 1.0

    def test_max_matches_limit(self):
        """max_matches should limit the number of results."""
        scene = np.zeros((400, 400, 3), dtype=np.uint8)
        # Multiple objects with red sub-features
        for ox in [50, 200]:
            for oy in [50, 200]:
                self._make_object_with_feature(scene, ox, oy, 60, 60)

        ref = np.zeros((60, 60, 3), dtype=np.uint8)
        cv2.rectangle(ref, (0, 0), (60, 60), (128, 128, 128), -1)
        cv2.rectangle(ref, (0, 0), (15, 15), (0, 0, 255), -1)

        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.0,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.25},
                "sub_color_filters": [[[0, 200, 200], [10, 255, 255]]],
                "sub_rmsd_threshold": 100.0,
                "max_matches": 2,
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.details["num_matches"] <= 2

    def test_details_structure(self):
        """Details should contain matches and num_matches."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)
        ref = np.zeros((50, 50, 3), dtype=np.uint8)

        ti = ToolInput(
            tool="locate_sub_object",
            threshold=0.5,
            params={
                "sub_reference_region": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
                "sub_color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert "matches" in result.details
        assert "num_matches" in result.details
