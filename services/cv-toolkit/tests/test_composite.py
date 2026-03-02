"""Tests for composite tool - multi-tool aggregation."""

import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


class TestComposite:
    def _make_composite_input(self, steps, aggregate="all"):
        return ToolInput(
            tool="composite",
            threshold=0.5,
            params={
                "composite": {
                    "steps": steps,
                    "aggregate": aggregate,
                }
            },
        )

    def test_all_mode_all_pass(self, solid_red):
        ti = self._make_composite_input(
            steps=[
                {"tool": "brightness_check", "threshold": 0.0},
                {"tool": "brightness_check", "threshold": 0.0},
            ],
            aggregate="all",
        )
        result = run_tool(solid_red, ti)
        assert result.match is True
        assert len(result.details["sub_results"]) == 2

    def test_all_mode_one_fails(self, solid_black):
        ti = self._make_composite_input(
            steps=[
                {"tool": "brightness_check", "threshold": 0.0},
                {"tool": "brightness_check", "threshold": 0.9},  # Will fail for black
            ],
            aggregate="all",
        )
        result = run_tool(solid_black, ti)
        assert result.match is False

    def test_any_mode_one_passes(self, solid_black):
        ti = self._make_composite_input(
            steps=[
                {"tool": "brightness_check", "threshold": 0.9},  # Fails
                {"tool": "brightness_check", "threshold": 0.0},  # Passes
            ],
            aggregate="any",
        )
        result = run_tool(solid_black, ti)
        assert result.match is True

    def test_any_mode_all_fail(self, solid_black):
        ti = self._make_composite_input(
            steps=[
                {"tool": "brightness_check", "threshold": 0.9},
                {"tool": "brightness_check", "threshold": 0.9},
            ],
            aggregate="any",
        )
        result = run_tool(solid_black, ti)
        assert result.match is False

    def test_majority_mode(self, solid_red):
        # Two pass, one fails (brightness of red is moderate)
        ti = self._make_composite_input(
            steps=[
                {"tool": "brightness_check", "threshold": 0.0},
                {"tool": "brightness_check", "threshold": 0.0},
                {"tool": "brightness_check", "threshold": 0.99},
            ],
            aggregate="majority",
        )
        result = run_tool(solid_red, ti)
        # 2 of 3 match = majority
        assert result.match is True

    def test_weighted_mode(self, solid_white):
        ti = ToolInput(
            tool="composite",
            threshold=0.3,
            params={
                "composite": {
                    "steps": [
                        {"tool": "brightness_check", "threshold": 0.0, "weight": 3.0},
                        {"tool": "brightness_check", "threshold": 0.0, "weight": 1.0},
                    ],
                    "aggregate": "weighted",
                }
            },
        )
        result = run_tool(solid_white, ti)
        assert result.match is True
        assert result.score > 0.5

    def test_missing_composite_param_raises(self, solid_red):
        ti = ToolInput(tool="composite", threshold=0.5)
        with pytest.raises(ValueError, match="composite"):
            run_tool(solid_red, ti)

    def test_details_contain_sub_results(self, solid_red):
        ti = self._make_composite_input(
            steps=[{"tool": "brightness_check", "threshold": 0.0}],
            aggregate="all",
        )
        result = run_tool(solid_red, ti)
        assert "sub_results" in result.details
        assert "individual_scores" in result.details
        assert "individual_matches" in result.details
        assert result.details["aggregate"] == "all"

    def test_unknown_sub_tool_raises(self, solid_red):
        ti = self._make_composite_input(
            steps=[{"tool": "nonexistent_tool", "threshold": 0.5}],
            aggregate="all",
        )
        with pytest.raises(KeyError, match="nonexistent_tool"):
            run_tool(solid_red, ti)
