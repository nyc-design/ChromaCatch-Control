"""Factory for selecting the configured audio source."""

from __future__ import annotations

import logging

from airplay_client.audio.airplay_audio_source import AirPlayAudioSource
from airplay_client.audio.base import AudioSource
from airplay_client.audio.ffmpeg_audio_source import FFmpegAudioSource
from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


def _resolve_audio_mode() -> str:
    mode = settings.audio_source.lower().strip()
    if mode == "auto":
        return "airplay" if settings.capture_source.lower().strip() == "airplay" else "system"
    return mode


def _try_auto_pair_capture_audio() -> str | None:
    """When using a UVC capture card, try to find its paired audio device."""
    if settings.audio_input_device:
        return None  # User already specified a device

    from airplay_client.audio.device_match import find_paired_audio_device

    paired = find_paired_audio_device(
        video_device=settings.capture_device,
        audio_backend=settings.audio_input_backend,
    )
    if paired:
        logger.info(
            "Auto-paired capture card audio: video=%s → audio=%s",
            settings.capture_device,
            paired,
        )
    return paired


def create_audio_source() -> AudioSource | None:
    if not settings.audio_enabled:
        return None

    mode = _resolve_audio_mode()
    if mode == "none":
        return None
    if mode == "airplay":
        if settings.capture_source.lower().strip() != "airplay":
            raise ValueError(
                "CC_CLIENT_AUDIO_SOURCE='airplay' requires CC_CLIENT_CAPTURE_SOURCE='airplay'."
            )
        return AirPlayAudioSource()
    if mode == "system":
        # Auto-pair audio device when using UVC capture card
        paired_device = None
        if settings.capture_source.lower().strip() == "capture":
            paired_device = _try_auto_pair_capture_audio()

        return FFmpegAudioSource(input_device=paired_device or None)

    raise ValueError(
        f"Unsupported CC_CLIENT_AUDIO_SOURCE='{settings.audio_source}'. "
        "Use one of: auto, airplay, system, none."
    )
