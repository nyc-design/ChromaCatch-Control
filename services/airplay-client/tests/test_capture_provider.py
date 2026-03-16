"""Tests for CaptureProvider abstraction and implementations."""

import asyncio
import queue
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame

from airplay_client.capture_provider.base import CaptureProvider


class TestCaptureProviderABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            CaptureProvider()  # type: ignore

    def test_has_audio_default_false(self):
        """Default has_audio should be False."""

        class Stub(CaptureProvider):
            async def get_au(self, timeout=0.5):
                return None

            async def get_audio(self, timeout=0.1):
                return None

            def start(self):
                pass

            def stop(self):
                pass

            @property
            def provider_name(self):
                return "stub"

            @property
            def is_running(self):
                return False

            @property
            def codec(self):
                return "h264"

        s = Stub()
        assert s.has_audio is False


class TestAirPlayCaptureProvider:
    @pytest.fixture
    def mock_h264_capture(self):
        capture = MagicMock()
        capture.is_running = True
        capture.start = MagicMock()
        capture.stop = MagicMock()
        capture.get_au = MagicMock(return_value=(b"\x00\x00\x00\x01\x65data", True, 1700000000.0))
        return capture

    @pytest.fixture
    def provider(self, mock_h264_capture):
        from airplay_client.capture_provider.airplay_provider import AirPlayCaptureProvider

        return AirPlayCaptureProvider(h264_capture=mock_h264_capture, audio_source=None)

    def test_provider_name(self, provider):
        assert provider.provider_name == "airplay-passthrough"

    def test_codec(self, provider):
        assert provider.codec == "h264"

    def test_has_audio_without_source(self, provider):
        assert provider.has_audio is False

    def test_start_starts_capture(self, provider, mock_h264_capture):
        provider.start()
        mock_h264_capture.start.assert_called_once()

    def test_stop_stops_capture(self, provider, mock_h264_capture):
        provider.start()
        provider.stop()
        mock_h264_capture.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_au_returns_encoded_au(self, provider, mock_h264_capture):
        provider.start()
        au = await provider.get_au(timeout=0.5)
        assert au is not None
        assert isinstance(au, EncodedAccessUnit)
        assert au.is_keyframe is True
        assert au.codec == "h264"
        assert au.capture_timestamp == 1700000000.0

    @pytest.mark.asyncio
    async def test_get_au_returns_none_on_timeout(self, provider, mock_h264_capture):
        mock_h264_capture.get_au.return_value = None
        provider.start()
        au = await provider.get_au(timeout=0.1)
        assert au is None

    @pytest.mark.asyncio
    async def test_get_audio_none_without_source(self, provider):
        provider.start()
        audio = await provider.get_audio()
        assert audio is None

    def test_is_running(self, provider, mock_h264_capture):
        mock_h264_capture.is_running = True
        assert provider.is_running is True
        mock_h264_capture.is_running = False
        assert provider.is_running is False

    @pytest.mark.asyncio
    async def test_sequence_increments(self, provider, mock_h264_capture):
        provider.start()
        au1 = await provider.get_au()
        au2 = await provider.get_au()
        assert au1 is not None and au2 is not None
        assert au2.sequence > au1.sequence


