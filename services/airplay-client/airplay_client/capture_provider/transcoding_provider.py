"""Transcoding capture provider wrapper.

Wraps a passthrough CaptureProvider (H.264) and re-encodes to H.265 via
decode → GPU encode. Preserves zero-copy as default; only used when
CC_CLIENT_TRANSCODE_CODEC is set.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame

from airplay_client.encode.base import EncoderBackend

from .base import CaptureProvider

logger = logging.getLogger(__name__)


class TranscodingCaptureProvider(CaptureProvider):
    """Wraps a passthrough provider, decoding H.264 and re-encoding to H.265.

    The inner provider delivers raw H.264 AUs. This wrapper:
    1. Decodes H.264 → BGR via PyAV
    2. Re-encodes BGR → H.265 via the best available GPU encoder

    Audio is passed through from the inner provider unchanged.
    """

    def __init__(
        self,
        inner: CaptureProvider,
        encoder: EncoderBackend,
    ):
        self._inner = inner
        self._encoder = encoder
        self._decoder = None  # Lazy — created on first AU
        self._encoder_started = False
        self._frame_count = 0

    def start(self) -> None:
        self._inner.start()
        logger.info(
            "Transcoding provider started: %s → %s (%s)",
            self._inner.provider_name,
            self._encoder.encoder_name,
            self._encoder.codec,
        )

    def stop(self) -> None:
        self._inner.stop()
        if self._encoder_started:
            self._encoder.flush()
            self._encoder.stop()
            self._encoder_started = False
        if self._decoder:
            self._decoder.close()
            self._decoder = None
        logger.info("Transcoding provider stopped")

    async def get_au(self, timeout: float = 0.5) -> EncodedAccessUnit | None:
        au = await self._inner.get_au(timeout)
        if au is None:
            return None
        return await asyncio.to_thread(self._transcode_au, au)

    async def get_audio(self, timeout: float = 0.1) -> EncodedAudioFrame | None:
        return await self._inner.get_audio(timeout)

    @property
    def provider_name(self) -> str:
        return f"{self._inner.provider_name}→{self._encoder.encoder_name}"

    @property
    def is_running(self) -> bool:
        return self._inner.is_running

    @property
    def codec(self) -> str:
        return self._encoder.codec

    @property
    def has_audio(self) -> bool:
        return self._inner.has_audio

    def _get_decoder(self):
        """Lazy-init the H.264 decoder."""
        if self._decoder is None:
            import av

            self._decoder = av.CodecContext.create(self._inner.codec, "r")
            self._decoder.thread_type = "AUTO"
        return self._decoder

    def _transcode_au(self, au: EncodedAccessUnit) -> EncodedAccessUnit | None:
        """Decode H.264 AU → BGR frame → encode via EncoderBackend."""
        import av

        decoder = self._get_decoder()

        try:
            packet = av.Packet(au.data)
            frames = decoder.decode(packet)
        except av.error.InvalidDataError:
            logger.debug("Skipping corrupt AU (seq=%d)", au.sequence)
            return None

        if not frames:
            return None  # Encoder buffering

        # Take last decoded frame (usually just one)
        video_frame = frames[-1]
        bgr = video_frame.to_ndarray(format="bgr24")

        # Start encoder on first frame (need dimensions)
        if not self._encoder_started:
            h, w = bgr.shape[:2]
            self._encoder.start(w, h)
            self._encoder_started = True
            logger.info("Transcoder encoder started: %dx%d", w, h)

        self._frame_count += 1
        force_kf = au.is_keyframe  # Mirror keyframe cadence from source

        result = self._encoder.encode(bgr, force_keyframe=force_kf)
        if result is None:
            return None

        # Preserve original timing
        return EncodedAccessUnit(
            data=result.data,
            is_keyframe=result.is_keyframe,
            capture_timestamp=au.capture_timestamp,
            sequence=au.sequence,
            codec=self._encoder.codec,
        )
