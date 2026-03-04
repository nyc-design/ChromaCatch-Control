"""Locate text tool - find text on screen via OCR with bounding boxes.

Uses pytesseract (lazy-imported) to detect text in the image and returns
bounding boxes for matching words or word groups. Runs OCR on the
color-filtered grayscale image (not binary mask) for better edge quality.
Small images are upscaled for better Tesseract accuracy.
"""

from __future__ import annotations

from difflib import SequenceMatcher

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region, normalize_bbox
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool

_MIN_OCR_HEIGHT = 100  # Upscale images shorter than this for better OCR


@register_tool("locate_text")
def locate_text(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Locate text matching a target string, returning bounding boxes.

    Pre-processes by isolating text-colored pixels, runs Tesseract OCR on
    the filtered grayscale image, then searches for the target string.

    Params (required):
        target (str): Text string to find.

    Params (optional):
        text_hsv_lower (list[int,int,int]): Lower HSV bound for text color. Default [0,0,0].
        text_hsv_upper (list[int,int,int]): Upper HSV bound for text color. Default [180,255,80].
        psm (int): Tesseract page segmentation mode. Default 6.
        min_confidence (int): Minimum OCR confidence to consider. Default 60.
        fuzzy (bool): Use fuzzy matching (SequenceMatcher). Default False.
        fuzzy_threshold (float): Fuzzy match similarity threshold. Default 0.6.
    """
    import pytesseract  # Lazy import — system dependency may not be available

    if "target" not in tool_input.params:
        raise ValueError("locate_text requires 'target' in params")

    target = str(tool_input.params["target"])
    hsv_lower = np.array(
        tool_input.params.get("text_hsv_lower", [0, 0, 0]), dtype=np.uint8
    )
    hsv_upper = np.array(
        tool_input.params.get("text_hsv_upper", [180, 255, 80]), dtype=np.uint8
    )
    psm = int(tool_input.params.get("psm", 6))
    min_confidence = int(tool_input.params.get("min_confidence", 60))
    fuzzy = bool(tool_input.params.get("fuzzy", False))
    fuzzy_threshold = float(tool_input.params.get("fuzzy_threshold", 0.6))

    img_crop = extract_region(image, tool_input.region)
    img_h, img_w = img_crop.shape[:2]

    # Upscale small images for better OCR (Tesseract needs ~30px char height)
    upscale = 1
    if img_h < _MIN_OCR_HEIGHT:
        upscale = max(2, _MIN_OCR_HEIGHT // img_h + 1)
        img_crop = cv2.resize(
            img_crop,
            (img_w * upscale, img_h * upscale),
            interpolation=cv2.INTER_CUBIC,
        )

    # Color filtering
    hsv = bgr_to_hsv(img_crop)
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)

    # Apply mask to get color-filtered grayscale (preserves edge quality)
    gray = cv2.cvtColor(img_crop, cv2.COLOR_BGR2GRAY)
    filtered = cv2.bitwise_and(gray, gray, mask=mask)

    # Morphological cleanup
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    filtered = cv2.morphologyEx(filtered, cv2.MORPH_CLOSE, kernel)

    # Invert if needed (Tesseract prefers dark text on light background)
    if np.mean(filtered[mask > 0]) > 127 if np.any(mask > 0) else False:
        filtered = cv2.bitwise_not(filtered)

    config = f"--psm {psm}"
    data = pytesseract.image_to_data(
        filtered, config=config, output_type=pytesseract.Output.DICT
    )

    # Collect detected words with bounding boxes
    words: list[dict] = []
    for i, conf in enumerate(data["conf"]):
        conf_val = int(conf)
        text = data["text"][i].strip()
        if conf_val >= min_confidence and text:
            # Scale bbox back to original coordinates if upscaled
            words.append({
                "text": text,
                "confidence": conf_val,
                "left": int(data["left"][i]) // upscale,
                "top": int(data["top"][i]) // upscale,
                "width": int(data["width"][i]) // upscale,
                "height": int(data["height"][i]) // upscale,
            })

    # Search for target among detected words
    target_lower = target.lower()
    target_words = target_lower.split()
    matches: list[dict] = []

    if len(target_words) == 1:
        # Single word — match individual words
        for word in words:
            if _is_match(word["text"], target_lower, fuzzy, fuzzy_threshold):
                bbox = normalize_bbox(
                    word["left"], word["top"], word["width"], word["height"],
                    img_h, img_w,
                )
                conf = word["confidence"] / 100.0
                matches.append({"bbox": bbox, "confidence": conf})
    else:
        # Multi-word — scan for consecutive word sequences
        for i in range(len(words) - len(target_words) + 1):
            window = words[i : i + len(target_words)]
            window_texts = [w["text"].lower() for w in window]

            if fuzzy:
                combined = " ".join(window_texts)
                similarity = SequenceMatcher(None, combined, target_lower).ratio()
                is_match = similarity > fuzzy_threshold
            else:
                is_match = window_texts == target_words

            if is_match:
                # Compute bounding box spanning all words in the match
                x_min = min(w["left"] for w in window)
                y_min = min(w["top"] for w in window)
                x_max = max(w["left"] + w["width"] for w in window)
                y_max = max(w["top"] + w["height"] for w in window)
                bbox = normalize_bbox(
                    x_min, y_min, x_max - x_min, y_max - y_min,
                    img_h, img_w,
                )
                avg_conf = sum(w["confidence"] for w in window) / len(window) / 100.0
                matches.append({"bbox": bbox, "confidence": avg_conf})

    best_score = max((m["confidence"] for m in matches), default=0.0)

    return ToolResult(
        tool="locate_text",
        score=best_score,
        match=len(matches) > 0 and best_score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "matches": matches,
            "num_matches": len(matches),
        },
    )


def _is_match(detected: str, target: str, fuzzy: bool, threshold: float = 0.6) -> bool:
    """Check if a detected word matches the target."""
    detected_lower = detected.lower()
    if fuzzy:
        similarity = SequenceMatcher(None, detected_lower, target).ratio()
        return similarity > threshold
    return detected_lower == target
