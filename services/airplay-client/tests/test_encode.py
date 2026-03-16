"""Tests for encoder backend abstraction and GPU probing."""

from unittest.mock import MagicMock, patch

import pytest

from airplay_client.encode.base import EncoderBackend
from airplay_client.encode.probe import (
    EncoderCapability,
    _software_fallback,
    probe_encoder_backends,
    select_codec,
)


class TestEncoderCapability:
    def test_software_fallback_always_available(self):
        cap = _software_fallback()
        assert cap.name == "software"
        assert cap.h264_codec == "libx264"
        assert cap.priority == 99

    def test_select_codec_h265_preferred(self):
        caps = [
            EncoderCapability(name="nvenc", h264_codec="h264_nvenc", h265_codec="hevc_nvenc", priority=0),
            EncoderCapability(name="software", h264_codec="libx264", h265_codec="libx265", priority=99),
        ]
        codec_name, codec_type = select_codec(caps, prefer_h265=True)
        assert codec_name == "hevc_nvenc"
        assert codec_type == "h265"

    def test_select_codec_h264_only(self):
        caps = [
            EncoderCapability(name="nvenc", h264_codec="h264_nvenc", h265_codec=None, priority=0),
            EncoderCapability(name="software", h264_codec="libx264", h265_codec=None, priority=99),
        ]
        codec_name, codec_type = select_codec(caps, prefer_h265=True)
        assert codec_name == "h264_nvenc"
        assert codec_type == "h264"

    def test_select_codec_no_h265_preference(self):
        caps = [
            EncoderCapability(name="nvenc", h264_codec="h264_nvenc", h265_codec="hevc_nvenc", priority=0),
        ]
        codec_name, codec_type = select_codec(caps, prefer_h265=False)
        assert codec_name == "h264_nvenc"
        assert codec_type == "h264"

    def test_select_codec_empty_falls_back(self):
        codec_name, codec_type = select_codec([], prefer_h265=True)
        assert codec_name == "libx264"
        assert codec_type == "h264"


class TestProbeEncoderBackends:
    @patch("airplay_client.encode.probe._check_nvenc", return_value=None)
    @patch("airplay_client.encode.probe._check_vaapi", return_value=None)
    @patch("airplay_client.encode.probe._check_videotoolbox", return_value=None)
    def test_probe_returns_software_fallback(self, *mocks):
        caps = probe_encoder_backends()
        assert len(caps) >= 1
        assert caps[-1].name == "software"

    @patch("airplay_client.encode.probe._check_nvenc")
    @patch("airplay_client.encode.probe._check_vaapi", return_value=None)
    @patch("airplay_client.encode.probe._check_videotoolbox", return_value=None)
    def test_probe_nvenc_found(self, mock_vt, mock_vaapi, mock_nvenc):
        mock_nvenc.return_value = EncoderCapability(
            name="nvenc", h264_codec="h264_nvenc", h265_codec="hevc_nvenc", priority=0
        )
        caps = probe_encoder_backends()
        assert caps[0].name == "nvenc"
        assert caps[-1].name == "software"

    @patch("airplay_client.encode.probe._check_nvenc", side_effect=Exception("crash"))
    @patch("airplay_client.encode.probe._check_vaapi", return_value=None)
    @patch("airplay_client.encode.probe._check_videotoolbox", return_value=None)
    def test_probe_handles_exceptions(self, *mocks):
        caps = probe_encoder_backends()
        assert len(caps) >= 1
        assert caps[-1].name == "software"

    def test_capabilities_sorted_by_priority(self):
        caps = [
            EncoderCapability(name="sw", h264_codec="libx264", h265_codec=None, priority=99),
            EncoderCapability(name="nvenc", h264_codec="h264_nvenc", h265_codec=None, priority=0),
            EncoderCapability(name="vaapi", h264_codec="h264_vaapi", h265_codec=None, priority=1),
        ]
        caps.sort(key=lambda c: c.priority)
        assert [c.name for c in caps] == ["nvenc", "vaapi", "sw"]


class TestEncoderBackendABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            EncoderBackend()  # type: ignore


class TestPyAVEncoder:
    """Test PyAV encoder with software fallback (libx264)."""

    @pytest.fixture
    def encoder(self):
        from airplay_client.encode.pyav_encoder import PyAVEncoder

        enc = PyAVEncoder(codec_name="libx264", codec_type="h264", fps=30, bitrate=2_000_000)
        yield enc
        if enc.is_ready:
            enc.stop()

    def test_not_ready_before_start(self, encoder):
        assert encoder.is_ready is False

    def test_start_sets_ready(self, encoder):
        encoder.start(320, 240)
        assert encoder.is_ready is True

    def test_encoder_name(self, encoder):
        assert encoder.encoder_name == "libx264"

    def test_codec_type(self, encoder):
        assert encoder.codec == "h264"

    def test_encode_produces_au(self, encoder):
        import numpy as np

        encoder.start(320, 240)
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        au = encoder.encode(frame, force_keyframe=True)
        assert au is not None
        assert au.byte_length > 0
        assert au.codec == "h264"
        assert au.width == 320
        assert au.height == 240
        assert au.is_keyframe is True

    def test_encode_without_start_raises(self, encoder):
        import numpy as np

        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        with pytest.raises(RuntimeError, match="not started"):
            encoder.encode(frame)

    def test_stop_resets_ready(self, encoder):
        encoder.start(320, 240)
        encoder.stop()
        assert encoder.is_ready is False

    def test_flush(self, encoder):
        import numpy as np

        encoder.start(320, 240)
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        encoder.encode(frame, force_keyframe=True)
        flushed = encoder.flush()
        assert isinstance(flushed, list)

    def test_dimension_change_restarts(self, encoder):
        import numpy as np

        encoder.start(320, 240)
        small = np.zeros((240, 320, 3), dtype=np.uint8)
        encoder.encode(small, force_keyframe=True)
        # Change dimensions
        big = np.zeros((480, 640, 3), dtype=np.uint8)
        au = encoder.encode(big, force_keyframe=True)
        assert au is not None
        assert au.width == 640
        assert au.height == 480

    def test_sequence_increments(self, encoder):
        import numpy as np

        encoder.start(160, 120)
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        au1 = encoder.encode(frame, force_keyframe=True)
        au2 = encoder.encode(frame)
        # Sequence should increment (au2 may be None if encoder buffers)
        if au1 and au2:
            assert au2.sequence > au1.sequence


class TestEncoderFactory:
    @patch("airplay_client.encode.factory.probe_encoder_backends")
    def test_factory_creates_encoder(self, mock_probe):
        from airplay_client.encode.factory import create_encoder

        mock_probe.return_value = [
            EncoderCapability(name="software", h264_codec="libx264", h265_codec=None, priority=99)
        ]
        enc = create_encoder(prefer_h265=True)
        assert enc.encoder_name == "libx264"
        assert enc.codec == "h264"

    @patch("airplay_client.encode.factory.probe_encoder_backends")
    def test_factory_force_backend(self, mock_probe):
        from airplay_client.encode.factory import create_encoder

        mock_probe.return_value = [
            EncoderCapability(name="nvenc", h264_codec="h264_nvenc", h265_codec="hevc_nvenc", priority=0),
            EncoderCapability(name="software", h264_codec="libx264", h265_codec="libx265", priority=99),
        ]
        enc = create_encoder(force_backend="software")
        assert "libx265" in enc.encoder_name or "libx264" in enc.encoder_name
