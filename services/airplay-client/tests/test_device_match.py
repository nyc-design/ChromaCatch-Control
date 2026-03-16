"""Tests for UVC audio device auto-pairing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from airplay_client.audio.device_match import (
    _match_device_name,
    _parse_avfoundation_output,
    _parse_dshow_output,
    _significant_words,
    find_paired_audio_device,
)
from airplay_client.audio.ffmpeg_audio_source import FFmpegAudioSource


# --- Name matching ---


class TestMatchDeviceName:
    def test_exact_match(self):
        audio = ["Mic A", "Elgato Cam Link 4K", "Built-in"]
        assert _match_device_name("Elgato Cam Link 4K", audio) == "Elgato Cam Link 4K"

    def test_exact_match_case_insensitive(self):
        audio = ["elgato cam link 4k"]
        assert _match_device_name("Elgato Cam Link 4K", audio) == "elgato cam link 4k"

    def test_prefix_match(self):
        audio = ["Built-in Mic", "Elgato Cam Link Audio"]
        assert _match_device_name("Elgato Cam Link 4K", audio) == "Elgato Cam Link Audio"

    def test_substring_match(self):
        audio = ["Built-in Mic", "AVerMedia Live Gamer"]
        assert _match_device_name("AVerMedia Live Gamer Portable", audio) == "AVerMedia Live Gamer"

    def test_no_match_returns_none(self):
        audio = ["Built-in Mic", "Logitech Webcam"]
        assert _match_device_name("Elgato Cam Link 4K", audio) is None

    def test_needs_minimum_two_words(self):
        # Single word match shouldn't count (too generic)
        audio = ["USB Audio"]
        assert _match_device_name("USB Video", audio) is None

    def test_empty_audio_list(self):
        assert _match_device_name("Elgato", []) is None


class TestSignificantWords:
    def test_strips_generic_words(self):
        result = _significant_words("elgato cam link 4k audio device")
        assert "audio" not in result
        assert "device" not in result
        assert "elgato" in result
        assert "cam" in result

    def test_empty_string(self):
        assert _significant_words("") == []


# --- AVFoundation parsing ---


class TestParseAvfoundation:
    SAMPLE_OUTPUT = """\
[AVFoundation indev @ 0x...] AVFoundation video devices:
[AVFoundation indev @ 0x...] [0] FaceTime HD Camera
[AVFoundation indev @ 0x...] [1] Elgato Cam Link 4K
[AVFoundation indev @ 0x...] [2] Capture screen 0
[AVFoundation indev @ 0x...] AVFoundation audio devices:
[AVFoundation indev @ 0x...] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x...] [1] Elgato Cam Link 4K
"""

    def test_parse_video_devices(self):
        result = _parse_avfoundation_output(self.SAMPLE_OUTPUT)
        assert result["video"] == [
            "FaceTime HD Camera",
            "Elgato Cam Link 4K",
            "Capture screen 0",
        ]

    def test_parse_audio_devices(self):
        result = _parse_avfoundation_output(self.SAMPLE_OUTPUT)
        assert result["audio"] == [
            "MacBook Pro Microphone",
            "Elgato Cam Link 4K",
        ]

    def test_empty_output(self):
        result = _parse_avfoundation_output("")
        assert result == {"video": [], "audio": []}


# --- DirectShow parsing ---


class TestParseDshow:
    SAMPLE_OUTPUT = """\
