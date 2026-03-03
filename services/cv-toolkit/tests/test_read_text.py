"""Tests for read_text tool (mocked pytesseract)."""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from cv_toolkit.models import Region, ToolInput
from cv_toolkit.registry import run_tool


def _mock_tesseract_data(texts, confidences, lefts=None, tops=None, widths=None, heights=None):
    """Create a mock pytesseract image_to_data output dict."""
    n = len(texts)
    return {
        "text": texts,
        "conf": confidences,
        "level": [5] * n,
        "page_num": [1] * n,
        "block_num": [1] * n,
        "par_num": [1] * n,
        "line_num": [1] * n,
        "word_num": list(range(1, n + 1)),
        "left": lefts or [10 * i for i in range(n)],
        "top": tops or [0] * n,
        "width": widths or [50] * n,
        "height": heights or [20] * n,
    }


@pytest.fixture(autouse=True)
def mock_pytesseract():
    """Mock pytesseract module before it gets lazy-imported."""
    mock_pt = MagicMock()
    mock_pt.Output.DICT = "dict"
    sys.modules["pytesseract"] = mock_pt
    yield mock_pt
    del sys.modules["pytesseract"]


class TestReadText:
    def test_basic_text_extraction(self, mock_pytesseract, solid_white):
        """Single filter, high confidence text is extracted correctly."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Hello", "World"], [90, 85]
        )
        ti = ToolInput(
            tool="read_text",
            threshold=0.5,
            params={"color_filters": [[[0, 0, 0], [180, 255, 80]]]},
        )
        result = run_tool(solid_white, ti)
        assert result.tool == "read_text"
        assert result.details["text"] == "Hello World"
        assert result.details["word_count"] == 2
        assert result.score > 0.5
        assert result.match is True

    def test_no_text_detected(self, mock_pytesseract, solid_black):
        """Returns empty text and zero score when nothing is recognized."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["", ""], [-1, -1]
        )
        ti = ToolInput(
            tool="read_text",
            threshold=0.5,
            params={"color_filters": [[[0, 0, 0], [180, 255, 80]]]},
        )
        result = run_tool(solid_black, ti)
        assert result.score == 0.0
        assert result.match is False
        assert result.details["text"] == ""

    def test_min_confidence_filtering(self, mock_pytesseract, solid_white):
        """Words below min_confidence are filtered out."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Good", "Bad"], [80, 30]
        )
        ti = ToolInput(
            tool="read_text",
            threshold=0.0,
            params={
                "color_filters": [[[0, 0, 0], [180, 255, 80]]],
                "min_confidence": 50,
            },
        )
        result = run_tool(solid_white, ti)
        assert result.details["text"] == "Good"
        assert result.details["word_count"] == 1

    def test_best_voting_picks_highest_confidence(self, mock_pytesseract, solid_white):
        """With voting='best', the filter with highest confidence wins."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_tesseract_data(["Low"], [55])
            return _mock_tesseract_data(["High"], [95])

        mock_pytesseract.image_to_data.side_effect = side_effect
        ti = ToolInput(
            tool="read_text",
            threshold=0.0,
            params={
                "color_filters": [
                    [[0, 0, 0], [180, 255, 80]],
                    [[0, 0, 180], [180, 30, 255]],
                ],
                "voting": "best",
            },
        )
        result = run_tool(solid_white, ti)
        assert result.details["text"] == "High"
        assert result.details["confidence"] == 95.0

    def test_majority_voting(self, mock_pytesseract, solid_white):
        """With voting='majority', the most common text wins."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return _mock_tesseract_data(["Outlier"], [90])
            return _mock_tesseract_data(["Pokemon"], [80])

        mock_pytesseract.image_to_data.side_effect = side_effect
        ti = ToolInput(
            tool="read_text",
            threshold=0.0,
            params={
                "color_filters": [
                    [[0, 0, 0], [180, 255, 80]],
                    [[0, 0, 180], [180, 30, 255]],
                    [[0, 100, 100], [10, 255, 255]],
                ],
                "voting": "majority",
            },
        )
        result = run_tool(solid_white, ti)
        assert result.details["text"] == "Pokemon"

    def test_default_filters_used(self, mock_pytesseract, solid_white):
        """When no color_filters provided, 4 default filters are used."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Test"], [75]
        )
        ti = ToolInput(tool="read_text", threshold=0.0)
        result = run_tool(solid_white, ti)
        # Default has 4 filters, so image_to_data called 4 times
        assert mock_pytesseract.image_to_data.call_count == 4
        assert result.details["filters_tried"] == 4

    def test_region_support(self, mock_pytesseract, solid_white):
        """Region parameter crops the image before OCR."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Region"], [85]
        )
        ti = ToolInput(
            tool="read_text",
            threshold=0.0,
            region=Region(x=0.1, y=0.1, w=0.5, h=0.5),
            params={"color_filters": [[[0, 0, 0], [180, 255, 80]]]},
        )
        result = run_tool(solid_white, ti)
        assert result.details["text"] == "Region"

    def test_details_structure(self, mock_pytesseract, solid_white):
        """Result details contain all expected fields."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["Word"], [88], lefts=[5], tops=[3], widths=[40], heights=[15]
        )
        ti = ToolInput(
            tool="read_text",
            threshold=0.0,
            params={"color_filters": [[[0, 0, 0], [180, 255, 80]]]},
        )
        result = run_tool(solid_white, ti)
        assert "text" in result.details
        assert "confidence" in result.details
        assert "word_count" in result.details
        assert "words" in result.details
        assert "filters_tried" in result.details
        assert "filter_results" in result.details
        # Check word bbox structure
        assert len(result.details["words"]) == 1
        word = result.details["words"][0]
        assert "text" in word
        assert "confidence" in word
        assert "bbox" in word
        bbox = word["bbox"]
        assert "x" in bbox and "y" in bbox and "w" in bbox and "h" in bbox
