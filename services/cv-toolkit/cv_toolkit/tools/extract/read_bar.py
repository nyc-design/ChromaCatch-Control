"""Progress/HP bar fill ratio reader - PA's HP bar reading technique.

Finds bar by color, counts matching pixels, computes fill ratio via
column/row projection to determine how full the bar is.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv_toolkit._utils import bgr_to_hsv, extract_region
from cv_toolkit.models import ToolInput, ToolResult
from cv_toolkit.registry import register_tool


@register_tool("read_bar")
def read_bar(
    image: np.ndarray, reference: np.ndarray | None, tool_input: ToolInput
) -> ToolResult:
    """Read the fill ratio of a progress/HP bar by color.

    Projects bar-colored pixels onto the bar axis (horizontal or vertical)
    to find the extent of the filled portion.

    Params (required):
        bar_hsv_lower (list[int,int,int]): Lower HSV bound for filled bar color.
        bar_hsv_upper (list[int,int,int]): Upper HSV bound for filled bar color.

    Params (optional):
        bg_hsv_lower (list[int,int,int]): Lower HSV bound for empty/background.
        bg_hsv_upper (list[int,int,int]): Upper HSV bound for empty/background.
            If provided, ratio = filled / (filled + empty).
            If omitted, ratio = filled / total.
        orientation (str): "horizontal" or "vertical". Default "horizontal".
    """
    if "bar_hsv_lower" not in tool_input.params or "bar_hsv_upper" not in tool_input.params:
        raise ValueError(
            "read_bar requires 'bar_hsv_lower' and 'bar_hsv_upper' in params"
        )

    bar_hsv_lower = np.array(tool_input.params["bar_hsv_lower"], dtype=np.uint8)
    bar_hsv_upper = np.array(tool_input.params["bar_hsv_upper"], dtype=np.uint8)
    bg_hsv_lower = tool_input.params.get("bg_hsv_lower")
    bg_hsv_upper = tool_input.params.get("bg_hsv_upper")
    orientation = str(tool_input.params.get("orientation", "horizontal"))

    img_crop = extract_region(image, tool_input.region)
    hsv = bgr_to_hsv(img_crop)
    img_h, img_w = img_crop.shape[:2]

    # Create mask for bar (filled) color
    bar_mask = cv2.inRange(hsv, bar_hsv_lower, bar_hsv_upper)
    bar_pixels = int(np.count_nonzero(bar_mask))

    # Create mask for background (empty) color if provided
    has_bg = bg_hsv_lower is not None and bg_hsv_upper is not None
    if has_bg:
        bg_lower = np.array(bg_hsv_lower, dtype=np.uint8)
        bg_upper = np.array(bg_hsv_upper, dtype=np.uint8)
        bg_mask = cv2.inRange(hsv, bg_lower, bg_upper)
        bg_pixels = int(np.count_nonzero(bg_mask))
    else:
        bg_pixels = 0

    total_pixels = img_h * img_w

    # Compute fill ratio using projection
    if orientation == "horizontal":
        fill_ratio, filled_extent, total_extent = _horizontal_fill(
            bar_mask, bg_mask if has_bg else None, img_h, img_w
        )
    elif orientation == "vertical":
        fill_ratio, filled_extent, total_extent = _vertical_fill(
            bar_mask, bg_mask if has_bg else None, img_h, img_w
        )
    else:
        raise ValueError(f"orientation must be 'horizontal' or 'vertical', got {orientation!r}")

    score = fill_ratio

    return ToolResult(
        tool="read_bar",
        score=score,
        match=score >= tool_input.threshold,
        threshold=tool_input.threshold,
        details={
            "fill_ratio": fill_ratio,
            "bar_pixels": bar_pixels,
            "total_pixels": total_pixels,
            "filled_extent_px": filled_extent,
            "total_extent_px": total_extent,
            "orientation": orientation,
        },
    )


def _horizontal_fill(
    bar_mask: np.ndarray,
    bg_mask: np.ndarray | None,
    img_h: int,
    img_w: int,
) -> tuple[float, int, int]:
    """Compute horizontal fill ratio via column projection.

    Projects bar mask onto x-axis (column sums). A column is considered
    "filled" if > 50% of its height has bar pixels. The fill ratio is
    the rightmost filled column / total columns.

    If bg_mask is provided, total_columns = filled_columns + empty_columns.
    """
    if img_w == 0 or img_h == 0:
        return 0.0, 0, 0

    # Column sums: how many bar pixels per column
    col_sums = bar_mask.astype(np.float32).sum(axis=0) / 255.0
    threshold = img_h * 0.5  # > 50% of column height

    filled_columns = col_sums > threshold

    if bg_mask is not None:
        bg_col_sums = bg_mask.astype(np.float32).sum(axis=0) / 255.0
        empty_columns = bg_col_sums > threshold
        total_cols = int(np.count_nonzero(filled_columns) + np.count_nonzero(empty_columns))
    else:
        total_cols = img_w

    if total_cols == 0:
        return 0.0, 0, 0

    # Find rightmost filled column
    filled_indices = np.where(filled_columns)[0]
    if len(filled_indices) == 0:
        return 0.0, 0, total_cols

    rightmost = int(filled_indices[-1]) + 1

    if bg_mask is not None:
        # Find the full extent of bar + background
        all_indices = np.where(filled_columns | empty_columns)[0]
        if len(all_indices) == 0:
            return 0.0, 0, total_cols
        leftmost = int(all_indices[0])
        extent = int(all_indices[-1]) - leftmost + 1
        filled_extent = rightmost - leftmost
        fill_ratio = filled_extent / extent if extent > 0 else 0.0
    else:
        filled_extent = rightmost
        fill_ratio = filled_extent / total_cols

    return max(0.0, min(1.0, fill_ratio)), filled_extent, total_cols


def _vertical_fill(
    bar_mask: np.ndarray,
    bg_mask: np.ndarray | None,
    img_h: int,
    img_w: int,
) -> tuple[float, int, int]:
    """Compute vertical fill ratio via row projection (bottom-to-top).

    Projects bar mask onto y-axis (row sums). A row is considered "filled"
    if > 50% of its width has bar pixels. Fill is measured from bottom up.
    """
    if img_w == 0 or img_h == 0:
        return 0.0, 0, 0

    # Row sums: how many bar pixels per row
    row_sums = bar_mask.astype(np.float32).sum(axis=1) / 255.0
    threshold = img_w * 0.5  # > 50% of row width

    filled_rows = row_sums > threshold

    if bg_mask is not None:
        bg_row_sums = bg_mask.astype(np.float32).sum(axis=1) / 255.0
        empty_rows = bg_row_sums > threshold
        total_rows = int(np.count_nonzero(filled_rows) + np.count_nonzero(empty_rows))
    else:
        total_rows = img_h

    if total_rows == 0:
        return 0.0, 0, 0

    # Find topmost filled row (bottom-to-top means topmost = smallest index)
    filled_indices = np.where(filled_rows)[0]
    if len(filled_indices) == 0:
        return 0.0, 0, total_rows

    topmost = int(filled_indices[0])

    if bg_mask is not None:
        all_indices = np.where(filled_rows | empty_rows)[0]
        if len(all_indices) == 0:
            return 0.0, 0, total_rows
        bottommost = int(all_indices[-1])
        extent = bottommost - int(all_indices[0]) + 1
        filled_extent = bottommost - topmost + 1
        fill_ratio = filled_extent / extent if extent > 0 else 0.0
    else:
        # Bottom-to-top: filled from topmost to bottom
        filled_extent = img_h - topmost
        fill_ratio = filled_extent / total_rows

    return max(0.0, min(1.0, fill_ratio)), filled_extent, total_rows
