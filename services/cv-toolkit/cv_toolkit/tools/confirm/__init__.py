"""Import all confirm tool modules to trigger @register_tool decorators."""

from cv_toolkit.tools.confirm import (  # noqa: F401
    brightness_check,
    color_distance,
    color_presence,
    composite,
    contour_detect,
    dominant_colors,
    edge_density,
    exact_match,
    grid_similarity,
    grid_structure,
    histogram_similarity,
    motion_detect,
    object_detect,
    ocr_read,
    ssim_compare,
)
