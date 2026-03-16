"""CaptureProvider factory — creates the right provider based on source config."""

from __future__ import annotations

import logging

from airplay_client.config import client_settings as settings

from .base import CaptureProvider

logger = logging.getLogger(__name__)


def create_capture_provider() -> CaptureProvider:
    """Create a CaptureProvider based on CC_CLIENT_CAPTURE_SOURCE config.

    Passthrough providers (zero-copy H.264):
      - airplay: AirPlayCaptureProvider (UxPlay → H264Capture)
      - sysdvr: SysDVRCaptureProvider (RTSP → GStreamer H.264 depay)

    Encoding providers (raw frames → EncoderBackend):
      - capture: EncodingCaptureProvider(CaptureCardFrameSource + encoder)
      - screen: EncodingCaptureProvider(ScreenFrameSource + encoder)
      - ntr: EncodingCaptureProvider(NTRFrameSource + encoder)
    """
    source = settings.capture_source.lower()
    audio_source = _create_audio_source()

    if source == "airplay":
        from airplay_client.capture.h264_capture import H264Capture

        from .airplay_provider import AirPlayCaptureProvider

        return AirPlayCaptureProvider(
            h264_capture=H264Capture(udp_port=settings.airplay_udp_port),
            audio_source=audio_source,
        )

    if source == "sysdvr":
        from .sysdvr_provider import SysDVRCaptureProvider

        rtsp_url = getattr(settings, "sysdvr_rtsp_url", "rtsp://192.168.1.100:6666/video")
        return SysDVRCaptureProvider(rtsp_url=rtsp_url)

    # Raw-frame sources need an encoder
    from airplay_client.encode.factory import create_encoder

    encoder = create_encoder(
        prefer_h265=getattr(settings, "prefer_h265", True),
        force_backend=getattr(settings, "encoder_preset", None),
    )

    frame_source = _create_frame_source(source)

    from .encoding_provider import EncodingCaptureProvider

    return EncodingCaptureProvider(
        frame_source=frame_source,
        encoder=encoder,
        audio_source=audio_source,
    )


def _create_frame_source(source: str):
    """Create the underlying FrameSource for raw-frame capture."""
    from airplay_client.sources.factory import create_frame_source

    # Reuse the existing source factory — it handles capture, screen, ntr
    return create_frame_source()


def _create_audio_source():
    """Create an AudioSource if audio is enabled."""
    from airplay_client.audio.factory import create_audio_source

    return create_audio_source()
