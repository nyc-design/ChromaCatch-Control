"""Waterfill-isolated digit reading - PA's read_number_waterfill technique.

Color filter -> waterfill -> isolate individual digits -> OCR each in
single-char mode -> character substitution -> reassemble number.
"""

from __future__ import annotations

from collections import Counter

import cv2
import numpy as np

from cv_toolkit._utils import extract_region, waterfill_candidates
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool

# Default OCR character substitutions for common misreads
_DEFAULT_SUBSTITUTIONS: dict[str, str] = {
    "O": "0",
    "o": "0",
    "U": "0",
    "S": "5",
    "s": "5",
    "l": "1",
    "I": "1",
    "|": "1",
    "B": "8",
    "Z": "2",
    "G": "6",
    "q": "9",
}


def _ocr_single_char(
    image: np.ndarray,
    bbox_px: list[int],
    psm: int,
    substitutions: dict[str, str],
) -> tuple[str, float]:
    """Extract a single digit from a bounding box region.

    Crops the candidate, pads with white border, OCRs in single-char mode,
    and applies substitution table.

    Returns (character, confidence).
    """
    import pytesseract  # Lazy import — system dependency may not be available

    x, y, w, h = bbox_px
    crop = image[y : y + h, x : x + w]

    # Pad with 10px white border for better OCR
    padded = cv2.copyMakeBorder(
        crop, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )

    config = f"--psm {psm}"
    data = pytesseract.image_to_data(
        padded, config=config, output_type=pytesseract.Output.DICT
    )

    # Find the best recognized character
    best_char = ""
    best_conf = 0.0
    for i, conf in enumerate(data["conf"]):
        conf_val = int(conf)
        text = data["text"][i].strip()
        if conf_val > 0 and text:
            if conf_val > best_conf:
                best_char = text
                best_conf = float(conf_val)

    # Apply substitution table
    if best_char in substitutions:
        best_char = substitutions[best_char]

    return best_char, best_conf


def _read_digits_with_filter(
    image: np.ndarray,
    color_filter: tuple[np.ndarray, np.ndarray],
    psm: int,
    substitutions: dict[str, str],
    allow_decimal: bool,
    allow_negative: bool,
    min_area_ratio: float,
    max_area_ratio: float,
    min_aspect_ratio: float,
    max_aspect_ratio: float,
) -> dict:
    """Run waterfill with one color filter and read digits.

    Returns dict with value, raw_text, confidence, digit_count.
    """
    candidates = waterfill_candidates(
        image,
        [color_filter],
        min_area_ratio=min_area_ratio,
        max_area_ratio=max_area_ratio,
        min_aspect=min_aspect_ratio,
        max_aspect=max_aspect_ratio,
    )

    if not candidates:
        return {
            "value": None,
            "raw_text": "",
            "confidence": 0.0,
            "digit_count": 0,
        }

    # Sort candidates left-to-right by x coordinate
    candidates.sort(key=lambda c: c["bbox_px"][0])

    chars: list[str] = []
    confidences: list[float] = []

    for candidate in candidates:
        char, conf = _ocr_single_char(
            image, candidate["bbox_px"], psm, substitutions
        )
        if char:
            chars.append(char)
            confidences.append(conf)

    raw_text = "".join(chars)

    # Parse to number
    value = _parse_number(raw_text, allow_decimal, allow_negative)
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # Score = fraction of characters that are valid digits
    valid_chars = set("0123456789")
    if allow_decimal:
        valid_chars.add(".")
    if allow_negative:
        valid_chars.add("-")
    digit_count = sum(1 for c in raw_text if c in valid_chars)
    score = digit_count / len(raw_text) if raw_text else 0.0

    return {
        "value": value,
        "raw_text": raw_text,
        "confidence": mean_conf,
        "digit_count": digit_count,
    }


def _parse_number(
    text: str, allow_decimal: bool, allow_negative: bool
) -> int | float | None:
    """Parse a string of digit characters into a number.

    Strips non-digit characters (except . and - when allowed).
    Returns int, float, or None if parsing fails.
    """
    valid_chars = set("0123456789")
    if allow_decimal:
        valid_chars.add(".")
    if allow_negative:
        valid_chars.add("-")

    cleaned = "".join(c for c in text if c in valid_chars)
    if not cleaned or cleaned in (".", "-", "-."):
        return None

    try:
        if allow_decimal and "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return None


