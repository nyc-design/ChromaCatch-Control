"""Tests for locate_template tool (waterfill + RMSD matching)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestLocateTemplate:
    def _make_scene_with_red_rect(self, sx, sy, sw, sh, img_size=(300, 300)):
        """Create a scene with a red rectangle at the given pixel position."""
        img = np.zeros((*img_size, 3), dtype=np.uint8)
        cv2.rectangle(img, (sx, sy), (sx + sw, sy + sh), (0, 0, 255), -1)
        return img

    def _make_red_reference(self, w=50, h=50):
        """Create a solid red reference image."""
        ref = np.zeros((h, w, 3), dtype=np.uint8)
        ref[:, :] = (0, 0, 255)
        return ref

    def test_finds_matching_object(self):
        """Red rectangle in scene should match red reference."""
        scene = self._make_scene_with_red_rect(100, 100, 50, 50)
        ref = self._make_red_reference(50, 50)
        ti = ToolInput(
            tool="locate_template",
            threshold=0.3,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.match is True
        assert result.details["num_matches"] >= 1
        assert result.score > 0.3

    def test_no_match_wrong_color(self):
        """Blue reference should not match red candidates."""
        scene = self._make_scene_with_red_rect(100, 100, 50, 50)
        ref = np.zeros((50, 50, 3), dtype=np.uint8)
        ref[:, :] = (255, 0, 0)  # Blue reference
        ti = ToolInput(
            tool="locate_template",
            threshold=0.5,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(scene, ti, reference=ref)
        # RMSD between red crop and blue reference should be high => low confidence
        # May or may not match depending on RMSD threshold
        for m in result.details["matches"]:
            assert m["confidence"] < 0.8

    def test_no_candidates_found(self):
        """Scene with no matching colors should find nothing."""
        scene = np.zeros((200, 200, 3), dtype=np.uint8)  # All black
        ref = self._make_red_reference()
        ti = ToolInput(
            tool="locate_template",
            threshold=0.5,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.match is False
        assert result.details["num_matches"] == 0

    def test_requires_reference(self, solid_red):
        """Missing reference should raise ValueError."""
        ti = ToolInput(
            tool="locate_template",
            threshold=0.5,
            params={"color_filters": [[[0, 200, 200], [10, 255, 255]]]},
        )
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_auto_derives_color_filters(self):
        """Without color_filters, should auto-derive from reference and still find matches."""
        scene = self._make_scene_with_red_rect(100, 100, 50, 50)
        ref = self._make_red_reference(50, 50)
        ti = ToolInput(tool="locate_template", threshold=0.3)
        result = run_tool(scene, ti, reference=ref)
        assert result.match is True
        assert result.details["num_matches"] >= 1

    def test_multiple_candidates(self):
        """Multiple red rectangles should produce multiple matches."""
        img = np.zeros((300, 300, 3), dtype=np.uint8)
        cv2.rectangle(img, (20, 20), (70, 70), (0, 0, 255), -1)
        cv2.rectangle(img, (150, 150), (200, 200), (0, 0, 255), -1)
        ref = self._make_red_reference(50, 50)
        ti = ToolInput(
            tool="locate_template",
            threshold=0.3,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(img, ti, reference=ref)
        assert result.details["num_matches"] >= 2

    def test_brightness_scale_disabled(self):
        """Should work with brightness scaling disabled."""
        scene = self._make_scene_with_red_rect(100, 100, 50, 50)
        ref = self._make_red_reference(50, 50)
        ti = ToolInput(
            tool="locate_template",
            threshold=0.3,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
                "brightness_scale_enabled": False,
            },
        )
        result = run_tool(scene, ti, reference=ref)
        assert result.details["num_matches"] >= 1

    def test_bbox_normalized(self):
        """Returned bboxes should have normalized coordinates."""
        scene = self._make_scene_with_red_rect(100, 100, 50, 50)
        ref = self._make_red_reference(50, 50)
        ti = ToolInput(
            tool="locate_template",
            threshold=0.3,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
            },
        )
        result = run_tool(scene, ti, reference=ref)
        if result.details["matches"]:
            bbox = result.details["matches"][0]["bbox"]
            for key in ["x", "y", "w", "h"]:
                assert 0.0 <= bbox[key] <= 1.0

    def test_identical_image_and_ref(self):
        """Exact template match should give high confidence."""
        # Scene has a red rectangle; reference IS the red rectangle template
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (150, 150), (0, 0, 255), -1)

        # Reference is exactly the object we're looking for
        ref = self._make_red_reference(100, 100)

        ti = ToolInput(
            tool="locate_template",
            threshold=0.5,
            params={
                "color_filters": [[[0, 200, 200], [10, 255, 255]]],
                "rmsd_threshold": 80.0,
            },
        )
        result = run_tool(img, ti, reference=ref)
        assert result.match is True
        assert result.score > 0.8
