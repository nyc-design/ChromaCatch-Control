"""OCR read tool - color-filtered text extraction via pytesseract."""

from __future__ import annotations

from difflib import SequenceMatcher

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("ocr_read")
def ocr_read(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Color-filtered OCR on a region.

    Pre-processes by isolating text-colored pixels, then runs Tesseract.
    If params["expected"] is set, score = string similarity to expected text.
    Otherwise score = OCR confidence / 100.

    Params:
        text_hsv_lower (list[int,int,int]): Lower HSV bound for text color. Default [0,0,0].
        text_hsv_upper (list[int,int,int]): Upper HSV bound for text color. Default [180,255,80].
        expected (str): Expected text for similarity scoring.
        psm (int): Tesseract page segmentation mode. Default 7 (single line).
    """
    import pytesseract  # Lazy import — system dependency may not be available

    hsv_lower = np.array(
        tool_input.params.get("text_hsv_lower", [0, 0, 0]), dtype=np.uint8
    )
    hsv_upper = np.array(
        tool_input.params.get("text_hsv_upper", [180, 255, 80]), dtype=np.uint8
    )
    expected = tool_input.params.get("expected")
    psm = int(tool_input.params.get("psm", 7))

    img_crop = extract_region(image, tool_input.region)
    hsv = bgr_to_hsv(img_crop)

    # Create mask for text-colored pixels, invert for white-on-black
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)

    # Clean up with morphological operations
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # Invert if needed (Tesseract prefers dark text on light background)
    if np.mean(mask) > 127:
        mask = cv2.bitwise_not(mask)

    config = f"--psm {psm}"
    data = pytesseract.image_to_data(mask, config=config, output_type=pytesseract.Output.DICT)

    # Extract text and confidence
    texts = []
    confidences = []
    for i, conf in enumerate(data["conf"]):
        conf_val = int(conf)
        if conf_val > 0 and data["text"][i].strip():
            texts.append(data["text"][i].strip())
            confidences.append(conf_val)

    ocr_text = " ".join(texts)
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    details: dict = {
        "text": ocr_text,
        "mean_confidence": mean_confidence,
        "word_count": len(texts),
    }

    if expected is not None:
        similarity = SequenceMatcher(None, ocr_text.lower(), expected.lower()).ratio()
        score = similarity
        details["expected"] = expected
        details["similarity"] = similarity
    else:
        score = mean_confidence / 100.0

    return ToolResult(
        tool="ocr_read",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details=details,
    )