class TestEncodingCaptureProvider:
    @pytest.fixture
    def mock_frame_source(self):
        source = MagicMock()
        source.source_name = "test-source"
        source.is_running = True
        source.start = MagicMock()
        source.stop = MagicMock()
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        source.get_frame = MagicMock(return_value=frame)
        return source

    @pytest.fixture
    def mock_encoder(self):
        encoder = MagicMock()
        encoder.encoder_name = "libx264"
        encoder.codec = "h264"
        encoder.is_ready = False
        encoder.start = MagicMock()
        encoder.stop = MagicMock()
        encoder.flush = MagicMock(return_value=[])
        encoder.encode = MagicMock(
            return_value=EncodedAccessUnit(data=b"encoded", is_keyframe=True, codec="h264", width=320, height=240)
        )
        return encoder

    @pytest.fixture
    def provider(self, mock_frame_source, mock_encoder):
        from airplay_client.capture_provider.encoding_provider import EncodingCaptureProvider

        return EncodingCaptureProvider(
            frame_source=mock_frame_source, encoder=mock_encoder, audio_source=None
        )

    def test_provider_name(self, provider):
        assert provider.provider_name == "test-source-libx264"

    def test_codec(self, provider):
        assert provider.codec == "h264"

    def test_start_starts_source(self, provider, mock_frame_source):
        provider.start()
        mock_frame_source.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_au_starts_encoder_on_first_frame(self, provider, mock_encoder):
        provider.start()
        au = await provider.get_au()
        mock_encoder.start.assert_called_once_with(320, 240)
        assert au is not None
        assert isinstance(au, EncodedAccessUnit)

    @pytest.mark.asyncio
    async def test_get_au_returns_none_on_no_frame(self, provider, mock_frame_source):
        mock_frame_source.get_frame.return_value = None
        provider.start()
        au = await provider.get_au()
        assert au is None

    @pytest.mark.asyncio
    async def test_keyframe_interval(self, provider, mock_encoder):
        provider.start()
        # First frame should force keyframe (frame_count=1, 1%60==1)
        await provider.get_au()
        call = mock_encoder.encode.call_args
        # force_keyframe passed as keyword arg
        assert call.kwargs.get("force_keyframe") is True

    def test_stop_flushes_and_stops(self, provider, mock_frame_source, mock_encoder):
        provider.start()
        # Simulate encoder started
        provider._encoder_started = True
        provider.stop()
        mock_encoder.flush.assert_called_once()
        mock_encoder.stop.assert_called_once()
        mock_frame_source.stop.assert_called_once()

    def test_has_audio_without_source(self, provider):
        assert provider.has_audio is False


class TestEncodingProviderWithAudio:
    @pytest.fixture
    def mock_audio_source(self):
        source = MagicMock()
        source.source_name = "test-audio"
        source.sample_rate = 48000
        source.channels = 2
        source.is_running = True
        source.start = MagicMock()
        source.stop = MagicMock()
        source.get_chunk = MagicMock(return_value=b"\x00" * 1920)
        return source

    @pytest.fixture
    def provider(self, mock_audio_source):
        from airplay_client.capture_provider.encoding_provider import EncodingCaptureProvider

        frame_source = MagicMock()
        frame_source.source_name = "test"
        frame_source.is_running = True
        encoder = MagicMock()
        encoder.encoder_name = "libx264"
        encoder.codec = "h264"
        encoder.flush = MagicMock(return_value=[])
        return EncodingCaptureProvider(
            frame_source=frame_source, encoder=encoder, audio_source=mock_audio_source
        )

    def test_has_audio(self, provider):
        assert provider.has_audio is True

    def test_start_starts_audio(self, provider, mock_audio_source):
        provider.start()
        mock_audio_source.start.assert_called_once()


