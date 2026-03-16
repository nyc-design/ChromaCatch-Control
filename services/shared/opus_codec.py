"""Opus audio encoder/decoder using PyAV (FFmpeg libopus wrapper).

Provides first-class audio encoding for the ChromaCatch pipeline.
Opus is the codec — low latency, handles packet loss gracefully, wide support.
"""

from __future__ import annotations

import logging
import time

from .encoded_au import EncodedAudioFrame

logger = logging.getLogger(__name__)


class OpusEncoder:
    """Encodes raw PCM audio into Opus frames.

    Uses PyAV (FFmpeg libopus) for encoding. Produces EncodedAudioFrame
    objects ready for transport via RTP+FEC or WebSocket.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        bitrate: int = 128_000,
        frame_duration_ms: int = 20,
    ):
        self._sample_rate = sample_rate
        self._channels = channels
        self._bitrate = bitrate
        self._frame_duration_ms = frame_duration_ms
        self._codec_ctx = None
        self._sequence = 0
        self._frame_size = sample_rate * frame_duration_ms // 1000  # samples per frame

    def start(self) -> None:
        import av

        if self._codec_ctx is not None:
            self.stop()
        layout = "stereo" if self._channels == 2 else "mono"
        codec = av.Codec("libopus", "w")
        self._codec_ctx = codec.create()
        self._codec_ctx.sample_rate = self._sample_rate
        self._codec_ctx.layout = layout  # setting layout also sets channels
        self._codec_ctx.bit_rate = self._bitrate
        self._codec_ctx.format = av.AudioFormat("s16")
        self._codec_ctx.options = {"frame_duration": str(self._frame_duration_ms)}
        self._codec_ctx.open()
        self._sequence = 0
        logger.info(
            "Opus encoder started: %dHz, %dch, %dkbps, %dms frames",
            self._sample_rate,
            self._channels,
            self._bitrate // 1000,
            self._frame_duration_ms,
        )

    def encode(self, pcm_data: bytes) -> list[EncodedAudioFrame]:
        """Encode raw PCM (s16le) bytes into Opus frames.

        The input PCM should be interleaved s16le samples.
        May return 0 or more frames depending on buffering.
        """
        if self._codec_ctx is None:
            raise RuntimeError("Opus encoder not started. Call start() first.")

        import av
        import numpy as np

        capture_ts = time.time()
        samples = np.frombuffer(pcm_data, dtype=np.int16)
        if self._channels > 1:
            samples = samples.reshape(-1, self._channels)

        av_frame = av.AudioFrame.from_ndarray(
            samples.reshape(1, -1) if self._channels == 1 else samples.T,
            format="s16",
            layout="stereo" if self._channels == 2 else "mono",
        )
        av_frame.sample_rate = self._sample_rate
        av_frame.pts = self._sequence * self._frame_size

        try:
            packets = self._codec_ctx.encode(av_frame)
        except Exception as e:
            logger.warning("Opus encode error: %s", e)
            return []

        results = []
        for p in packets:
            self._sequence += 1
            results.append(
                EncodedAudioFrame(
                    data=bytes(p),
                    capture_timestamp=capture_ts,
                    sequence=self._sequence,
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                    duration_ms=self._frame_duration_ms,
                )
            )
        return results

    def stop(self) -> None:
        if self._codec_ctx is not None:
            try:
                self._codec_ctx.close()
            except Exception:
                pass
            self._codec_ctx = None
            logger.info("Opus encoder stopped")

    @property
    def is_ready(self) -> bool:
        return self._codec_ctx is not None

    @property
    def frame_size(self) -> int:
        """Number of samples per Opus frame."""
        return self._frame_size


class OpusDecoder:
    """Decodes Opus frames back to raw PCM.

    Used on the backend to decode audio for dashboard playback or CV audio analysis.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self._sample_rate = sample_rate
        self._channels = channels
        self._codec_ctx = None

    def start(self) -> None:
        import av

        if self._codec_ctx is not None:
            self.stop()
        self._codec_ctx = av.CodecContext.create("libopus", "r")
        self._codec_ctx.sample_rate = self._sample_rate
        self._codec_ctx.channels = self._channels
        self._codec_ctx.layout = "stereo" if self._channels == 2 else "mono"
        self._codec_ctx.open()
        logger.info("Opus decoder started: %dHz, %dch", self._sample_rate, self._channels)

    def decode(self, opus_data: bytes) -> bytes | None:
        """Decode Opus frame to raw PCM (s16le interleaved) bytes.

        Returns None if decode fails or no output produced.
        """
        if self._codec_ctx is None:
            raise RuntimeError("Opus decoder not started. Call start() first.")

        import av

        packet = av.Packet(opus_data)
        try:
            frames = self._codec_ctx.decode(packet)
        except Exception as e:
            logger.warning("Opus decode error: %s", e)
            return None

        frame_list = list(frames)
        if not frame_list:
            return None

        # Convert first frame to s16le PCM bytes
        frame = frame_list[0]
        array = frame.to_ndarray()  # shape: (channels, samples) for planar
        if array.ndim == 2:
            # Interleave channels: (channels, samples) → (samples, channels) → flat
            array = array.T
        pcm = array.astype("<i2").tobytes()
        return pcm

    def stop(self) -> None:
        if self._codec_ctx is not None:
            try:
                self._codec_ctx.close()
            except Exception:
                pass
            self._codec_ctx = None
            logger.info("Opus decoder stopped")

    @property
    def is_ready(self) -> bool:
        return self._codec_ctx is not None
