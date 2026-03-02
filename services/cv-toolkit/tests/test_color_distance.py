"""Tests for color_distance tool."""

import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestColorDistance:
    def test_red_matches_red_target(self, solid_red):
        ti = ToolInput(
            tool="color_distance",
            threshold=0.8,
            params={"target_bgr": [0, 0, 255]},
        )
        result = run_tool(solid_red, ti)
        assert result.score > 0.9
        assert result.match is True

    def test_red_does_not_match_blue_target(self, solid_red):
        ti = ToolInput(
            tool="color_distance",
            threshold=0.5,
            params={"target_bgr": [255, 0, 0]},
        )
        result = run_tool(solid_red, ti)
        assert result.score < 0.5
        assert result.match is False

    def test_with_reference_image(self, solid_red):
        ti = ToolInput(tool="color_distance", threshold=0.8)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.95

    def test_target_lab_param(self, solid_black):
        # Black in LAB is approximately [0, 128, 128] in OpenCV (L=0)
        ti = ToolInput(
            tool="color_distance",
            threshold=0.5,
            params={"target_lab": [0.0, 128.0, 128.0]},
        )
        result = run_tool(solid_black, ti)
        assert result.score > 0.5

    def test_no_target_raises(self, solid_red):
        ti = ToolInput(tool="color_distance", threshold=0.5)
        with pytest.raises(ValueError, match="target"):
            run_tool(solid_red, ti)

    def test_details_contain_delta_e(self, solid_red):
        ti = ToolInput(
            tool="color_distance",
            threshold=0.0,
            params={"target_bgr": [0, 0, 255]},
        )
        result = run_tool(solid_red, ti)
        assert "delta_e" in result.details
        assert "image_mean_lab" in result.details
        assert "target_lab" in result.details
