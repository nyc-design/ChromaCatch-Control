"""Tests for grid_structure tool (lighting-invariant comparison)."""

import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestGridStructure:
    def test_identical_images(self, checkerboard):
        ti = ToolInput(tool="grid_structure", threshold=0.9)
        result = run_tool(checkerboard, ti, reference=checkerboard)
        assert result.score > 0.95
        assert result.match is True

    def test_lighting_invariance_brightness_shift(self, gradient):
        """Same image with uniform brightness increase should still match."""
        brighter = np.clip(gradient.astype(np.int16) + 30, 0, 255).astype(np.uint8)
        ti = ToolInput(tool="grid_structure", threshold=0.8)
        result = run_tool(gradient, ti, reference=brighter)
        assert result.score > 0.8
        assert result.match is True

    def test_lighting_invariance_color_temp_shift(self, checkerboard):
        """Simulated warm lighting (add to R, subtract from B) should still match."""
        shifted = checkerboard.copy().astype(np.int16)
        shifted[:, :, 2] = np.clip(shifted[:, :, 2] + 20, 0, 255)  # More red
        shifted[:, :, 0] = np.clip(shifted[:, :, 0] - 20, 0, 255)  # Less blue
        shifted = shifted.astype(np.uint8)
        ti = ToolInput(tool="grid_structure", threshold=0.7)
        result = run_tool(checkerboard, ti, reference=shifted)
        assert result.score > 0.7

    def test_completely_different_images(self, gradient, checkerboard):
        ti = ToolInput(tool="grid_structure", threshold=0.9)
        result = run_tool(gradient, ti, reference=checkerboard)
        assert result.score < 0.9

    def test_requires_reference(self, solid_red):
        ti = ToolInput(tool="grid_structure", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_uniform_color_images(self, solid_red):
        """Two identical solid-color images have no internal structure to compare."""
        ti = ToolInput(tool="grid_structure", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_red)
        # Both uniform → both have zero variance → correlation = 1.0
        assert result.score > 0.9

    def test_details_contain_correlation(self, checkerboard):
        ti = ToolInput(tool="grid_structure", threshold=0.0)
        result = run_tool(checkerboard, ti, reference=checkerboard)
        assert "correlation" in result.details
        assert "sample_points" in result.details

    def test_scale_invariance(self, high_res_gradient, low_res_gradient):
        ti = ToolInput(tool="grid_structure", threshold=0.7)
        result = run_tool(high_res_gradient, ti, reference=low_res_gradient)
        assert result.score > 0.7