class TestTranscodingCaptureProvider:
    @pytest.fixture
    def mock_inner(self):
        inner = MagicMock()
        inner.provider_name = "airplay-passthrough"
        inner.is_running = True
        inner.codec = "h264"
        inner.has_audio = False
        inner.start = MagicMock()
        inner.stop = MagicMock()
        inner.get_au = AsyncMock(
            return_value=EncodedAccessUnit(
                data=b"\x00\x00\x00\x01\x65data",
                is_keyframe=True,
                codec="h264",
                capture_timestamp=1700000000.0,
                sequence=1,
            )
        )
        inner.get_audio = AsyncMock(return_value=None)
        return inner

    @pytest.fixture
    def mock_encoder(self):
        encoder = MagicMock()
        encoder.encoder_name = "nvenc-h265"
        encoder.codec = "h265"
        encoder.is_ready = False
        encoder.start = MagicMock()
        encoder.stop = MagicMock()
        encoder.flush = MagicMock(return_value=[])
        encoder.encode = MagicMock(
            return_value=EncodedAccessUnit(
                data=b"hevc_encoded",
                is_keyframe=True,
                codec="h265",
                width=1920,
                height=1080,
            )
        )
        return encoder

    @pytest.fixture
    def provider(self, mock_inner, mock_encoder):
        from airplay_client.capture_provider.transcoding_provider import TranscodingCaptureProvider

        return TranscodingCaptureProvider(inner=mock_inner, encoder=mock_encoder)

    def test_provider_name(self, provider):
        assert provider.provider_name == "airplay-passthrough→nvenc-h265"

    def test_codec_is_encoder_codec(self, provider):
        assert provider.codec == "h265"

    def test_is_running_delegates(self, provider, mock_inner):
        assert provider.is_running is True
        mock_inner.is_running = False
        assert provider.is_running is False

    def test_has_audio_delegates(self, provider, mock_inner):
        assert provider.has_audio is False
        mock_inner.has_audio = True
        assert provider.has_audio is True

    def test_start_starts_inner(self, provider, mock_inner):
        provider.start()
        mock_inner.start.assert_called_once()

    def test_stop_stops_inner_and_encoder(self, provider, mock_inner, mock_encoder):
        provider.start()
        provider._encoder_started = True
        provider.stop()
        mock_inner.stop.assert_called_once()
        mock_encoder.flush.assert_called_once()
        mock_encoder.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_au_transcodes(self, provider, mock_inner, mock_encoder):
        import sys

        provider.start()
        # Mock PyAV decode
        mock_frame = MagicMock()
        mock_frame.to_ndarray.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)

        mock_decoder = MagicMock()
        mock_decoder.decode.return_value = [mock_frame]

        mock_av = MagicMock()
        mock_av.CodecContext.create.return_value = mock_decoder
        mock_av.Packet = lambda data: MagicMock(data=data)
        mock_av.error.InvalidDataError = Exception

        with patch.dict(sys.modules, {"av": mock_av, "av.error": mock_av.error}):
            # Reset the lazy decoder so it uses our mock
            provider._decoder = None
            au = await provider.get_au(timeout=0.5)

        assert au is not None
        assert au.codec == "h265"
        assert au.capture_timestamp == 1700000000.0
        mock_encoder.start.assert_called_once_with(1920, 1080)
        mock_encoder.encode.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_au_returns_none_when_inner_none(self, provider, mock_inner):
        mock_inner.get_au = AsyncMock(return_value=None)
        provider.start()
        au = await provider.get_au(timeout=0.1)
        assert au is None

    @pytest.mark.asyncio
    async def test_get_audio_delegates_to_inner(self, provider, mock_inner):
        audio_frame = EncodedAudioFrame(data=b"opus_data")
        mock_inner.get_audio = AsyncMock(return_value=audio_frame)
        result = await provider.get_audio()
        assert result is audio_frame


class TestFactoryTranscodeWrapping:
    @pytest.fixture(autouse=True)
    def restore_settings(self):
        from airplay_client.config import client_settings

        orig = (
            client_settings.capture_source,
            client_settings.transcode_codec,
        )
        try:
            yield
        finally:
            client_settings.capture_source, client_settings.transcode_codec = orig

    def test_no_transcode_by_default(self):
        from airplay_client.capture_provider.factory import _maybe_wrap_transcode
        from airplay_client.config import client_settings

        client_settings.transcode_codec = "none"
        mock_provider = MagicMock()
        result = _maybe_wrap_transcode(mock_provider)
        assert result is mock_provider  # Not wrapped

    def test_transcode_h265_wraps(self):
        from airplay_client.capture_provider.factory import _maybe_wrap_transcode
        from airplay_client.capture_provider.transcoding_provider import TranscodingCaptureProvider
        from airplay_client.config import client_settings

        client_settings.transcode_codec = "h265"
        mock_provider = MagicMock()
        mock_provider.provider_name = "airplay-passthrough"

        with patch("airplay_client.encode.factory.probe_encoder_backends") as mock_probe:
            from airplay_client.encode.probe import EncoderCapability

            mock_probe.return_value = [
                EncoderCapability(name="software", h264_codec="libx264", h265_codec="libx265", priority=99)
            ]
            result = _maybe_wrap_transcode(mock_provider)
            assert isinstance(result, TranscodingCaptureProvider)

    def test_empty_transcode_codec_no_wrap(self):
        from airplay_client.capture_provider.factory import _maybe_wrap_transcode
        from airplay_client.config import client_settings

        client_settings.transcode_codec = ""
        mock_provider = MagicMock()
        result = _maybe_wrap_transcode(mock_provider)
        assert result is mock_provider

    def test_invalid_transcode_codec_raises(self):
        from airplay_client.capture_provider.factory import _maybe_wrap_transcode
        from airplay_client.config import client_settings

        client_settings.transcode_codec = "vp9"
        mock_provider = MagicMock()

        with pytest.raises(ValueError, match="Unsupported CC_CLIENT_TRANSCODE_CODEC"):
            _maybe_wrap_transcode(mock_provider)
