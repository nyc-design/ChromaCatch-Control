"""Tests for read_bar tool using synthetic images with known fill ratios."""

import numpy as np
import pytest

from cv_toolkit.models import Region, ToolInput
from cv_toolkit.registry import run_tool


def _make_horizontal_bar(fill_fraction: float, height: int = 40, width: int = 200):
    """Create a horizontal bar image with green fill on black background.

    Green (BGR: 0,255,0) fills from left to fill_fraction * width.
    Black fills the remainder.
    """
    img = np.zeros((height, width, 3), dtype=np.uint8)
    fill_w = int(width * fill_fraction)
    if fill_w > 0:
        img[:, :fill_w] = (0, 255, 0)  # Green in BGR
    return img


def _make_horizontal_bar_with_bg(fill_fraction: float, height: int = 40, width: int = 200):
    """Create a horizontal bar with green fill and red empty background.

    Green fills left portion, red fills right portion.
    """
    img = np.zeros((height, width, 3), dtype=np.uint8)
    fill_w = int(width * fill_fraction)
    if fill_w > 0:
        img[:, :fill_w] = (0, 255, 0)  # Green in BGR
    if fill_w < width:
        img[:, fill_w:] = (0, 0, 255)  # Red in BGR
    return img


def _make_vertical_bar(fill_fraction: float, height: int = 200, width: int = 40):
    """Create a vertical bar image with green fill from bottom up.

    Green fills from bottom to fill_fraction * height.
    Black fills the upper portion.
    """
    img = np.zeros((height, width, 3), dtype=np.uint8)
    fill_h = int(height * fill_fraction)
    if fill_h > 0:
        img[height - fill_h :, :] = (0, 255, 0)  # Green in BGR
    return img


# HSV range for pure green (BGR 0,255,0 => HSV ~60,255,255)
_GREEN_HSV_LOWER = [35, 100, 100]
_GREEN_HSV_UPPER = [85, 255, 255]

# HSV range for pure red (BGR 0,0,255 => HSV ~0,255,255)
_RED_HSV_LOWER = [0, 100, 100]
_RED_HSV_UPPER = [10, 255, 255]


class TestReadBar:
    def test_full_horizontal_bar(self):
        """100% filled horizontal bar returns score ~1.0."""
        img = _make_horizontal_bar(1.0)
        ti = ToolInput(
            tool="read_bar",
            threshold=0.5,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
            },
        )
        result = run_tool(img, ti)
        assert result.tool == "read_bar"
        assert result.score >= 0.95
        assert result.match is True
        assert result.details["orientation"] == "horizontal"

    def test_empty_horizontal_bar(self):
        """0% filled horizontal bar returns score 0.0."""
        img = _make_horizontal_bar(0.0)
        ti = ToolInput(
            tool="read_bar",
            threshold=0.5,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
            },
        )
        result = run_tool(img, ti)
        assert result.score == 0.0
        assert result.match is False
        assert result.details["bar_pixels"] == 0

    def test_half_horizontal_bar(self):
        """50% filled horizontal bar returns score ~0.5."""
        img = _make_horizontal_bar(0.5)
        ti = ToolInput(
            tool="read_bar",
            threshold=0.0,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
            },
        )
        result = run_tool(img, ti)
        assert 0.4 <= result.score <= 0.6
        assert result.details["fill_ratio"] == result.score

    def test_horizontal_bar_with_background(self):
        """Bar with explicit background uses filled/(filled+empty) ratio."""
        img = _make_horizontal_bar_with_bg(0.5)
        ti = ToolInput(
            tool="read_bar",
            threshold=0.0,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
                "bg_hsv_lower": _RED_HSV_LOWER,
                "bg_hsv_upper": _RED_HSV_UPPER,
            },
        )
        result = run_tool(img, ti)
        assert 0.4 <= result.score <= 0.6

    def test_vertical_bar(self):
        """Vertical bar fill is measured bottom-to-top."""
        img = _make_vertical_bar(0.75)
        ti = ToolInput(
            tool="read_bar",
            threshold=0.0,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
                "orientation": "vertical",
            },
        )
        result = run_tool(img, ti)
        assert 0.65 <= result.score <= 0.85
        assert result.details["orientation"] == "vertical"

    def test_missing_params_raises(self, solid_white):
        """Raises ValueError when required HSV params missing."""
        ti = ToolInput(tool="read_bar", threshold=0.5, params={})
        with pytest.raises(ValueError, match="bar_hsv_lower"):
            run_tool(solid_white, ti)

    def test_invalid_orientation_raises(self, solid_white):
        """Raises ValueError for unknown orientation."""
        ti = ToolInput(
            tool="read_bar",
            threshold=0.5,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
                "orientation": "diagonal",
            },
        )
        with pytest.raises(ValueError, match="orientation"):
            run_tool(solid_white, ti)

    def test_region_support(self):
        """Region parameter crops the image before analysis."""
        # Create a wide image where only the right half has a bar
        img = np.zeros((40, 400, 3), dtype=np.uint8)
        img[:, 200:400] = (0, 255, 0)  # Green right half

        ti = ToolInput(
            tool="read_bar",
            threshold=0.0,
            region=Region(x=0.5, y=0.0, w=0.5, h=1.0),
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
            },
        )
        result = run_tool(img, ti)
        # Cropped to right half which is all green
        assert result.score >= 0.95

    def test_details_structure(self):
        """Result details contain all expected fields."""
        img = _make_horizontal_bar(0.5)
        ti = ToolInput(
            tool="read_bar",
            threshold=0.0,
            params={
                "bar_hsv_lower": _GREEN_HSV_LOWER,
                "bar_hsv_upper": _GREEN_HSV_UPPER,
            },
        )
        result = run_tool(img, ti)
        assert "fill_ratio" in result.details
        assert "bar_pixels" in result.details
        assert "total_pixels" in result.details
        assert "filled_extent_px" in result.details
        assert "total_extent_px" in result.details
        assert "orientation" in result.details
