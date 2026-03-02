"""Tool registry and dispatcher."""

from __future__ import annotations

from typing import Callable

import numpy as np

from cv_toolkit.models import ToolInput, ToolResult

ToolFunction = Callable[[np.ndarray, "np.ndarray | None", ToolInput], ToolResult]

TOOL_REGISTRY: dict[str, ToolFunction] = {}


def register_tool(name: str) -> Callable[[ToolFunction], ToolFunction]:
    """Decorator to register a tool function."""

    def decorator(fn: ToolFunction) -> ToolFunction:
        TOOL_REGISTRY[name] = fn
        return fn

    return decorator


def run_tool(
    image: np.ndarray,
    tool_input: ToolInput,
    reference: np.ndarray | None = None,
) -> ToolResult:
    """Dispatch to the registered tool by name.

    Args:
        image: BGR numpy array (H, W, 3)
        tool_input: Validated ToolInput with tool name, threshold, region, params
        reference: Optional BGR numpy array for comparison tools

    Returns:
        ToolResult with score, match bool, and optional details

    Raises:
        KeyError: If tool name is not registered
    """
    fn = TOOL_REGISTRY.get(tool_input.tool)
    if fn is None:
        raise KeyError(
            f"Unknown tool: {tool_input.tool!r}. "
            f"Available: {sorted(TOOL_REGISTRY.keys())}"
        )
    return fn(image, reference, tool_input)
