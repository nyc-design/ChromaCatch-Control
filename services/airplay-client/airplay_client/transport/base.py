"""Abstract base class for media transport."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from airplay_client.capture_provider.base import CaptureProvider


class MediaTransport(ABC):
    """Base class for media transport backends.

    A MediaTransport is responsible for delivering video and audio data
    from the client to the backend. The control plane (commands, status,
    ACKs) always uses WebSocket regardless of transport mode.

    New (unified) transports implement start_with_provider() to pull
    EncodedAccessUnit/EncodedAudioFrame from a CaptureProvider.
    Legacy transports implement start() with their own source management.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the transport (legacy path — transport manages its own sources)."""

    async def start_with_provider(self, provider: CaptureProvider) -> None:
        """Start the transport, pulling media from the given CaptureProvider.

        Override this in unified transports. Default falls back to start().
        """
        await self.start()

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport and clean up resources."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is actively delivering media."""

    @property
    @abstractmethod
    def transport_name(self) -> str:
        """Human-readable transport name for status reporting."""