[dshow @ 0x...] DirectShow video devices (some may be both video and audio devices)
[dshow @ 0x...]  "Elgato Cam Link 4K"
[dshow @ 0x...]     Alternative name "@device_pnp_..."
[dshow @ 0x...]  "OBS Virtual Camera"
[dshow @ 0x...] DirectShow audio devices
[dshow @ 0x...]  "Microphone (Elgato Cam Link 4K)"
[dshow @ 0x...]     Alternative name "@device_cm_..."
[dshow @ 0x...]  "Microphone (Realtek High Definition Audio)"
"""

    def test_parse_video_devices(self):
        result = _parse_dshow_output(self.SAMPLE_OUTPUT)
        assert result["video"] == ["Elgato Cam Link 4K", "OBS Virtual Camera"]

    def test_parse_audio_devices(self):
        result = _parse_dshow_output(self.SAMPLE_OUTPUT)
        assert result["audio"] == [
            "Microphone (Elgato Cam Link 4K)",
            "Microphone (Realtek High Definition Audio)",
        ]

    def test_skips_alternative_names(self):
        result = _parse_dshow_output(self.SAMPLE_OUTPUT)
        for devices in result.values():
            for name in devices:
                assert "@device" not in name


# --- Integration: find_paired_audio_device ---


class TestFindPairedMacos:
    @patch("airplay_client.audio.device_match.platform.system", return_value="Darwin")
    @patch("airplay_client.audio.device_match._list_avfoundation_devices")
    def test_finds_paired_by_index(self, mock_list, mock_system):
        mock_list.return_value = {
            "video": ["FaceTime HD Camera", "Elgato Cam Link 4K"],
            "audio": ["MacBook Pro Microphone", "Elgato Cam Link 4K"],
        }
        result = find_paired_audio_device("1")  # video index 1 = Elgato
        assert result == ":1"  # audio index 1

    @patch("airplay_client.audio.device_match.platform.system", return_value="Darwin")
    @patch("airplay_client.audio.device_match._list_avfoundation_devices")
    def test_no_match_returns_none(self, mock_list, mock_system):
        mock_list.return_value = {
            "video": ["FaceTime HD Camera"],
            "audio": ["MacBook Pro Microphone"],
        }
        result = find_paired_audio_device("0")
        assert result is None


class TestFindPairedWindows:
    @patch("airplay_client.audio.device_match.platform.system", return_value="Windows")
    @patch("airplay_client.audio.device_match._list_dshow_devices")
    def test_finds_paired_by_index(self, mock_list, mock_system):
        mock_list.return_value = {
            "video": ["Elgato Cam Link 4K", "OBS Virtual Camera"],
            "audio": ["Microphone (Elgato Cam Link 4K)", "Microphone (Realtek)"],
        }
        result = find_paired_audio_device("0")
        assert result == "Microphone (Elgato Cam Link 4K)"

    @patch("airplay_client.audio.device_match.platform.system", return_value="Windows")
    @patch("airplay_client.audio.device_match._list_dshow_devices")
    def test_no_match_returns_none(self, mock_list, mock_system):
        mock_list.return_value = {
            "video": ["OBS Virtual Camera"],
            "audio": ["Microphone (Realtek)"],
        }
        result = find_paired_audio_device("0")
        assert result is None


class TestFindPairedLinux:
    @patch("airplay_client.audio.device_match.platform.system", return_value="Linux")
    @patch("airplay_client.audio.device_match._find_usb_ancestor")
    @patch("airplay_client.audio.device_match._find_alsa_card_under")
    @patch("airplay_client.audio.device_match._alsa_card_to_pulse_source")
    def test_finds_paired_via_sysfs(
        self, mock_pulse, mock_alsa, mock_usb, mock_system, tmp_path
    ):
        # Create mock sysfs structure
        video_sysfs = tmp_path / "sys" / "class" / "video4linux" / "video0"
        video_sysfs.mkdir(parents=True)

        mock_usb.return_value = tmp_path / "usb_device"
        mock_alsa.return_value = 2
        mock_pulse.return_value = "alsa_input.usb-Elgato_Cam_Link_4K"

        with patch(
            "airplay_client.audio.device_match.Path",
            side_effect=lambda p: tmp_path / "sys" / "class" / "video4linux" / "video0"
            if "sys/class" in str(p)
            else type(tmp_path)(p),
        ):
            # Simpler: test _find_paired_linux directly
            from airplay_client.audio.device_match import _find_paired_linux

            with patch("airplay_client.audio.device_match.Path") as MockPath:
                sysfs_mock = MagicMock()
                sysfs_mock.exists.return_value = True
                sysfs_mock.resolve.return_value = tmp_path / "resolved"
                MockPath.side_effect = lambda p: sysfs_mock if "sys/class" in str(p) else type(tmp_path)(p)
                MockPath.return_value = sysfs_mock

                result = _find_paired_linux("0", "pulse")
                assert result == "alsa_input.usb-Elgato_Cam_Link_4K"


class TestFindPairedUnsupported:
    @patch("airplay_client.audio.device_match.platform.system", return_value="FreeBSD")
    def test_unsupported_platform_returns_none(self, mock_system):
        assert find_paired_audio_device("0") is None


# --- Auto-pair in factory ---


class TestAudioFactoryAutoPair:
    def test_auto_pairs_when_capture_source(self):
        from airplay_client.audio.factory import create_audio_source
        from airplay_client.config import client_settings

        orig = (
            client_settings.audio_enabled,
            client_settings.audio_source,
            client_settings.capture_source,
            client_settings.audio_input_device,
        )
        try:
            client_settings.audio_enabled = True
            client_settings.audio_source = "system"
            client_settings.capture_source = "capture"
            client_settings.audio_input_device = ""

            with patch(
                "airplay_client.audio.device_match.find_paired_audio_device",
                return_value="hw:2",
            ) as mock_pair:
                source = create_audio_source()
                assert isinstance(source, FFmpegAudioSource)
                assert source._input_device == "hw:2"
                mock_pair.assert_called_once()
        finally:
            (
                client_settings.audio_enabled,
                client_settings.audio_source,
                client_settings.capture_source,
                client_settings.audio_input_device,
            ) = orig

    def test_skips_auto_pair_when_device_set(self):
        from airplay_client.audio.factory import create_audio_source
        from airplay_client.config import client_settings

        orig = (
            client_settings.audio_enabled,
            client_settings.audio_source,
            client_settings.capture_source,
            client_settings.audio_input_device,
        )
        try:
            client_settings.audio_enabled = True
            client_settings.audio_source = "system"
            client_settings.capture_source = "capture"
            client_settings.audio_input_device = "my_device"

            with patch(
                "airplay_client.audio.device_match.find_paired_audio_device",
            ) as mock_pair:
                source = create_audio_source()
                assert isinstance(source, FFmpegAudioSource)
                mock_pair.assert_not_called()
        finally:
            (
                client_settings.audio_enabled,
                client_settings.audio_source,
                client_settings.capture_source,
                client_settings.audio_input_device,
            ) = orig
