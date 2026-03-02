"""Tests for motion_detect tool."""

import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestMotionDetect:
    def test_no_motion_same_image(self, solid_red):
        ti = ToolInput(tool="motion_detect", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.99
        assert result.match is True

    def test_lots_of_motion_different_images(self, solid_red, solid_blue):
        ti = ToolInput(tool="motion_detect", threshold=0.5)
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.score < 0.5

    def test_small_change_intermediate(self, gradient):
        shifted = np.clip(gradient.astype(np.int16) + 15, 0, 255).astype(np.uint8)
        ti = ToolInput(tool="motion_detect", threshold=0.5)
        result = run_tool(gradient, ti, reference=shifted)
        assert 0.5 < result.score < 1.0

    def test_requires_reference(self, solid_red):
        ti = ToolInput(tool="motion_detect", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_high_score_means_no_change(self, checkerboard):
        ti = ToolInput(tool="motion_detect", threshold=0.0)
        result = run_tool(checkerboard, ti, reference=checkerboard)
        assert result.score > 0.99
        assert result.details["rmsd"] < 1.0

    def test_details_contain_rmsd(self, solid_red, solid_blue):
        ti = ToolInput(tool="motion_detect", threshold=0.0)
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert "rmsd" in result.details
        assert "change_ratio" in result.details
