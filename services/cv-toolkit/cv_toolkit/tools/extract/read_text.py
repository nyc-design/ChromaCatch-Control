"""Multi-filter OCR with voting - PA's multifiltered_OCR technique.

Runs N color filters, OCRs each result, and votes on the best text.
"""

from __future__ import annotations

from collections import Counter

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region, normalize_bbox
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool

# Default color filter presets (HSV ranges)
_DEFAULT_FILTERS: list[list[list[int]]] = [
    [[0, 0, 0], [180, 255, 80]],        # dark text
    [[0, 0, 180], [180, 30, 255]],       # light text
    [[0, 100, 100], [10, 255, 255]],     # red text
    [[100, 100, 100], [130, 255, 255]],  # blue text
]


def _ocr_with_filter(
    image: np.ndarray,
    hsv_lower: np.ndarray,
    hsv_upper: np.ndarray,
    psm: int,
    min_confidence: int,
) -> dict:
    """Apply a single color filter and OCR the result.

    Returns dict with text, confidence, words list.
    """
    import pytesseract  # Lazy import — system dependency may not be available

    hsv = bgr_to_hsv(image)
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)

    # Morphological cleanup
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # Invert if needed (Tesseract prefers dark text on light background)
    if np.mean(mask) > 127:
        mask = cv2.bitwise_not(mask)

    config = f"--psm {psm}"
    data = pytesseract.image_to_data(
        mask, config=config, output_type=pytesseract.Output.DICT
    )

    img_h, img_w = image.shape[:2]
    words: list[dict] = []
    texts: list[str] = []
    confidences: list[int] = []

    for i, conf in enumerate(data["conf"]):
        conf_val = int(conf)
        word = data["text"][i].strip()
        if conf_val >= min_confidence and word:
            texts.append(word)
            confidences.append(conf_val)
            words.append({
                "text": word,
                "confidence": conf_val,
                "bbox": normalize_bbox(
                    int(data["left"][i]),
                    int(data["top"][i]),
                    int(data["width"][i]),
                    int(data["height"][i]),
                    img_h,
                    img_w,
                ),
            })

    text = " ".join(texts)
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "text": text,
        "confidence": mean_conf,
        "words": words,
        "word_count": len(texts),
    }


@register_tool("read_text")
def read_text(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Multi-filter OCR with voting.

    Runs multiple color filters, OCRs each, and picks the best result
    via confidence-based or majority voting.

    Params (optional):
        color_filters (list): List of [hsv_lower, hsv_upper] pairs.
            Default: 4 standard presets (dark, light, red, blue text).
        psm (int): Tesseract page segmentation mode. Default 6.
        min_confidence (int): Min word confidence. Default 50.
        voting (str): "best" (highest confidence) or "majority". Default "best".
    """
    color_filters_raw = tool_input.params.get("color_filters", _DEFAULT_FILTERS)
    psm = int(tool_input.params.get("psm", 6))
    min_confidence = int(tool_input.params.get("min_confidence", 50))
    voting = str(tool_input.params.get("voting", "best"))

    img_crop = extract_region(image, tool_input.region)

    # Run OCR with each color filter
    filter_results: list[dict] = []
    for filt in color_filters_raw:
        hsv_lower = np.array(filt[0], dtype=np.uint8)
        hsv_upper = np.array(filt[1], dtype=np.uint8)
        result = _ocr_with_filter(img_crop, hsv_lower, hsv_upper, psm, min_confidence)
        filter_results.append(result)

    # Pick the winner
    if voting == "majority":
        # Pick the most common non-empty text across filters
        text_counts: Counter[str] = Counter()
        for fr in filter_results:
            if fr["text"]:
                text_counts[fr["text"]] += 1

        if text_counts:
            winner_text = text_counts.most_common(1)[0][0]
            # Use the result with highest confidence for this text
            best = max(
                (fr for fr in filter_results if fr["text"] == winner_text),
                key=lambda fr: fr["confidence"],
            )
        else:
            best = {"text": "", "confidence": 0.0, "words": [], "word_count": 0}
    else:
        # "best" — pick filter result with highest mean confidence
        non_empty = [fr for fr in filter_results if fr["text"]]
        if non_empty:
            best = max(non_empty, key=lambda fr: fr["confidence"])
        else:
            best = {"text": "", "confidence": 0.0, "words": [], "word_count": 0}

    score = best["confidence"] / 100.0

    return ToolResult(
        tool="read_text",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "text": best["text"],
            "confidence": best["confidence"],
            "word_count": best["word_count"],
            "words": best["words"],
            "filters_tried": len(filter_results),
            "filter_results": [
                {"text": fr["text"], "confidence": fr["confidence"]}
                for fr in filter_results
            ],
        },
    )
