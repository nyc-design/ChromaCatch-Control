"""Cross-platform UVC audio device auto-pairing.

Given a video capture device (index or path), find the corresponding audio
input device on the same USB hardware. Works on Linux, Windows, and macOS.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def find_paired_audio_device(
    video_device: str,
    audio_backend: str = "auto",
) -> str | None:
    """Find the audio input device paired with a video capture device.

    Args:
        video_device: Video device index ("0") or path ("/dev/video0").
        audio_backend: "auto", "pulse", "dshow", or "avfoundation".

    Returns:
        Backend-specific audio device selector string, or None if not found.
    """
    system = platform.system().lower()
    try:
        if system == "linux":
            return _find_paired_linux(video_device, audio_backend)
        if system == "darwin":
            return _find_paired_macos(video_device)
        if system == "windows":
            return _find_paired_windows(video_device)
    except Exception:
        logger.debug("Device pairing failed", exc_info=True)
    return None


def _find_paired_linux(video_device: str, audio_backend: str) -> str | None:
    """Linux: match via sysfs USB device tree.

    Strategy: /dev/videoN → /sys/class/video4linux/videoN/device → walk up to
    USB device → find sibling sound card → PulseAudio/ALSA source name.
    """
    # Resolve to /dev/videoN path
    try:
        dev_index = int(video_device)
        video_path = f"/dev/video{dev_index}"
    except ValueError:
        video_path = video_device

    video_name = Path(video_path).name  # "video0"
    sysfs = Path(f"/sys/class/video4linux/{video_name}")
    if not sysfs.exists():
        logger.debug("sysfs path not found: %s", sysfs)
        return None

    # Walk up to USB device (look for idVendor)
    usb_device = _find_usb_ancestor(sysfs.resolve())
    if usb_device is None:
        logger.debug("No USB ancestor found for %s", video_name)
        return None

    # Find ALSA card under the same USB device
    alsa_card = _find_alsa_card_under(usb_device)
    if alsa_card is None:
        logger.debug("No ALSA card found under %s", usb_device)
        return None

    backend = audio_backend.lower() if audio_backend != "auto" else "pulse"

    if backend == "pulse":
        return _alsa_card_to_pulse_source(alsa_card)

    # ALSA hw device
    return f"hw:{alsa_card}"


def _find_usb_ancestor(path: Path) -> Path | None:
    """Walk up sysfs tree to find the USB device node (has idVendor)."""
    current = path
    for _ in range(20):  # safety limit
        if (current / "idVendor").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _find_alsa_card_under(usb_device: Path) -> int | None:
    """Find ALSA sound card number under a USB device sysfs node."""
    # Look for sound/cardN anywhere under this USB device
    sound_dir = None
    for p in usb_device.rglob("sound"):
        if p.is_dir():
            sound_dir = p
            break
    if sound_dir is None:
        return None

    for card_dir in sorted(sound_dir.iterdir()):
        if card_dir.name.startswith("card"):
            try:
                return int(card_dir.name.removeprefix("card"))
            except ValueError:
                continue
    return None


def _alsa_card_to_pulse_source(card_number: int) -> str | None:
    """Convert ALSA card number to PulseAudio source name."""
    # Try pactl first
    pactl = shutil.which("pactl")
    if pactl:
        try:
            result = subprocess.run(
                [pactl, "list", "sources", "short"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    source_name = parts[1]
                    # PulseAudio names like "alsa_input.usb-..." or contain card number
                    if f"card{card_number}" in source_name or f".{card_number}." in source_name:
                        return source_name
        except Exception:
            logger.debug("pactl source lookup failed", exc_info=True)

    # Fallback: ALSA hw device selector (works with -f alsa too)
    return f"hw:{card_number}"


def _find_paired_macos(video_device: str) -> str | None:
    """macOS: match via ffmpeg device listing + name correlation.

    Strategy: List AVFoundation devices, find the video device by index/name,
    then find an audio device with a matching name prefix.
    """
    devices = _list_avfoundation_devices()
    if not devices:
        return None

    video_devices = devices.get("video", [])
    audio_devices = devices.get("audio", [])

    # Resolve which video device we're using
    try:
        video_idx = int(video_device)
    except ValueError:
        video_idx = None

    target_video_name = None
    if video_idx is not None and video_idx < len(video_devices):
        target_video_name = video_devices[video_idx]
    else:
        # Try to match by name
        for name in video_devices:
            if video_device.lower() in name.lower():
                target_video_name = name
                break

    if not target_video_name:
        logger.debug("Could not identify video device: %s", video_device)
        return None

    # Find matching audio device (same name prefix — most USB capture cards
    # register video and audio devices with the same or similar names)
    audio_match = _match_device_name(target_video_name, audio_devices)
    if audio_match is not None:
        audio_idx = audio_devices.index(audio_match)
        # AVFoundation input format: ":<audio_index>"
        return f":{audio_idx}"

    logger.debug("No audio device matched video '%s'", target_video_name)
    return None


def _list_avfoundation_devices() -> dict[str, list[str]] | None:
    """List AVFoundation video and audio devices via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10,
        )
        # ffmpeg outputs device list to stderr
        return _parse_avfoundation_output(result.stderr)
    except Exception:
        logger.debug("AVFoundation device listing failed", exc_info=True)
        return None


