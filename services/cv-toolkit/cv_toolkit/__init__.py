"""ChromaCatch-Go CV Toolkit - pure computer vision analysis functions."""

from cv_toolkit.models import (  # noqa: F401
    CompositeInput,
    CompositeStep,
    LocateMatch,
    Region,
    ToolInput,
    ToolResult,
)
from cv_toolkit.registry import TOOL_REGISTRY, run_tool  # noqa: F401

# Import all tool modules to trigger @register_tool decorators
import cv_toolkit.tools  # noqa: F401

__all__ = [
    "run_tool",
    "TOOL_REGISTRY",
    "ToolInput",
    "ToolResult",
    "Region",
    "CompositeStep",
    "CompositeInput",
    "LocateMatch",
]
