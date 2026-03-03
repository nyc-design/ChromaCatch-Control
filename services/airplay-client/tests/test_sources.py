"""Tests for frame source factory."""

from unittest.mock import patch

import pytest

from airplay_client.capture.frame_capture import CaptureBackend, FrameCapture
from airplay_client.config import client_settings
from airplay_client.sources.airplay_source import AirPlayFrameSource
from airplay_client.sources.capture_card_source import CaptureCardFrameSource
from airplay_client.sources.factory import create_frame_source
from airplay_client.sources.ntr_source import NTRFrameSource
from airplay_client.sources.screen_source import ScreenFrameSource
from airplay_client.sources.sysdvr_source import SysDVRFrameSource


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


def test_factory_sysdvr():
    client_settings.capture_source = "sysdvr"
    source = create_frame_source()
    assert isinstance(source, SysDVRFrameSource)


def test_factory_ntr():
    client_settings.capture_source = "ntr"
    source = create_frame_source()
    assert isinstance(source, NTRFrameSource)


def test_factory_invalid_source():
    client_settings.capture_source = "invalid"
    with pytest.raises(ValueError, match="Unsupported CC_CLIENT_CAPTURE_SOURCE"):
        create_frame_source()


class TestSysDVRSource:
    def test_source_name(self):
        s = SysDVRFrameSource(url="rtsp://localhost:6666/video")
        assert s.source_name == "sysdvr"

    def test_not_running_initially(self):
        s = SysDVRFrameSource(url="rtsp://localhost:6666/video")
        assert s.is_running is False

    def test_get_frame_returns_none_when_stopped(self):
        s = SysDVRFrameSource(url="rtsp://localhost:6666/video")
        assert s.get_frame(timeout=0.01) is None

    def test_stop_idempotent(self):
        s = SysDVRFrameSource(url="rtsp://localhost:6666/video")
        s.stop()  # Should not raise
        s.stop()  # Should not raise


class TestNTRSource:
    def test_source_name(self):
        s = NTRFrameSource(host="127.0.0.1", port=9000)
        assert s.source_name == "ntr"

    def test_not_running_initially(self):
        s = NTRFrameSource(host="127.0.0.1", port=9000)
        assert s.is_running is False

    def test_get_frame_returns_none_when_stopped(self):
        s = NTRFrameSource(host="127.0.0.1", port=9000)
        assert s.get_frame(timeout=0.01) is None

    def test_stop_idempotent(self):
        s = NTRFrameSource(host="127.0.0.1", port=9000)
        s.stop()
        s.stop()


class TestSourceFactoryWithMock:
    @patch("airplay_client.sources.factory.settings")
    def test_factory_creates_sysdvr(self, mock_settings):
        mock_settings.capture_source = "sysdvr"
        mock_settings.sysdvr_url = "rtsp://localhost:6666/video"
        s = create_frame_source()
        assert s.source_name == "sysdvr"

    @patch("airplay_client.sources.factory.settings")
    def test_factory_creates_ntr(self, mock_settings):
        mock_settings.capture_source = "ntr"
        mock_settings.ntr_host = "127.0.0.1"
        mock_settings.ntr_port = 9000
        s = create_frame_source()
        assert s.source_name == "ntr"

    @patch("airplay_client.sources.factory.settings")
    def test_factory_unknown_raises(self, mock_settings):
        mock_settings.capture_source = "invalid"
        with pytest.raises(ValueError, match="Unsupported"):
            create_frame_source()
