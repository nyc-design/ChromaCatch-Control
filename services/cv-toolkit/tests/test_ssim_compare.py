"""Tests for ssim_compare tool (Structural Similarity Index)."""

import cv2
import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestSsimCompare:
    def test_identical_images(self, solid_red):
        ti = ToolInput(tool="ssim_compare", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.99
        assert result.match is True

    def test_different_images(self, solid_red, solid_blue):
        ti = ToolInput(tool="ssim_compare", threshold=0.9)
        result = run_tool(solid_red, ti, reference=solid_blue)
        assert result.score < 0.9
        assert result.match is False

    def test_requires_reference(self, solid_red):
        ti = ToolInput(tool="ssim_compare", threshold=0.5)
        with pytest.raises(ValueError, match="reference"):
            run_tool(solid_red, ti)

    def test_similar_images_high_score(self):
        """Slightly noisy version of same image should score high."""
        img = np.full((100, 100, 3), 128, dtype=np.uint8)
        noisy = img.copy()
        noise = np.random.randint(-5, 5, img.shape, dtype=np.int16)
        noisy = np.clip(noisy.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        ti = ToolInput(tool="ssim_compare", threshold=0.9)
        result = run_tool(noisy, ti, reference=img)
        assert result.score > 0.9

    def test_details_structure(self, solid_red):
        ti = ToolInput(tool="ssim_compare", threshold=0.0)
        result = run_tool(solid_red, ti, reference=solid_red)
        assert "ssim" in result.details
        assert "luminance" in result.details
        assert "contrast" in result.details
        assert "structure" in result.details

    def test_different_resolutions(self):
        small = np.full((50, 50, 3), 128, dtype=np.uint8)
        large = np.full((200, 200, 3), 128, dtype=np.uint8)
        ti = ToolInput(tool="ssim_compare", threshold=0.9)
        result = run_tool(small, ti, reference=large)
        assert result.score > 0.9

    def test_custom_window_size(self, solid_red):
        ti = ToolInput(tool="ssim_compare", threshold=0.0, params={"window_size": 7})
        result = run_tool(solid_red, ti, reference=solid_red)
        assert result.score > 0.99
