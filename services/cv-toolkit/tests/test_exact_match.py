"""Tests for exact_match tool (brightness-scaled RMSD with alpha masking)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestExactMatch:
    def test_identical_images(self, solid_red):
        ti = ToolInput(tool="exact_match", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.99
        assert result.match is True

    def test_different_images(self, solid_red, solid_blue):
        ti = ToolInput(tool="exact_match", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.score < 0.5
        assert result.match is False

    def test_requires_reference(self, solid_red):
        ti = ToolInput(tool="exact_match", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_brightness_shifted_still_matches(self):
        """Image with shifted brightness should still score well."""
        dark = np.full((100, 100, 3), 100, dtype=np.uint8)
        bright = np.full((100, 100, 3), 115, dtype=np.uint8)
        ti = ToolInput(tool="exact_match", threshold=0.9)
        result = run_tool(dark, ti, reference=bright)
        # Brightness scaling should compensate for the 15% difference
        assert result.score > 0.9

    def test_alpha_masking(self):
        """Transparent pixels in reference should be excluded."""
        img = np.full((100, 100, 3), 128, dtype=np.uint8)
        # Reference with alpha: left half opaque (same color), right half transparent (diff color)
        ref = np.zeros((100, 100, 4), dtype=np.uint8)
        ref[:, :50, 0] = 128  # Left half B matches
        ref[:, :50, 1] = 128  # Left half G matches
        ref[:, :50, 2] = 128  # Left half R matches
        ref[:, :50, 3] = 255  # Left half opaque
        ref[:, 50:, :3] = 0   # Right half different but transparent
        ref[:, 50:, 3] = 0    # Right half transparent
        ti = ToolInput(
            tool="exact_match", threshold=0.9,
            params={"alpha_channel": True, "use_stddev_weight": False},
        )
        result = run_tool(img, ti, reference=ref)
        # Only left half (matching) is compared, RMSD ~0
        assert result.score > 0.9
        assert result.details["masked_pixels"] == 5000

    def test_stddev_weight_in_details(self, solid_red):
        ti = ToolInput(tool="exact_match", threshold=0.0)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert "stddev_weight" in result.details
        assert "rmsd" in result.details
        assert "normalized_rmsd" in result.details

    def test_different_resolutions(self):
        """Images of different sizes should be resized before comparison."""
        small = np.full((50, 50, 3), 128, dtype=np.uint8)
        large = np.full((200, 200, 3), 128, dtype=np.uint8)
        ti = ToolInput(tool="exact_match", threshold=0.9)
        result = run_tool(small, ti, reference=large)
        assert result.score > 0.9

    def test_no_stddev_weight(self, solid_red, solid_blue):
        ti = ToolInput(
            tool="exact_match", threshold=0.0,
            params={"use_stddev_weight": False},
        )
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.details["stddev_weight"] == 1.0
