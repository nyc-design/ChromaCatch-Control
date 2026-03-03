"""Tests for dominant_colors tool."""

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestDominantColors:
    def test_solid_color_one_dominant(self, solid_red):
        ti = ToolInput(
            tool="dominant_colors", threshold=0.0, params={"k": 1}
        )
        result = run_tool(solid_red, ti)
        assert result.score == 1.0  # No reference = extraction success
        assert len(result.details["colors_lab"]) == 1
        assert result.details["proportions"][0] > 0.99

    def test_two_color_image_k2(self, half_red_half_blue):
        ti = ToolInput(
            tool="dominant_colors", threshold=0.0, params={"k": 2}
        )
        result = run_tool(half_red_half_blue, ti)
        assert len(result.details["colors_lab"]) == 2
        # Each should be roughly 50%
        for p in result.details["proportions"]:
            assert 0.3 < p < 0.7

    def test_with_reference_same(self, solid_red):
        ti = ToolInput(
            tool="dominant_colors", threshold=0.8, params={"k": 3}
        )
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.8
        assert result.match is True

    def test_with_reference_different(self, solid_red, solid_blue):
        ti = ToolInput(
            tool="dominant_colors", threshold=0.5, params={"k": 3}
        )
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.score < 0.5

    def test_details_structure(self, gradient):
        ti = ToolInput(
            tool="dominant_colors", threshold=0.0, params={"k": 3}
        )
        result = run_tool(gradient, ti)
        assert "colors_lab" in result.details
        assert "colors_bgr" in result.details
        assert "proportions" in result.details
        assert len(result.details["colors_lab"]) == 3
