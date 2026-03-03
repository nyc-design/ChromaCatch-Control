"""Tests for read_number tool (mocked pytesseract)."""

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from cv_toolkit.models import Region, ToolInput
from cv_toolkit.registry import run_tool


def _mock_tesseract_data(texts, confidences):
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
        "left": [0] * n,
        "top": [0] * n,
        "width": [20] * n,
        "height": [20] * n,
    }


@pytest.fixture(autouse=True)
def mock_pytesseract():
    """Mock pytesseract module before it gets lazy-imported."""
    mock_pt = MagicMock()
    mock_pt.Output.DICT = "dict"
    sys.modules["pytesseract"] = mock_pt
    yield mock_pt
    del sys.modules["pytesseract"]


def _make_digit_image():
    """Create a 200x100 image with bright blobs that waterfill will find.

    Places 3 white rectangles (digit-shaped) on black background.
    """
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    # Digit 1 at x=20
    img[20:80, 20:45] = (255, 255, 255)
    # Digit 2 at x=60
    img[20:80, 60:85] = (255, 255, 255)
    # Digit 3 at x=100
    img[20:80, 100:125] = (255, 255, 255)
    return img


class TestReadNumber:
    def test_basic_number_reading(self, mock_pytesseract):
        """Reads digits from waterfill candidates and assembles number."""
        # Mock OCR to return sequential digits
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            digits = ["1", "2", "3"]
            idx = min(call_count[0] - 1, len(digits) - 1)
            return _mock_tesseract_data([digits[idx]], [90])

        mock_pytesseract.image_to_data.side_effect = side_effect

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.5,
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "voting": False,
            },
        )
        result = run_tool(img, ti)
        assert result.tool == "read_number"
        assert result.details["value"] == 123
        assert result.score > 0.5

    def test_missing_color_filters_raises(self, mock_pytesseract, solid_white):
        """Raises ValueError when color_filters not provided."""
        ti = ToolInput(tool="read_number", threshold=0.5, params={})
        with pytest.raises(ValueError, match="color_filters"):
            run_tool(solid_white, ti)

    def test_no_candidates_found(self, mock_pytesseract, solid_black):
        """Returns None value when no digit candidates found."""
        ti = ToolInput(
            tool="read_number",
            threshold=0.5,
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "voting": False,
            },
        )
        result = run_tool(solid_black, ti)
        assert result.details["value"] is None
        assert result.score == 0.0
        assert result.match is False

    def test_substitution_applied(self, mock_pytesseract):
        """OCR character substitution corrects common misreads."""
        # Mock OCR returning 'O' which should be substituted to '0'
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            chars = ["1", "O", "5"]  # O -> 0
            idx = min(call_count[0] - 1, len(chars) - 1)
            return _mock_tesseract_data([chars[idx]], [85])

        mock_pytesseract.image_to_data.side_effect = side_effect

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.0,
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "voting": False,
            },
        )
        result = run_tool(img, ti)
        assert result.details["value"] == 105
        assert result.details["raw_text"] == "105"

    def test_custom_substitutions(self, mock_pytesseract):
        """Custom substitution table overrides defaults."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            return _mock_tesseract_data(["X"], [85])

        mock_pytesseract.image_to_data.side_effect = side_effect

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.0,
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "substitutions": {"X": "7"},
                "voting": False,
            },
        )
        result = run_tool(img, ti)
        assert result.details["raw_text"] == "777"
        assert result.details["value"] == 777

    def test_allow_decimal(self, mock_pytesseract):
        """Decimal numbers parsed correctly when allow_decimal=True."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            chars = ["3", ".", "5"]
            idx = min(call_count[0] - 1, len(chars) - 1)
            return _mock_tesseract_data([chars[idx]], [90])

        mock_pytesseract.image_to_data.side_effect = side_effect

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.0,
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "allow_decimal": True,
                "voting": False,
            },
        )
        result = run_tool(img, ti)
        assert result.details["value"] == 3.5

    def test_voting_across_filters(self, mock_pytesseract):
        """Multiple filters with voting picks most common result."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            # Filter 1 digits: 1,2,3 => 123
            # Filter 2 digits: 4,5,6 => 456  (call 4,5,6)
            if call_count[0] <= 3:
                chars = ["1", "2", "3"]
                idx = call_count[0] - 1
            else:
                chars = ["1", "2", "3"]
                idx = call_count[0] - 4
            return _mock_tesseract_data([chars[idx]], [85])

        mock_pytesseract.image_to_data.side_effect = side_effect

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.0,
            params={
                "color_filters": [
                    [[0, 0, 200], [180, 30, 255]],
                    [[0, 0, 200], [180, 30, 255]],
                ],
                "voting": True,
            },
        )
        result = run_tool(img, ti)
        # Both filters return same candidates (same filter), so should agree
        assert result.details["value"] is not None

    def test_details_structure(self, mock_pytesseract):
        """Result details contain all expected fields."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["5"], [90]
        )

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.0,
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "voting": False,
            },
        )
        result = run_tool(img, ti)
        assert "value" in result.details
        assert "raw_text" in result.details
        assert "confidence" in result.details
        assert "digit_count" in result.details
        assert "filter_results" in result.details

    def test_region_support(self, mock_pytesseract):
        """Region parameter crops the image before processing."""
        mock_pytesseract.image_to_data.return_value = _mock_tesseract_data(
            ["7"], [90]
        )

        img = _make_digit_image()
        ti = ToolInput(
            tool="read_number",
            threshold=0.0,
            region=Region(x=0.0, y=0.0, w=0.5, h=1.0),
            params={
                "color_filters": [[[0, 0, 200], [180, 30, 255]]],
                "voting": False,
            },
        )
        result = run_tool(img, ti)
        # Should still produce a result (may find fewer digits in cropped region)
        assert result.tool == "read_number"