def _parse_avfoundation_output(stderr: str) -> dict[str, list[str]]:
    """Parse ffmpeg -list_devices output for AVFoundation."""
    video_devices: list[str] = []
    audio_devices: list[str] = []
    current_section: list[str] | None = None

    for line in stderr.splitlines():
        if "AVFoundation video devices:" in line:
            current_section = video_devices
            continue
        if "AVFoundation audio devices:" in line:
            current_section = audio_devices
            continue

        if current_section is not None:
            # Lines like: "[AVFoundation ...] [0] Elgato Cam Link 4K"
            match = re.search(r"\[\d+\]\s+(.+)$", line)
            if match:
                current_section.append(match.group(1).strip())

    return {"video": video_devices, "audio": audio_devices}


def _find_paired_windows(video_device: str) -> str | None:
    """Windows: match via ffmpeg dshow device listing + name correlation.

    Strategy: List DirectShow devices, find the video device, then find an
    audio device with a matching name prefix (same USB product name).
    """
    devices = _list_dshow_devices()
    if not devices:
        return None

    video_devices = devices.get("video", [])
    audio_devices = devices.get("audio", [])

    # Resolve which video device we're using
    try:
        video_idx = int(video_device)
        # On Windows with OpenCV, device index maps to DirectShow device order
        if video_idx < len(video_devices):
            target_video_name = video_devices[video_idx]
        else:
            target_video_name = None
    except ValueError:
        # Could be a device name directly
        target_video_name = video_device

    if not target_video_name:
        logger.debug("Could not identify video device: %s", video_device)
        return None

    # Find matching audio device
    audio_match = _match_device_name(target_video_name, audio_devices)
    if audio_match is not None:
        return audio_match  # dshow uses device name directly

    logger.debug("No audio device matched video '%s'", target_video_name)
    return None


def _list_dshow_devices() -> dict[str, list[str]] | None:
    """List DirectShow video and audio devices via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg:
        return None

    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-f", "dshow", "-list_devices", "true", "-i", "dummy"],
            capture_output=True, text=True, timeout=10,
        )
        return _parse_dshow_output(result.stderr)
    except Exception:
        logger.debug("DirectShow device listing failed", exc_info=True)
        return None


def _parse_dshow_output(stderr: str) -> dict[str, list[str]]:
    """Parse ffmpeg -list_devices output for DirectShow."""
    video_devices: list[str] = []
    audio_devices: list[str] = []
    current_section: list[str] | None = None

    for line in stderr.splitlines():
        if "DirectShow video devices" in line:
            current_section = video_devices
            continue
        if "DirectShow audio devices" in line:
            current_section = audio_devices
            continue

        if current_section is not None:
            # Lines like: [dshow @ ...] "Elgato Cam Link 4K" (video)
            match = re.search(r'"(.+?)"', line)
            if match:
                name = match.group(1)
                # Skip "Alternative name" lines
                if "@device" not in name:
                    current_section.append(name)

    return {"video": video_devices, "audio": audio_devices}


def _match_device_name(video_name: str, audio_devices: list[str]) -> str | None:
    """Find an audio device whose name matches the video device.

    USB capture cards typically register both video and audio devices with the
    same product name (e.g., "Elgato Cam Link 4K" for both).
    """
    video_lower = video_name.lower()

    # Exact match first
    for audio in audio_devices:
        if audio.lower() == video_lower:
            return audio

    # Prefix/substring match (e.g., "Elgato Cam Link" matches "Elgato Cam Link 4K Audio")
    # Use longest common prefix of significant words
    video_words = _significant_words(video_lower)
    best_match: str | None = None
    best_score = 0

    for audio in audio_devices:
        audio_words = _significant_words(audio.lower())
        # Count matching words from the start
        common = 0
        for vw, aw in zip(video_words, audio_words):
            if vw == aw:
                common += 1
            else:
                break

        if common > best_score and common >= 2:
            best_score = common
            best_match = audio

    # Also check if one name contains the other
    if best_match is None:
        for audio in audio_devices:
            audio_lower = audio.lower()
            if video_lower in audio_lower or audio_lower in video_lower:
                return audio

    return best_match


def _significant_words(name: str) -> list[str]:
    """Extract significant words from a device name (skip generic terms)."""
    skip = {"audio", "video", "input", "output", "capture", "device", "usb"}
    return [w for w in re.findall(r"[a-z0-9]+", name) if w not in skip]
