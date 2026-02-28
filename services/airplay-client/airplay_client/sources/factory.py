"""Factory for selecting the configured frame source."""

from __future__ import annotations

from airplay_client.config import client_settings as settings
from airplay_client.sources.airplay_source import AirPlayFrameSource
from airplay_client.sources.base import FrameSource
from airplay_client.sources.capture_card_source import CaptureCardFrameSource
from airplay_client.sources.screen_source import ScreenFrameSource


def create_frame_source() -> FrameSource:
    source = settings.capture_source.lower().strip()
    if source == "airplay":
        return AirPlayFrameSource()
    if source == "capture":
        return CaptureCardFrameSource()
    if source == "screen":
        return ScreenFrameSource()
    raise ValueError(
        f"Unsupported CC_CLIENT_CAPTURE_SOURCE='{settings.capture_source}'. "
        "Use one of: airplay, capture, screen."
    )

