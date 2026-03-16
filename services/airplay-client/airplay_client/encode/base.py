"""Encoder backend abstraction for raw BGR → H.264/H.265 encoding."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from shared.encoded_au import EncodedAccessUnit


class EncoderBackend(ABC):
    """Encodes raw BGR frames into H.264 or H.265 Access Units.

    Implementations wrap different FFmpeg encoder backends via PyAV:
    - NVENC (nvidia)
    - VAAPI (intel/amd)
    - VideoToolbox (macOS)
    - libx264/libx265 (software fallback)
    """

    @abstractmethod
    def encode(self, frame: np.ndarray, force_keyframe: bool = False) -> EncodedAccessUnit | None:
        """Encode a single BGR frame. Returns None if encoder is buffering."""

    @abstractmethod
    def start(self, width: int, height: int) -> None:
        """Initialize encoder with frame dimensions. Must be called before encode()."""

    @abstractmethod
    def stop(self) -> None:
        """Release encoder resources."""

    @abstractmethod
    def flush(self) -> list[EncodedAccessUnit]:
        """Flush any buffered frames from the encoder."""

    @property
    @abstractmethod
    def encoder_name(self) -> str:
        """Human-readable encoder name (e.g. 'nvenc-h265', 'vaapi-h264', 'x264')."""

    @property
    @abstractmethod
    def codec(self) -> str:
        """Codec identifier: 'h264' or 'h265'."""

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Whether the encoder is initialized and ready to encode."""
