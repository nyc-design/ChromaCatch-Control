"""Tests for brightness_check tool."""

import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestBrightnessCheck:
    def test_black_image_low_brightness(self, solid_black):
        ti = ToolInput(tool="brightness_check", threshold=0.5)
        result = run_tool(solid_black, ti)
        assert result.score < 0.1
        assert result.match is False

    def test_white_image_high_brightness(self, solid_white):
        ti = ToolInput(tool="brightness_check", threshold=0.5)
        result = run_tool(solid_white, ti)
        assert result.score > 0.9
        assert result.match is True

    def test_gray_midpoint(self):
        gray = np.full((100, 100, 3), 128, dtype=np.uint8)
        ti = ToolInput(tool="brightness_check", threshold=0.3)
        result = run_tool(gray, ti)
        assert 0.3 < result.score < 0.7

    def test_with_reference_same(self, solid_white):
        ti = ToolInput(tool="brightness_check", threshold=0.8)
        result = run_tool(solid_white, ti, reference=solid_white)
        assert result.score > 0.95
        assert result.match is True

    def test_with_reference_different(self, solid_black, solid_white):
        ti = ToolInput(tool="brightness_check", threshold=0.5)
        result = run_tool(solid_black, ti, reference=solid_white)
        assert result.score < 0.2
        assert result.match is False

    def test_expected_brightness_param(self, solid_white):
        ti = ToolInput(
            tool="brightness_check",
            threshold=0.8,
            params={"expected_brightness": 1.0},
        )
        result = run_tool(solid_white, ti)
        assert result.score > 0.8

    def test_details_contain_brightness(self, solid_red):
        ti = ToolInput(tool="brightness_check", threshold=0.0)
        result = run_tool(solid_red, ti)
        assert "mean_brightness" in result.details
        assert 0.0 <= result.details["mean_brightness"] <= 1.0
