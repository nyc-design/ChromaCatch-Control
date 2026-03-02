"""Tests for locate_text tool (OCR-based text localization, mocked pytesseract)."""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


def _mock_tesseract_data(words, confidences, lefts, tops, widths, heights):
    """Create a mock pytesseract.image_to_data output dict."""
    n = len(words)
    return {
        "text": words,
        "conf": confidences,
        "level": [5] * n,
        "page_num": [1] * n,
        "block_num": [1] * n,
        "par_num": [1] * n,
        "line_num": [1] * n,
        "word_num": list(range(1, n + 1)),
        "left": lefts,
        "top": tops,
        "width": widths,
        "height": heights,
    }


@pytest.fixture(autouse=True)
def mock_pytesseract():
    """Mock pytesseract module before it gets lazy-imported."""
    mock_pt = MagicMock()
    mock_pt.Output.DICT = "dict"
    sys.modules["pytesseract"] = mock_pt
    yield mock_pt
    del sys.modules["pytesseract"]


class TestLocateText:
    def test_finds_single_word(self, mock_pytesseract, solid_white):
        """Should locate a single matching word."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Hello", "World"],
            [90, 85],
            [10, 60],
            [5, 5],
            [40, 50],
            [20, 20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Hello"},
        )
        result = run_tool(solid_white, ti)
        assert result.match is True
        assert result.details["num_matches"] == 1
        bbox = result.details["matches"][0]["bbox"]
        assert bbox["x"] >= 0.0
        assert bbox["w"] > 0.0

    def test_finds_multi_word_phrase(self, mock_pytesseract, solid_white):
        """Should locate a multi-word phrase by combining consecutive words."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Hello", "World", "Foo"],
            [90, 85, 70],
            [10, 60, 120],
            [5, 5, 5],
            [40, 50, 30],
            [20, 20, 20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Hello World"},
        )
        result = run_tool(solid_white, ti)
        assert result.match is True
        assert result.details["num_matches"] == 1

    def test_no_match_for_missing_text(self, mock_pytesseract, solid_white):
        """Should not match text that is not present."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Hello", "World"],
            [90, 85],
            [10, 60],
            [5, 5],
            [40, 50],
            [20, 20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Pokemon"},
        )
        result = run_tool(solid_white, ti)
        assert result.match is False
        assert result.details["num_matches"] == 0

    def test_low_confidence_words_filtered(self, mock_pytesseract, solid_white):
        """Words below min_confidence should be filtered out."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Hello", "World"],
            [30, 85],  # Hello has low confidence
            [10, 60],
            [5, 5],
            [40, 50],
            [20, 20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Hello", "min_confidence": 60},
        )
        result = run_tool(solid_white, ti)
        assert result.details["num_matches"] == 0

    def test_fuzzy_matching(self, mock_pytesseract, solid_white):
        """Fuzzy mode should match similar text."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Helo"],
            [90],
            [10],
            [5],
            [40],
            [20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Hello", "fuzzy": True},
        )
        result = run_tool(solid_white, ti)
        assert result.match is True
        assert result.details["num_matches"] == 1

    def test_fuzzy_no_match_for_very_different(self, mock_pytesseract, solid_white):
        """Fuzzy mode should not match very different text."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["XYZ"],
            [90],
            [10],
            [5],
            [40],
            [20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Hello", "fuzzy": True},
        )
        result = run_tool(solid_white, ti)
        assert result.details["num_matches"] == 0

    def test_requires_target(self, solid_white):
        """Missing target param should raise ValueError."""
        ti = ToolInput(tool="locate_text", threshold=0.5)
        with pytest.raises(ValueError, match="target"):
            run_tool(solid_white, ti)

    def test_case_insensitive(self, mock_pytesseract, solid_white):
        """Matching should be case-insensitive."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["HELLO"],
            [90],
            [10],
            [5],
            [40],
            [20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "hello"},
        )
        result = run_tool(solid_white, ti)
        assert result.details["num_matches"] == 1

    def test_bbox_normalized(self, mock_pytesseract, solid_white):
        """Returned bboxes should have normalized coordinates."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Test"],
            [90],
            [10],
            [5],
            [40],
            [20],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.0,
            params={"target": "Test"},
        )
        result = run_tool(solid_white, ti)
        bbox = result.details["matches"][0]["bbox"]
        for key in ["x", "y", "w", "h"]:
            assert 0.0 <= bbox[key] <= 1.0

    def test_no_detected_words(self, mock_pytesseract, solid_black):
        """Empty OCR output should produce no matches."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["", ""],
            [-1, -1],
            [0, 0],
            [0, 0],
            [0, 0],
            [0, 0],
        )
        ti = ToolInput(
            tool="locate_text",
            threshold=0.5,
            params={"target": "Hello"},
        )
        result = run_tool(solid_black, ti)
        assert result.match is False
        assert result.score == 0.0
