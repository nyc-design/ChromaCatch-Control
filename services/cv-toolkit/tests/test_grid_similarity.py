"""Tests for grid_similarity tool."""

import numpy as np
import pytest

from cv_toolkit.models import Region, ToolInput
from cv_toolkit.registry import run_tool


class TestGridSimilarity:
    def test_identical_images(self, solid_red):
        ti = ToolInput(tool="grid_similarity", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.99
        assert result.match is True

    def test_different_colors(self, solid_red, solid_blue):
        ti = ToolInput(tool="grid_similarity", threshold=0.5)
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.score < 0.5
        assert result.match is False

    def test_scale_invariance(self, high_res_gradient, low_res_gradient):
        ti = ToolInput(tool="grid_similarity", threshold=0.7)
        result = run_tool(high_res_gradient, ti, reference=low_res_gradient)
        # Same gradient at different resolutions should be similar
        assert result.score > 0.7

    def test_region_comparison(self, half_red_half_blue, solid_red):
        # Compare only the left half (red) of half_red_half_blue against solid_red
        region = Region(x=0.0, y=0.0, w=0.5, h=1.0)
        ti = ToolInput(tool="grid_similarity", threshold=0.8, region=region)
        result = run_tool(half_red_half_blue, ti, reference=solid_red)
        assert result.score > 0.8

    def test_requires_reference(self, solid_red):
        ti = ToolInput(tool="grid_similarity", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_custom_grid_size(self, gradient):
        ti = ToolInput(
            tool="grid_similarity",
            threshold=0.5,
            params={"grid_size": 50},
        )
        result = run_tool(gradient, ti, reference=gradient)
        assert result.score > 0.99
        assert result.details["grid_shape"][0] <= 50 or result.details["grid_shape"][1] <= 50

    def test_gradient_vs_shifted(self, gradient):
        shifted = np.clip(gradient.astype(np.int16) + 30, 0, 255).astype(np.uint8)
        ti = ToolInput(tool="grid_similarity", threshold=0.5)
        result = run_tool(gradient, ti, reference=shifted)
        # Should have intermediate score - similar but not identical
        assert 0.5 < result.score < 1.0

    def test_details_contain_metrics(self, solid_red):
        ti = ToolInput(tool="grid_similarity", threshold=0.0)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert "mean_delta_e" in result.details
        assert "grid_shape" in result.details
