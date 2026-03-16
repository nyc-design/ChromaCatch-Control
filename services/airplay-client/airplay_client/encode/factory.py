"""Encoder factory — probes hardware and creates the best available encoder."""

from __future__ import annotations

import logging

from .base import EncoderBackend
from .probe import probe_encoder_backends, select_codec
from .pyav_encoder import PyAVEncoder

logger = logging.getLogger(__name__)


def create_encoder(
    prefer_h265: bool = True,
    force_backend: str | None = None,
    fps: int = 30,
    bitrate: int = 4_000_000,
) -> EncoderBackend:
    """Create the best available encoder backend.

    Args:
        prefer_h265: Prefer H.265 when available (better compression).
        force_backend: Force a specific backend ("nvenc", "vaapi", "videotoolbox", "software").
            If None, auto-probes and picks the best.
        fps: Target frame rate.
        bitrate: Target bitrate in bps.

    Returns:
        An EncoderBackend ready to be started with start(width, height).
    """
    capabilities = probe_encoder_backends()

    if force_backend:
        filtered = [c for c in capabilities if c.name == force_backend]
        if not filtered:
            logger.warning("Forced backend '%s' not available, falling back to auto", force_backend)
        else:
            capabilities = filtered

    codec_name, codec_type = select_codec(capabilities, prefer_h265=prefer_h265)
    logger.info("Selected encoder: %s (codec=%s)", codec_name, codec_type)

    return PyAVEncoder(
        codec_name=codec_name,
        codec_type=codec_type,
        fps=fps,
        bitrate=bitrate,
    )
