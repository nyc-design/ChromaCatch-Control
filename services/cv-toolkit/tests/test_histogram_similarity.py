"""Tests for histogram_similarity tool."""

import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestHistogramSimilarity:
    def test_identical_images(self, solid_red):
        ti = ToolInput(tool="histogram_similarity", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.9
        assert result.match is True

    def test_red_vs_blue(self, solid_red, solid_blue):
        # In HSV, red and blue differ only in H channel (S and V identical)
        # So per-channel average shows ~0.83 similarity — expected behavior
        ti = ToolInput(tool="histogram_similarity", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.score < 0.9
        assert result.match is False

    def test_correlation_method(self, gradient):
        ti = ToolInput(
            tool="histogram_similarity",
            threshold=0.9,
            params={"method": "correlation"},
        )
        result = run_tool(gradient, ti, reference=gradient)
        assert result.score > 0.9

    def test_chi_squared_method(self, gradient):
        ti = ToolInput(
            tool="histogram_similarity",
            threshold=0.5,
            params={"method": "chi_squared"},
        )
        result = run_tool(gradient, ti, reference=gradient)
        assert result.score > 0.9

    def test_bhattacharyya_method(self, gradient):
        ti = ToolInput(
            tool="histogram_similarity",
            threshold=0.5,
            params={"method": "bhattacharyya"},
        )
        result = run_tool(gradient, ti, reference=gradient)
        assert result.score > 0.9

    def test_requires_reference(self, solid_red):
        ti = ToolInput(tool="histogram_similarity", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_details_contain_method(self, solid_red):
        ti = ToolInput(
            tool="histogram_similarity",
            threshold=0.0,
            params={"method": "correlation"},
        )
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.details["method"] == "correlation"
        assert "per_channel_raw" in result.details

    def test_invalid_method_raises(self, solid_red):
        ti = ToolInput(
            tool="histogram_similarity",
            threshold=0.5,
            params={"method": "invalid"},
        )
        with pytest.raises(ValueError, match="Unknown method"):
            run_tool(solid_red, ti, reference=solid_red)
