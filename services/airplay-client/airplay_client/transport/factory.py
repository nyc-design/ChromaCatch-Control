"""Factory for creating the appropriate media transport."""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

from airplay_client.config import client_settings
from airplay_client.transport.base import MediaTransport

if TYPE_CHECKING:
    from airplay_client.audio.base import AudioSource
    from airplay_client.capture.h264_capture import H264Capture
    from airplay_client.sources.base import FrameSource
    from airplay_client.ws_client import WebSocketClient

logger = logging.getLogger(__name__)


def create_unified_transport(
    frame_ws: WebSocketClient | None = None,
) -> MediaTransport:
    """Create a unified transport that consumes CaptureProvider.

    Supports: 'rtp-fec', 'au-ws', 'auto'
    These transports use start_with_provider(CaptureProvider).
    """
    mode = client_settings.transport_mode.lower()

    if mode in ("rtp-fec", "rtp-fec-unified"):
        from airplay_client.transport.unified_rtp_fec import UnifiedRTPFECTransport

        logger.info("Using unified RTP+FEC transport (UDP + FEC, video + audio)")
        return UnifiedRTPFECTransport()

    if mode in ("au-ws", "websocket"):
        if frame_ws is None:
            raise ValueError("AU WebSocket transport requires a frame_ws client")
        from airplay_client.transport.au_ws_transport import AUWebSocketTransport

        logger.info("Using unified AU WebSocket transport (H.264/H.265 + Opus over WS)")
        return AUWebSocketTransport(frame_ws=frame_ws)

    if mode == "auto":
        if frame_ws is None:
            raise ValueError("Auto transport requires a frame_ws client for WS fallback")
        from airplay_client.transport.auto_transport import AutoTransport

        logger.info("Using auto transport (UDP probe → RTP+FEC or AU WS)")
        return AutoTransport(frame_ws=frame_ws)

    raise ValueError(
        f"Unknown unified transport mode: {mode!r}. "
        "Use 'rtp-fec', 'au-ws', or 'auto'."
    )


def create_media_transport(
    frame_source: FrameSource,
    audio_source: AudioSource | None,
    frame_ws: WebSocketClient | None = None,
    h264_capture: H264Capture | None = None,
) -> MediaTransport:
    """Create a media transport based on config (legacy path).

    Deprecated: use create_unified_transport() with CaptureProvider instead.
    """
    mode = client_settings.transport_mode.lower()

    if mode == "rtp-fec":
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport

        if h264_capture is None:
            raise ValueError("RTP+FEC transport requires an h264_capture instance")
        logger.info("Using RTP+FEC media transport (legacy path)")
        return RTPFECTransport(h264_capture=h264_capture)

    if mode in ("h264-ws", "au-ws"):
        from airplay_client.transport.h264_ws_transport import H264WebSocketTransport

        if frame_ws is None:
            raise ValueError("H.264-WS transport requires a frame_ws client")
        if h264_capture is None:
            raise ValueError("H.264-WS transport requires an h264_capture instance")
        logger.info("Using H.264 passthrough WebSocket transport (legacy path)")
        return H264WebSocketTransport(
            frame_ws=frame_ws,
            h264_capture=h264_capture,
            audio_source=audio_source,
        )

    if mode in ("websocket", "auto"):
        from airplay_client.transport.h264_ws_transport import H264WebSocketTransport

        if frame_ws is None:
            raise ValueError("Transport requires a frame_ws client")
        if h264_capture is None:
            raise ValueError("Transport requires an h264_capture instance")
        logger.info("Using H.264-WS transport (legacy auto path)")
        return H264WebSocketTransport(
            frame_ws=frame_ws,
            h264_capture=h264_capture,
            audio_source=audio_source,
        )

    # Removed modes
    removed = {"srt", "srt-failover", "webrtc", "webrtc-failover"}
    if mode in removed:
        raise ValueError(
            f"Transport mode '{mode}' has been removed. "
            "Use 'rtp-fec' (primary), 'au-ws' (TCP fallback), or 'auto' (smart probe)."
        )

    raise ValueError(
        f"Unknown transport mode: {mode!r}. "
        "Use 'rtp-fec', 'au-ws', or 'auto'."
    )
