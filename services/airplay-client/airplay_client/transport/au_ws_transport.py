"""Unified AU WebSocket transport — H.264/H.265 AUs + Opus audio over WebSocket.

Consumes EncodedAccessUnit and EncodedAudioFrame from a CaptureProvider.
Works over TCP/HTTPS — compatible with Cloud Run, Fly.io, any HTTP platform.
"""

from __future__ import annotations

import asyncio
import logging
import time

from airplay_client.capture_provider.base import CaptureProvider
from airplay_client.transport.base import MediaTransport
from airplay_client.ws_client import WebSocketClient
from shared.messages import AudioFrameMetadata, H264FrameMetadata

logger = logging.getLogger(__name__)


class AUWebSocketTransport(MediaTransport):
    """Sends encoded video AUs + Opus audio over WebSocket.

    Uses the existing two-message WS pattern:
    1. JSON metadata (H264FrameMetadata with codec field, or AudioFrameMetadata)
    2. Binary payload (H.264/H.265 AU bytes, or Opus frame bytes)
    """

    def __init__(self, frame_ws: WebSocketClient):
        self._frame_ws = frame_ws
        self._provider: CaptureProvider | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._frames_sent_count = 0
        self._audio_frames_sent_count = 0
        self._bytes_sent_count = 0
        self._audio_sequence = 0

    async def start(self) -> None:
        """Legacy start — no-op, use start_with_provider()."""
        raise RuntimeError("AUWebSocketTransport requires start_with_provider()")

    async def start_with_provider(self, provider: CaptureProvider) -> None:
        """Start transport, pulling AUs from the given CaptureProvider."""
        self._provider = provider
        self._running = True
        self._tasks = [
            asyncio.create_task(self._frame_ws.connect()),
            asyncio.create_task(self._video_sender_loop()),
        ]
        if provider.has_audio:
            self._tasks.append(asyncio.create_task(self._audio_sender_loop()))
        logger.info("AU WebSocket transport started (codec=%s, audio=%s)", provider.codec, provider.has_audio)

    async def _video_sender_loop(self) -> None:
        """Pull video AUs from provider and send over WebSocket."""
        while self._running and self._provider:
            try:
                au = await self._provider.get_au(0.5)
                if au is None:
                    continue

                # Send via existing WS client method (supports codec field)
                await self._frame_ws.send_h264_au(
                    au_bytes=au.data,
                    is_keyframe=au.is_keyframe,
                    capture_timestamp=au.capture_timestamp,
                    codec=au.codec,
                )
                self._frames_sent_count += 1
                self._bytes_sent_count += au.byte_length

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("AU WS video send error")
                await asyncio.sleep(0.1)

    async def _audio_sender_loop(self) -> None:
        """Pull audio frames from provider and send over WebSocket."""
        while self._running and self._provider:
            try:
                audio = await self._provider.get_audio(0.1)
                if audio is None:
                    continue

                # Send Opus audio frame as AudioFrameMetadata + binary
                if self._frame_ws.is_connected:
                    self._audio_sequence += 1
                    metadata = AudioFrameMetadata(
                        sequence=self._audio_sequence,
                        sample_rate=audio.sample_rate,
                        channels=audio.channels,
                        duration_ms=audio.duration_ms,
                        capture_timestamp=audio.capture_timestamp,
                        sent_timestamp=time.time(),
                        byte_length=audio.byte_length,
                    )
                    async with self._frame_ws._send_lock:
                        try:
                            await self._frame_ws._ws.send(metadata.model_dump_json())
                            await self._frame_ws._ws.send(audio.data)
                        except Exception:
                            pass
                    self._audio_frames_sent_count += 1
                    self._bytes_sent_count += audio.byte_length

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("AU WS audio send error")
                await asyncio.sleep(0.1)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        await self._frame_ws.disconnect()
        logger.info(
            "AU WebSocket transport stopped (%d video, %d audio frames)",
            self._frames_sent_count, self._audio_frames_sent_count,
        )

    @property
    def is_connected(self) -> bool:
        return self._frame_ws.is_connected

    @property
    def transport_name(self) -> str:
        return "au-ws"

    @property
    def frames_sent(self) -> int:
        return self._frames_sent_count

    @property
    def audio_frames_sent(self) -> int:
        return self._audio_frames_sent_count

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent_count
