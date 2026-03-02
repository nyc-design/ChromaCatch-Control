"""Data models for the CV toolkit uniform interface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Region(BaseModel):
    """Normalized region of interest (0.0-1.0 coordinates)."""

    x: float = Field(ge=0.0, le=1.0, description="Left edge, normalized")
    y: float = Field(ge=0.0, le=1.0, description="Top edge, normalized")
    w: float = Field(gt=0.0, le=1.0, description="Width, normalized")
    h: float = Field(gt=0.0, le=1.0, description="Height, normalized")


class ToolInput(BaseModel):
    """Uniform input for every CV tool.

    Images (numpy arrays) are passed as function arguments, not in this model.
    """

    tool: str = Field(description="Registered tool name")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    region: Region | None = Field(
        default=None, description="Crop both images to this ROI before analysis"
    )
    params: dict = Field(
        default_factory=dict, description="Tool-specific overrides"
    )


class ToolResult(BaseModel):
    """Uniform output from every CV tool."""

    tool: str
    score: float = Field(ge=0.0, le=1.0)
    match: bool
    threshold: float
    details: dict = Field(default_factory=dict)


class LocateMatch(BaseModel):
    """A single located element with bounding box and confidence."""

    bbox: Region
    confidence: float = Field(ge=0.0, le=1.0)


class CompositeStep(BaseModel):
    """One step within a composite tool invocation."""

    tool: str
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    region: Region | None = None
    params: dict = Field(default_factory=dict)
    weight: float = Field(default=1.0, gt=0.0)


class CompositeInput(BaseModel):
    """Input for the composite meta-tool."""

    steps: list[CompositeStep]
    aggregate: str = Field(
        default="all", pattern="^(all|any|majority|weighted)$"
    )
