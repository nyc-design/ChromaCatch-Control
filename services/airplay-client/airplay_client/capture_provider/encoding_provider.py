"""Generic encoding capture provider.

Wraps any FrameSource (UVC, screen, NTR) + EncoderBackend to produce
canonical EncodedAccessUnit. Used for sources that produce raw BGR frames.
"""

from __future__ import annotations

import asyncio
import logging

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame
from shared.opus_codec import OpusEncoder

from airplay_client.audio.base import AudioSource
from airplay_client.encode.base import EncoderBackend
from airplay_client.sources.base import FrameSource

from .base import CaptureProvider

logger = logging.getLogger(__name__)


class EncodingCaptureProvider(CaptureProvider):
    """Captures raw frames from a FrameSource, encodes to H.264/H.265 via EncoderBackend.

    Used for sources that don't produce encoded video natively:
    - UVC capture cards (CaptureCardFrameSource)
    - Screen/window capture (ScreenFrameSource)
    - NTR modded 3DS (NTRFrameSource, JPEG→decode→re-encode)
    """

    def __init__(
        self,
        frame_source: FrameSource,
        encoder: EncoderBackend,
        audio_source: AudioSource | None = None,
        keyframe_interval: int = 60,
    ):
        self._frame_source = frame_source
        self._encoder = encoder
        self._audio_source = audio_source
        self._opus_encoder: OpusEncoder | None = None
        self._keyframe_interval = keyframe_interval
        self._frame_count = 0
        self._encoder_started = False

    def start(self) -> None:
        self._frame_source.start()
        # Encoder start is deferred until first frame (need dimensions)
        self._encoder_started = False
        if self._audio_source:
            self._audio_source.start()
            self._opus_encoder = OpusEncoder(
                sample_rate=self._audio_source.sample_rate,
                channels=self._audio_source.channels,
            )
            self._opus_encoder.start()
        logger.info(
            "Encoding capture provider started: source=%s, encoder=%s, audio=%s",
            self._frame_source.source_name,
            self._encoder.encoder_name,
            self._audio_source is not None,
        )

    def stop(self) -> None:
        self._frame_source.stop()
        if self._encoder_started:
            flush = self._encoder.flush()
            if flush:
                logger.debug("Flushed %d frames from encoder", len(flush))
            self._encoder.stop()
            self._encoder_started = False
        if self._audio_source:
            self._audio_source.stop()
        if self._opus_encoder:
            self._opus_encoder.stop()
            self._opus_encoder = None
        logger.info("Encoding capture provider stopped")

    async def get_au(self, timeout: float = 0.5) -> EncodedAccessUnit | None:
        frame = await asyncio.to_thread(self._frame_source.get_frame, timeout)
        if frame is None:
            return None

        # Start encoder on first frame (now we know dimensions)
        if not self._encoder_started:
            h, w = frame.shape[:2]
            self._encoder.start(w, h)
            self._encoder_started = True

        # Force keyframe at regular intervals
        self._frame_count += 1
        force_kf = (self._frame_count % self._keyframe_interval) == 1

        return self._encoder.encode(frame, force_keyframe=force_kf)

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
        return f"{self._frame_source.source_name}-{self._encoder.encoder_name}"

    @property
    def is_running(self) -> bool:
        return self._frame_source.is_running

    @property
    def codec(self) -> str:
        return self._encoder.codec

    @property
    def has_audio(self) -> bool:
        return self._audio_source is not None
