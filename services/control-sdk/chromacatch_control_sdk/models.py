"""Pydantic models for ChromaCatch Control SDK."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConnectedClients(BaseModel):
    connected_clients: list[str] = Field(default_factory=list)
    total_clients: int = 0


class CommandResult(BaseModel):
    status: str
    action: str | None = None
    client_id: str | None = None
    sent_to: int | None = None
    command_id: str | None = None
    command_sequence: int | None = None


class TemplateMatchResult(BaseModel):
    x: int
    y: int
    width: int
    height: int
    score: float


class TemplateMatchResponse(BaseModel):
    client_id: str
    frame_width: int
    frame_height: int
    match_count: int
    matches: list[TemplateMatchResult] = Field(default_factory=list)
