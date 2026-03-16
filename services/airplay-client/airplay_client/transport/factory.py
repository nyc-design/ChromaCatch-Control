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

# Legacy transport modes that still work but are deprecated
_DEPRECATED_MODES = {"srt", "srt-failover", "webrtc", "webrtc-failover", "websocket"}

# Unified transport modes that use CaptureProvider
_UNIFIED_MODES = {"rtp-fec-unified", "au-ws", "auto"}


def create_unified_transport(
    frame_ws: WebSocketClient | None = None,
) -> MediaTransport:
    """Create a unified transport that consumes CaptureProvider.

    Supports: 'rtp-fec' (or 'rtp-fec-unified'), 'au-ws', 'auto'
    These transports use start_with_provider(CaptureProvider).
    """
    mode = client_settings.transport_mode.lower()

    if mode in ("rtp-fec", "rtp-fec-unified"):
        from airplay_client.transport.unified_rtp_fec import UnifiedRTPFECTransport

        logger.info("Using unified RTP+FEC transport (UDP + FEC, video + audio)")
        return UnifiedRTPFECTransport()

    if mode in ("au-ws", "h264-ws"):
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

    Args:
        frame_source: The frame source (AirPlay, capture card, screen).
        audio_source: The audio source (or None if disabled).
        frame_ws: WebSocket client for frame channel.
        h264_capture: H.264 capture instance.

    Returns:
        A MediaTransport instance.
    """
    mode = client_settings.transport_mode.lower()

    # Emit deprecation for legacy modes
    if mode in _DEPRECATED_MODES:
        warnings.warn(
            f"Transport mode '{mode}' is deprecated. Use 'rtp-fec' (primary), "
            "'au-ws' (TCP fallback), or 'auto' (smart probe). "
            "Deprecated transports will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )

    if mode == "rtp-fec":
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport

        if h264_capture is None:
            raise ValueError("RTP+FEC transport requires an h264_capture instance")
        logger.info("Using RTP+FEC media transport (lowest latency UDP + Reed-Solomon FEC)")
        return RTPFECTransport(h264_capture=h264_capture)
    elif mode == "srt":
        from airplay_client.transport.srt_transport import SRTTransport

        logger.info("Using SRT media transport (H.264 passthrough + Opus audio)")
        return SRTTransport(audio_enabled=audio_source is not None)
    elif mode == "srt-failover":
        from airplay_client.transport.failover_transport import FailoverTransport
        from airplay_client.transport.srt_transport import SRTTransport
        from airplay_client.transport.ws_transport import WebSocketTransport

        if frame_ws is None:
            raise ValueError("SRT failover requires a frame_ws client for fallback")
        srt = SRTTransport(audio_enabled=audio_source is not None)
        ws = WebSocketTransport(frame_ws=frame_ws, frame_source=frame_source, audio_source=audio_source)
        logger.info("Using SRT media transport with WebSocket failover")
        return FailoverTransport(srt_transport=srt, ws_transport=ws)
    elif mode in ("h264-ws", "au-ws"):
        from airplay_client.transport.h264_ws_transport import H264WebSocketTransport

        if frame_ws is None:
            raise ValueError("H.264-WS transport requires a frame_ws client")
        if h264_capture is None:
            raise ValueError("H.264-WS transport requires an h264_capture instance")
        logger.info("Using H.264 passthrough WebSocket transport")
        return H264WebSocketTransport(
            frame_ws=frame_ws,
            h264_capture=h264_capture,
            audio_source=audio_source,
        )
    elif mode == "webrtc":
        from airplay_client.transport.webrtc_transport import WebRTCTransport

        logger.info("Using WebRTC media transport (H.264 passthrough via WHIP)")
        return WebRTCTransport(audio_enabled=audio_source is not None)
    elif mode == "webrtc-failover":
        from airplay_client.transport.failover_transport import FailoverTransport
        from airplay_client.transport.webrtc_transport import WebRTCTransport
        from airplay_client.transport.ws_transport import WebSocketTransport

        if frame_ws is None:
            raise ValueError("WebRTC failover requires a frame_ws client for fallback")
        webrtc = WebRTCTransport(audio_enabled=audio_source is not None)
        ws = WebSocketTransport(frame_ws=frame_ws, frame_source=frame_source, audio_source=audio_source)
        logger.info("Using WebRTC media transport with WebSocket failover")
        return FailoverTransport(srt_transport=webrtc, ws_transport=ws)
    elif mode == "websocket":
        from airplay_client.transport.ws_transport import WebSocketTransport

        if frame_ws is None:
            raise ValueError("WebSocket transport requires a frame_ws client")
        logger.info("Using WebSocket media transport (JPEG frames + PCM audio)")
        return WebSocketTransport(
            frame_ws=frame_ws,
            frame_source=frame_source,
            audio_source=audio_source,
        )
    elif mode == "auto":
        # Legacy auto path uses h264-ws if available
        from airplay_client.transport.h264_ws_transport import H264WebSocketTransport

        if frame_ws is None:
            raise ValueError("Auto transport requires a frame_ws client")
        if h264_capture is None:
            raise ValueError("Auto transport requires an h264_capture instance")
        logger.info("Auto mode (legacy path): using H.264-WS transport")
        return H264WebSocketTransport(
            frame_ws=frame_ws,
            h264_capture=h264_capture,
            audio_source=audio_source,
        )
    else:
        raise ValueError(
            f"Unknown transport mode: {mode!r}. "
            "Use 'rtp-fec', 'au-ws', 'auto', or legacy: 'srt', 'srt-failover', "
            "'webrtc', 'webrtc-failover', 'h264-ws', 'websocket'."
        )
