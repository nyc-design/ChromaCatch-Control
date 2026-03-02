"""Tests for the tool registry and dispatcher."""

import numpy as np
import pytest

from cv_toolkit import TOOL_REGISTRY, run_tool
from cv_toolkit.models import ToolInput


EXPECTED_TOOLS = [
    # Confirm (15)
    "brightness_check",
    "color_distance",
    "color_presence",
    "composite",
    "contour_detect",
    "dominant_colors",
    "edge_density",
    "exact_match",
    "grid_similarity",
    "grid_structure",
    "histogram_similarity",
    "motion_detect",
    "object_detect",
    "ocr_read",
    "ssim_compare",
    # Locate (7)
    "locate_color",
    "locate_contour",
    "locate_feature",
    "locate_multi_scale",
    "locate_sub_object",
    "locate_template",
    "locate_text",
    # Extract (3)
    "read_bar",
    "read_number",
    "read_text",
]


class TestToolRegistry:
    def test_all_tools_registered(self):
        for tool_name in EXPECTED_TOOLS:
            assert tool_name in TOOL_REGISTRY, f"{tool_name} not registered"

    def test_total_count(self):
        assert len(TOOL_REGISTRY) == 25

    def test_unknown_tool_raises(self):
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        ti = ToolInput(tool="nonexistent")
        with pytest.raises(KeyError, match="nonexistent"):
            run_tool(img, ti)

    def test_dispatches_correctly(self, solid_red):
        ti = ToolInput(tool="brightness_check", threshold=0.0)
        result = run_tool(solid_red, ti)
        assert result.tool == "brightness_check"
        assert 0.0 <= result.score <= 1.0