@register_tool("read_number")
def read_number(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Waterfill-isolated digit reading.

    Color-filters the image, finds digit-shaped blobs via waterfill,
    OCRs each in single-character mode, applies substitution corrections,
    and reassembles the number.

    Params (required):
        color_filters (list): List of [hsv_lower, hsv_upper] pairs.

    Params (optional):
        allow_decimal (bool): Allow decimal point. Default False.
        allow_negative (bool): Allow leading minus. Default False.
        substitutions (dict): OCR error corrections. Default: common digit misreads.
        psm (int): Tesseract PSM for single char. Default 10.
        voting (bool): Try multiple filters and majority-vote. Default True.
        min_area_ratio (float): Min digit area as fraction of region. Default 0.001.
        max_area_ratio (float): Max digit area as fraction of region. Default 0.15.
        min_aspect_ratio (float): Min width/height ratio. Default 0.2.
        max_aspect_ratio (float): Max width/height ratio. Default 3.0.
    """
    if "color_filters" not in tool_input.params:
        raise ValueError("read_number requires 'color_filters' in params")

    color_filters_raw = tool_input.params["color_filters"]
    allow_decimal = bool(tool_input.params.get("allow_decimal", False))
    allow_negative = bool(tool_input.params.get("allow_negative", False))
    substitutions = dict(
        tool_input.params.get("substitutions", _DEFAULT_SUBSTITUTIONS)
    )
    psm = int(tool_input.params.get("psm", 10))
    voting = bool(tool_input.params.get("voting", True))
    min_area_ratio = float(tool_input.params.get("min_area_ratio", 0.001))
    max_area_ratio = float(tool_input.params.get("max_area_ratio", 0.15))
    min_aspect_ratio = float(tool_input.params.get("min_aspect_ratio", 0.2))
    max_aspect_ratio = float(tool_input.params.get("max_aspect_ratio", 3.0))

    img_crop = extract_region(image, tool_input.region)

    # Run digit reading with each color filter
    filter_results: list[dict] = []
    for filt in color_filters_raw:
        hsv_lower = np.array(filt[0], dtype=np.uint8)
        hsv_upper = np.array(filt[1], dtype=np.uint8)
        result = _read_digits_with_filter(
            img_crop,
            (hsv_lower, hsv_upper),
            psm,
            substitutions,
            allow_decimal,
            allow_negative,
            min_area_ratio,
            max_area_ratio,
            min_aspect_ratio,
            max_aspect_ratio,
        )
        filter_results.append(result)

    # Pick winner
    if voting and len(filter_results) > 1:
        # Majority vote — weight numeric-only results 2x
        value_counts: Counter = Counter()
        for fr in filter_results:
            if fr["value"] is not None:
                weight = 2
                value_counts[fr["value"]] += weight
            elif fr["raw_text"]:
                value_counts[fr["raw_text"]] += 1

        if value_counts:
            winner_value = value_counts.most_common(1)[0][0]
            # Find the filter result that matches the winner
            best = None
            for fr in filter_results:
                if fr["value"] == winner_value or fr["raw_text"] == winner_value:
                    if best is None or fr["confidence"] > best["confidence"]:
                        best = fr
            if best is None:
                best = filter_results[0]
        else:
            best = max(filter_results, key=lambda fr: fr["confidence"])
    else:
        # Single filter or voting disabled — pick highest confidence
        non_empty = [fr for fr in filter_results if fr["value"] is not None]
        if non_empty:
            best = max(non_empty, key=lambda fr: fr["confidence"])
        else:
            best = max(filter_results, key=lambda fr: fr["confidence"])

    # Score = fraction of digits successfully parsed
    raw = best["raw_text"]
    if raw and best["value"] is not None:
        score = best["digit_count"] / len(raw) if raw else 0.0
    else:
        score = 0.0

    return ToolResult(
        tool="read_number",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "value": best["value"],
            "raw_text": best["raw_text"],
            "confidence": best["confidence"],
            "digit_count": best["digit_count"],
            "filter_results": [
                {
                    "value": fr["value"],
                    "raw_text": fr["raw_text"],
                    "confidence": fr["confidence"],
                }
                for fr in filter_results
            ],
        },
    )
