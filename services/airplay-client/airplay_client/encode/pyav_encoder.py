"""PyAV-based video encoder — single implementation supporting all FFmpeg backends.

Wraps any FFmpeg encoder (NVENC, VAAPI, VideoToolbox, x264, x265) via PyAV.
Configured at construction with the codec name from probe results.
"""

from __future__ import annotations

import logging
import time
from fractions import Fraction

import numpy as np

from shared.encoded_au import EncodedAccessUnit

from .base import EncoderBackend

logger = logging.getLogger(__name__)


class PyAVEncoder(EncoderBackend):
    """Video encoder using PyAV (FFmpeg wrapper).

    Supports H.264 and H.265 with any FFmpeg encoder backend.
    Uses ultrafast/zerolatency settings for real-time streaming.
    """

    def __init__(
        self,
        codec_name: str = "libx264",
        codec_type: str = "h264",
        fps: int = 30,
        bitrate: int = 4_000_000,
        preset: str = "ultrafast",
        tune: str = "zerolatency",
        gop_size: int = 60,
    ):
        self._codec_name = codec_name
        self._codec_type = codec_type
        self._fps = fps
        self._bitrate = bitrate
        self._preset = preset
        self._tune = tune
        self._gop_size = gop_size
        self._codec_ctx = None
        self._sequence = 0
        self._width = 0
        self._height = 0

    def start(self, width: int, height: int) -> None:
        import av

        if self._codec_ctx is not None:
            self.stop()

        self._width = width
        self._height = height

        codec = av.Codec(self._codec_name, "w")
        self._codec_ctx = codec.create()
        self._codec_ctx.width = width
        self._codec_ctx.height = height
        self._codec_ctx.pix_fmt = "yuv420p"
        self._codec_ctx.time_base = Fraction(1, self._fps)
        self._codec_ctx.bit_rate = self._bitrate
        self._codec_ctx.gop_size = self._gop_size

        # Apply encoder-specific options
        options = {}
        if self._codec_name in ("libx264", "libx265"):
            options["preset"] = self._preset
            if self._codec_name == "libx264":
                options["tune"] = self._tune
        elif "nvenc" in self._codec_name:
            options["preset"] = "p1"  # fastest NVENC preset
            options["tune"] = "ull"  # ultra-low-latency
            options["rc"] = "cbr"
        elif "vaapi" in self._codec_name:
            options["rc_mode"] = "CBR"
        elif "videotoolbox" in self._codec_name:
            options["realtime"] = "1"
            options["allow_sw"] = "0"

        self._codec_ctx.options = options
        self._codec_ctx.open()
        self._sequence = 0
        logger.info(
            "Encoder started: %s (%s) %dx%d @ %dfps, %dkbps",
            self._codec_name,
            self._codec_type,
            width,
            height,
            self._fps,
            self._bitrate // 1000,
        )

    def encode(self, frame: np.ndarray, force_keyframe: bool = False) -> EncodedAccessUnit | None:
        if self._codec_ctx is None:
            raise RuntimeError("Encoder not started. Call start(width, height) first.")

        import av

        capture_ts = time.time()

        # Handle frame dimension changes
        h, w = frame.shape[:2]
        if w != self._width or h != self._height:
            logger.info("Frame dimensions changed %dx%d → %dx%d, restarting encoder", self._width, self._height, w, h)
            self.start(w, h)

        av_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        av_frame.pts = self._sequence
        if force_keyframe:
            av_frame.pict_type = av.video.frame.PictureType.I

        try:
            packets = self._codec_ctx.encode(av_frame)
        except av.error.ExitError:
            logger.warning("Encoder exit error, restarting")
            self.start(self._width, self._height)
            return None

        if not packets:
            return None

        au_data = b"".join(bytes(p) for p in packets)
        is_kf = force_keyframe or any(p.is_keyframe for p in packets)
        self._sequence += 1

        return EncodedAccessUnit(
            data=au_data,
            is_keyframe=is_kf,
            capture_timestamp=capture_ts,
            sequence=self._sequence,
            codec=self._codec_type,
            width=self._width,
            height=self._height,
        )

    def flush(self) -> list[EncodedAccessUnit]:
        if self._codec_ctx is None:
            return []
        try:
            packets = self._codec_ctx.encode(None)
        except Exception:
            return []
        results = []
        for p in packets:
            self._sequence += 1
            results.append(
                EncodedAccessUnit(
                    data=bytes(p),
                    is_keyframe=p.is_keyframe,
                    capture_timestamp=time.time(),
                    sequence=self._sequence,
                    codec=self._codec_type,
                    width=self._width,
                    height=self._height,
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
            logger.info("Encoder stopped: %s", self._codec_name)

    @property
    def encoder_name(self) -> str:
        return f"{self._codec_name}"

    @property
    def codec(self) -> str:
        return self._codec_type

    @property
    def is_ready(self) -> bool:
        return self._codec_ctx is not None
