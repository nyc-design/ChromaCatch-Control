"""Tests for CV toolkit Pydantic models."""

import pytest
from pydantic import ValidationError

from cv_toolkit.models import (
    CompositeInput,
    CompositeStep,
    Region,
    ToolInput,
    ToolResult,
)


class TestRegion:
    def test_valid_creation(self):
        r = Region(x=0.1, y=0.2, w=0.5, h=0.3)
        assert r.x == 0.1
        assert r.w == 0.5

    def test_boundary_values(self):
        r = Region(x=0.0, y=0.0, w=1.0, h=1.0)
        assert r.x == 0.0
        assert r.h == 1.0

    def test_rejects_negative(self):
        with pytest.raises(ValidationError):
            Region(x=-0.1, y=0.0, w=0.5, h=0.5)

    def test_rejects_over_one(self):
        with pytest.raises(ValidationError):
            Region(x=0.0, y=0.0, w=1.1, h=0.5)

    def test_rejects_zero_width(self):
        with pytest.raises(ValidationError):
            Region(x=0.0, y=0.0, w=0.0, h=0.5)

    def test_json_roundtrip(self):
        r = Region(x=0.25, y=0.5, w=0.3, h=0.4)
        raw = r.model_dump_json()
        parsed = Region.model_validate_json(raw)
        assert parsed == r


class TestToolInput:
    def test_defaults(self):
        ti = ToolInput(tool="grid_similarity")
        assert ti.threshold == 0.5
        assert ti.region is None
        assert ti.params == {}

    def test_with_region(self):
        ti = ToolInput(
            tool="test",
            threshold=0.8,
            region=Region(x=0.1, y=0.1, w=0.5, h=0.5),
        )
        assert ti.region is not None
        assert ti.region.x == 0.1

    def test_with_params(self):
        ti = ToolInput(tool="test", params={"grid_size": 300})
        assert ti.params["grid_size"] == 300

    def test_rejects_threshold_over_one(self):
        with pytest.raises(ValidationError):
            ToolInput(tool="test", threshold=1.5)

    def test_rejects_negative_threshold(self):
        with pytest.raises(ValidationError):
            ToolInput(tool="test", threshold=-0.1)

    def test_json_roundtrip(self):
        ti = ToolInput(tool="grid_similarity", threshold=0.9)
        raw = ti.model_dump_json()
        parsed = ToolInput.model_validate_json(raw)
        assert parsed.tool == "grid_similarity"
        assert parsed.threshold == 0.9


class TestToolResult:
    def test_creation(self):
        tr = ToolResult(tool="test", score=0.85, match=True, threshold=0.8)
        assert tr.score == 0.85
        assert tr.match is True

    def test_details_default_empty(self):
        tr = ToolResult(tool="test", score=0.5, match=True, threshold=0.5)
        assert tr.details == {}

    def test_with_details(self):
        tr = ToolResult(
            tool="test",
            score=0.9,
            match=True,
            threshold=0.8,
            details={"mean_delta_e": 12.5},
        )
        assert tr.details["mean_delta_e"] == 12.5

    def test_json_roundtrip(self):
        tr = ToolResult(tool="test", score=0.75, match=True, threshold=0.7)
        raw = tr.model_dump_json()
        parsed = ToolResult.model_validate_json(raw)
        assert parsed == tr


class TestCompositeStep:
    def test_defaults(self):
        cs = CompositeStep(tool="grid_similarity")
        assert cs.threshold == 0.5
        assert cs.weight == 1.0

    def test_rejects_zero_weight(self):
        with pytest.raises(ValidationError):
            CompositeStep(tool="test", weight=0.0)

    def test_rejects_negative_weight(self):
        with pytest.raises(ValidationError):
            CompositeStep(tool="test", weight=-1.0)


class TestCompositeInput:
    def test_valid_aggregates(self):
        for mode in ("all", "any", "majority", "weighted"):
            ci = CompositeInput(
                steps=[CompositeStep(tool="test")], aggregate=mode
            )
            assert ci.aggregate == mode

    def test_rejects_invalid_aggregate(self):
        with pytest.raises(ValidationError):
            CompositeInput(
                steps=[CompositeStep(tool="test")], aggregate="invalid"
            )

    def test_json_roundtrip(self):
        ci = CompositeInput(
            steps=[
                CompositeStep(tool="grid_similarity", threshold=0.8),
                CompositeStep(tool="brightness_check", threshold=0.9),
            ],
            aggregate="all",
        )
        raw = ci.model_dump_json()
        parsed = CompositeInput.model_validate_json(raw)
        assert len(parsed.steps) == 2
        assert parsed.aggregate == "all"
