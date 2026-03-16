"""Runtime GPU encoder capability probing (Sunshine-inspired).

Detects available hardware encoders at runtime and returns them in
preference order. Probing is fail-safe — if a check errors, that
backend is simply skipped.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EncoderCapability:
    """A detected encoder backend capability."""

    name: str  # e.g. "nvenc", "vaapi", "videotoolbox", "software"
    h264_codec: str | None  # PyAV codec name, e.g. "h264_nvenc"
    h265_codec: str | None  # PyAV codec name, e.g. "hevc_nvenc"
    priority: int  # lower = preferred


def _check_nvenc() -> EncoderCapability | None:
    """Check for NVIDIA GPU + NVENC support."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        gpu_name = result.stdout.strip()
        if not gpu_name:
            return None
        # Verify PyAV can open the codec
        try:
            import av

            av.Codec("h264_nvenc", "w")
        except Exception:
            logger.debug("nvidia-smi found GPU '%s' but PyAV h264_nvenc unavailable", gpu_name)
            return None
        h265 = None
        try:
            av.Codec("hevc_nvenc", "w")
            h265 = "hevc_nvenc"
        except Exception:
            pass
        logger.info("NVENC available: %s (h265=%s)", gpu_name, h265 is not None)
        return EncoderCapability(name="nvenc", h264_codec="h264_nvenc", h265_codec=h265, priority=0)
    except Exception:
        return None


def _check_vaapi() -> EncoderCapability | None:
    """Check for VAAPI support (Intel/AMD on Linux)."""
    if platform.system() != "Linux":
        return None
    try:
        import os

        if not os.path.exists("/dev/dri/renderD128"):
            return None
        import av

        av.Codec("h264_vaapi", "w")
    except Exception:
        return None
    h265 = None
    try:
        import av

        av.Codec("hevc_vaapi", "w")
        h265 = "hevc_vaapi"
    except Exception:
        pass
    logger.info("VAAPI available (h265=%s)", h265 is not None)
    return EncoderCapability(name="vaapi", h264_codec="h264_vaapi", h265_codec=h265, priority=1)


def _check_videotoolbox() -> EncoderCapability | None:
    """Check for VideoToolbox support (macOS)."""
    if platform.system() != "Darwin":
        return None
    try:
        import av

        av.Codec("h264_videotoolbox", "w")
    except Exception:
        return None
    h265 = None
    try:
        import av

        av.Codec("hevc_videotoolbox", "w")
        h265 = "hevc_videotoolbox"
    except Exception:
        pass
    logger.info("VideoToolbox available (h265=%s)", h265 is not None)
    return EncoderCapability(
        name="videotoolbox", h264_codec="h264_videotoolbox", h265_codec=h265, priority=2
    )


def _software_fallback() -> EncoderCapability:
    """Software encoding via libx264/libx265 — always available."""
    h265 = None
    try:
        import av

        av.Codec("libx265", "w")
        h265 = "libx265"
    except Exception:
        pass
    return EncoderCapability(name="software", h264_codec="libx264", h265_codec=h265, priority=99)


def probe_encoder_backends() -> list[EncoderCapability]:
    """Detect available encoder backends in preference order.

    Returns a list sorted by priority (lower = preferred).
    Always includes software fallback as the last entry.
    """
    capabilities: list[EncoderCapability] = []
    for checker in [_check_nvenc, _check_vaapi, _check_videotoolbox]:
        try:
            cap = checker()
            if cap is not None:
                capabilities.append(cap)
        except Exception as e:
            logger.debug("Encoder probe failed: %s", e)
    capabilities.append(_software_fallback())
    capabilities.sort(key=lambda c: c.priority)
    logger.info(
        "Encoder backends available: %s",
        [f"{c.name}(h264={c.h264_codec}, h265={c.h265_codec})" for c in capabilities],
    )
    return capabilities


def select_codec(capabilities: list[EncoderCapability], prefer_h265: bool = True) -> tuple[str, str]:
    """Select the best codec name and codec type from available capabilities.

    Args:
        capabilities: Output from probe_encoder_backends().
        prefer_h265: If True, prefer H.265 when available.

    Returns:
        Tuple of (pyav_codec_name, codec_type) e.g. ("hevc_nvenc", "h265").
    """
    if prefer_h265:
        for cap in capabilities:
            if cap.h265_codec:
                return cap.h265_codec, "h265"
    for cap in capabilities:
        if cap.h264_codec:
            return cap.h264_codec, "h264"
    return "libx264", "h264"
