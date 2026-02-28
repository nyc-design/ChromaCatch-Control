"""Tests for frame source factory."""

import pytest

from airplay_client.capture.frame_capture import CaptureBackend, FrameCapture
from airplay_client.config import client_settings
from airplay_client.sources.airplay_source import AirPlayFrameSource
from airplay_client.sources.capture_card_source import CaptureCardFrameSource
from airplay_client.sources.factory import create_frame_source
from airplay_client.sources.screen_source import ScreenFrameSource


@pytest.fixture(autouse=True)
def restore_capture_source():
    original = client_settings.capture_source
    try:
        yield
    finally:
        client_settings.capture_source = original


def test_factory_airplay():
    client_settings.capture_source = "airplay"
    original_detect = FrameCapture._detect_backend
    try:
        FrameCapture._detect_backend = staticmethod(lambda: CaptureBackend.GSTREAMER)
        source = create_frame_source()
    finally:
        FrameCapture._detect_backend = original_detect
    assert isinstance(source, AirPlayFrameSource)


def test_factory_capture():
    client_settings.capture_source = "capture"
    source = create_frame_source()
    assert isinstance(source, CaptureCardFrameSource)


def test_factory_screen():
    client_settings.capture_source = "screen"
    source = create_frame_source()
    assert isinstance(source, ScreenFrameSource)


def test_factory_invalid_source():
    client_settings.capture_source = "invalid"
    with pytest.raises(ValueError, match="Unsupported CC_CLIENT_CAPTURE_SOURCE"):
        create_frame_source()
