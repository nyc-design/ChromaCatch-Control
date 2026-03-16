"""AirPlay passthrough capture provider.

Wraps the existing H264Capture (UxPlay RTP → GStreamer → Annex-B AUs)
and audio source, producing canonical EncodedAccessUnit and EncodedAudioFrame.
Zero decode/re-encode — H.264 AUs pass through untouched.
"""

from __future__ import annotations

import asyncio
import logging

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame
from shared.opus_codec import OpusEncoder

from airplay_client.audio.base import AudioSource
from airplay_client.capture.h264_capture import H264Capture

from .base import CaptureProvider

logger = logging.getLogger(__name__)


class AirPlayCaptureProvider(CaptureProvider):
    """AirPlay H.264 passthrough provider.

    Uses H264Capture to get raw AUs from UxPlay's RTP stream.
    Audio is captured via the existing AudioSource and Opus-encoded.
    """

    def __init__(
        self,
        h264_capture: H264Capture | None = None,
        audio_source: AudioSource | None = None,
        udp_port: int | None = None,
    ):
        self._h264_capture = h264_capture or H264Capture(udp_port=udp_port)
        self._audio_source = audio_source
        self._opus_encoder: OpusEncoder | None = None
        self._sequence = 0
        self._audio_sequence = 0

    def start(self) -> None:
        self._h264_capture.start()
        if self._audio_source:
            self._audio_source.start()
            self._opus_encoder = OpusEncoder(
                sample_rate=self._audio_source.sample_rate,
                channels=self._audio_source.channels,
            )
            self._opus_encoder.start()
        logger.info("AirPlay capture provider started (audio=%s)", self._audio_source is not None)

    def stop(self) -> None:
        self._h264_capture.stop()
        if self._audio_source:
            self._audio_source.stop()
        if self._opus_encoder:
            self._opus_encoder.stop()
            self._opus_encoder = None
        logger.info("AirPlay capture provider stopped")

    async def get_au(self, timeout: float = 0.5) -> EncodedAccessUnit | None:
        result = await asyncio.to_thread(self._h264_capture.get_au, timeout)
        if result is None:
            return None
        au_bytes, is_keyframe, timestamp = result
        self._sequence += 1
        return EncodedAccessUnit(
            data=au_bytes,
            is_keyframe=is_keyframe,
            capture_timestamp=timestamp,
            sequence=self._sequence,
            codec="h264",
        )

    async def get_audio(self, timeout: float = 0.1) -> EncodedAudioFrame | None:
        if not self._audio_source or not self._opus_encoder:
            return None
        pcm = await asyncio.to_thread(self._audio_source.get_chunk, timeout)
        if pcm is None:
            return None
        frames = self._opus_encoder.encode(pcm)
        return frames[0] if frames else None

    @property
    def provider_name(self) -> str:
        return "airplay-passthrough"

    @property
    def is_running(self) -> bool:
        return self._h264_capture.is_running

    @property
    def codec(self) -> str:
        return "h264"

    @property
    def has_audio(self) -> bool:
        return self._audio_source is not None
