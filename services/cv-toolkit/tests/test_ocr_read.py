"""Tests for ocr_read tool (mocked pytesseract)."""

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from cv_toolkit.models import ToolInput
from cv_toolkit.registry import run_tool


def _mock_tesseract_output(texts, confidences):
    """Create a mock pytesseract output dict."""
    return {
        "text": texts,
        "conf": confidences,
        "level": [5] * len(texts),
        "page_num": [1] * len(texts),
        "block_num": [1] * len(texts),
        "par_num": [1] * len(texts),
        "line_num": [1] * len(texts),
        "word_num": list(range(1, len(texts) + 1)),
        "left": [0] * len(texts),
        "top": [0] * len(texts),
        "width": [50] * len(texts),
        "height": [20] * len(texts),
    }


@pytest.fixture(autouse=True)
def mock_pytesseract():
    """Mock pytesseract module before it gets lazy-imported."""
    mock_pt = MagicMock()
    mock_pt.Output.DICT = "dict"
    sys.modules["pytesseract"] = mock_pt
    yield mock_pt
    del sys.modules["pytesseract"]


class TestOcrRead:
    def test_expected_text_match(self, mock_pytesseract, solid_white):
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_output(
            ["Hello", "World"], [90, 85]
        )
        ti = ToolInput(
            tool="ocr_read",
            threshold=0.5,
            params={"expected": "Hello World"},
        )
        result = run_tool(solid_white, ti)
        assert result.score > 0.5
        assert result.details["text"] == "Hello World"

    def test_expected_text_no_match(self, mock_pytesseract, solid_white):
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_output(
            ["Goodbye"], [90]
        )
        ti = ToolInput(
            tool="ocr_read",
            threshold=0.8,
            params={"expected": "Hello World"},
        )
        result = run_tool(solid_white, ti)
        assert result.score < 0.8
        assert result.match is False

    def test_no_expected_uses_confidence(self, mock_pytesseract, solid_white):
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_output(
            ["Pokemon"], [92]
        )
        ti = ToolInput(tool="ocr_read", threshold=0.5)
        result = run_tool(solid_white, ti)
        assert result.score > 0.9
        assert result.details["text"] == "Pokemon"

    def test_no_text_detected(self, mock_pytesseract, solid_black):
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_output(
            ["", ""], [-1, -1]
        )
        ti = ToolInput(tool="ocr_read", threshold=0.5)
        result = run_tool(solid_black, ti)
        assert result.score == 0.0
        assert result.match is False

    def test_details_structure(self, mock_pytesseract, solid_white):
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_output(
            ["Test"], [80]
        )
        ti = ToolInput(tool="ocr_read", threshold=0.0)
        result = run_tool(solid_white, ti)
        assert "text" in result.details
        assert "mean_confidence" in result.details
        assert "word_count" in result.details
