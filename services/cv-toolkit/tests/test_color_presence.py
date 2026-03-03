"""Tests for color_presence tool."""

import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestColorPresence:
    def test_red_image_red_range(self, solid_red):
        # Red in HSV: H~0 or ~179, S=255, V=255
        ti = ToolInput(
            tool="color_presence",
            threshold=0.9,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(solid_red, ti)
        assert result.score > 0.9
        assert result.match is True

    def test_red_image_blue_range(self, solid_red):
        ti = ToolInput(
            tool="color_presence",
            threshold=0.1,
            params={"hsv_lower": [100, 200, 200], "hsv_upper": [130, 255, 255]},
        )
        result = run_tool(solid_red, ti)
        assert result.score < 0.01
        assert result.match is False

    def test_half_and_half(self, half_red_half_blue):
        # Only red half matches
        ti = ToolInput(
            tool="color_presence",
            threshold=0.4,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(half_red_half_blue, ti)
        assert 0.4 < result.score < 0.6

    def test_missing_params_raises(self, solid_red):
        ti = ToolInput(tool="color_presence", threshold=0.5)
        with pytest.raises(ValueError, match="hsv_lower"):
            run_tool(solid_red, ti)

    def test_details_contain_counts(self, solid_red):
        ti = ToolInput(
            tool="color_presence",
            threshold=0.0,
            params={"hsv_lower": [0, 200, 200], "hsv_upper": [10, 255, 255]},
        )
        result = run_tool(solid_red, ti)
        assert "matching_pixels" in result.details
        assert "total_pixels" in result.details
