"""Tests for edge_density tool."""

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestEdgeDensity:
    def test_solid_color_no_edges(self, solid_red):
        ti = ToolInput(tool="edge_density", threshold=0.01)
        result = run_tool(solid_red, ti)
        assert result.score < 0.01

    def test_checkerboard_has_edges(self, checkerboard):
        ti = ToolInput(tool="edge_density", threshold=0.01)
        result = run_tool(checkerboard, ti)
        assert result.score > 0.01

    def test_with_reference_same(self, checkerboard):
        ti = ToolInput(tool="edge_density", threshold=0.8)
        result = run_tool(checkerboard, ti, reference=checkerboard)
        assert result.score > 0.9
        assert result.match is True

    def test_with_reference_different(self, checkerboard, solid_red):
        ti = ToolInput(tool="edge_density", threshold=0.8)
        result = run_tool(checkerboard, ti, reference=solid_red)
        assert result.score < 0.5

    def test_details_contain_density(self, gradient):
        ti = ToolInput(tool="edge_density", threshold=0.0)
        result = run_tool(gradient, ti)
        assert "edge_density" in result.details
        assert "edge_pixels" in result.details
