"""Canonical encoded media types for the ChromaCatch pipeline.

Every source produces these. Every transport consumes them.
The backend decodes them via a single PyAV decoder path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class EncodedAccessUnit:
    """A single encoded video access unit (H.264 or H.265 Annex-B byte stream).

    Produced by CaptureProviders (passthrough or via EncoderBackend).
    Consumed by MediaTransports for delivery to the backend.
    """

    data: bytes
    is_keyframe: bool = False
    capture_timestamp: float = field(default_factory=time.time)
    sequence: int = 0
    codec: str = "h264"  # "h264" | "h265"
    width: int = 0  # 0 = unknown until decoded
    height: int = 0  # 0 = unknown until decoded

    @property
    def byte_length(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return (
            f"EncodedAccessUnit(codec={self.codec!r}, "
            f"keyframe={self.is_keyframe}, "
            f"seq={self.sequence}, "
            f"size={self.byte_length}B, "
            f"{self.width}x{self.height})"
        )


@dataclass(frozen=True, slots=True)
class EncodedAudioFrame:
    """A single Opus-encoded audio frame.

    Produced by OpusEncoder from raw PCM input.
    Consumed by MediaTransports for delivery to the backend.
    """

    data: bytes
    capture_timestamp: float = field(default_factory=time.time)
    sequence: int = 0
    sample_rate: int = 48000
    channels: int = 2
    duration_ms: int = 20  # Opus frame duration

    @property
    def byte_length(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return (
            f"EncodedAudioFrame(seq={self.sequence}, "
            f"size={self.byte_length}B, "
            f"rate={self.sample_rate}, "
            f"ch={self.channels}, "
            f"dur={self.duration_ms}ms)"
        )
