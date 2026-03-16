"""Unified capture provider abstraction.

A CaptureProvider produces EncodedAccessUnit (video) and EncodedAudioFrame (audio)
regardless of source type. Passthrough providers (AirPlay, SysDVR) wrap H.264 sources
directly. Encoding providers (UVC, screen, NTR) wrap a FrameSource + EncoderBackend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame


class CaptureProvider(ABC):
    """Unified interface for all capture sources.

    Produces canonical EncodedAccessUnit and EncodedAudioFrame objects
    that any MediaTransport can consume.
    """

    @abstractmethod
    async def get_au(self, timeout: float = 0.5) -> EncodedAccessUnit | None:
        """Get the next encoded video access unit.

        Returns None on timeout or if no frame is available.
        """

    @abstractmethod
    async def get_audio(self, timeout: float = 0.1) -> EncodedAudioFrame | None:
        """Get the next encoded audio frame.

        Returns None on timeout, if no audio is available, or if audio is disabled.
        """

    @abstractmethod
    def start(self) -> None:
        """Start capture source and any dependent encoders."""

    @abstractmethod
    def stop(self) -> None:
        """Stop capture and release resources."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name (e.g. 'airplay-passthrough', 'uvc-nvenc')."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether provider is actively producing frames."""

    @property
    @abstractmethod
    def codec(self) -> str:
        """Video codec being produced: 'h264' or 'h265'."""

    @property
    def has_audio(self) -> bool:
        """Whether this provider produces audio frames."""
        return False
