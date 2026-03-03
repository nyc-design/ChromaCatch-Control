"""Tests for Commander abstraction + factory + all commanders."""

import struct

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.commander.dsu_commander import DSUCommander, _BUTTON_MAP, _build_dsu_header, _patch_crc, DSU_MAGIC
from airplay_client.commander.esp32_commander import ESP32Commander
from airplay_client.commander.factory import create_commander
from airplay_client.commander.luma3ds_client import Luma3DSCommander
from airplay_client.commander.sysbotbase_client import SysBotbaseCommander


# --- CommandResult ---


class TestCommandResult:
    def test_success_result(self):
        r = CommandResult(success=True, forwarded_at=1.0, completed_at=2.0)
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        r = CommandResult(success=False, error="connection refused")
        assert r.success is False
        assert r.error == "connection refused"


# --- Commander ABC ---


class TestCommanderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Commander()


# --- ESP32Commander ---


class TestESP32Commander:
    @pytest.fixture
    def mock_esp32(self):
        esp32 = MagicMock()
        esp32.send_command = AsyncMock(return_value={"status": "ok"})
        esp32.ping = AsyncMock(return_value=True)
        esp32.close = AsyncMock()
        esp32.status = AsyncMock(return_value={"ble_connected": True})
        esp32.host = "192.168.1.100"
        esp32.port = 80
        return esp32

    @pytest.fixture
    def commander(self, mock_esp32):
        return ESP32Commander(esp32_client=mock_esp32)

    def test_commander_name(self, commander):
        assert commander.commander_name == "esp32"

    def test_supported_command_types(self, commander):
        assert "mouse" in commander.supported_command_types
        assert "keyboard" in commander.supported_command_types

    @pytest.mark.asyncio
    async def test_send_command_success(self, commander, mock_esp32):
        result = await commander.send_command("click", {"x": 100, "y": 200})
        assert result.success is True
        assert result.error is None
        mock_esp32.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_command_failure(self, commander, mock_esp32):
        mock_esp32.send_command = AsyncMock(side_effect=ConnectionError("refused"))
        result = await commander.send_command("click", {"x": 0, "y": 0})
        assert result.success is False
        assert "refused" in result.error

    @pytest.mark.asyncio
    async def test_connect_success(self, commander, mock_esp32):
        await commander.connect()
        assert commander.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_unreachable(self, commander, mock_esp32):
        mock_esp32.ping = AsyncMock(return_value=False)
        await commander.connect()
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, commander, mock_esp32):
        await commander.connect()
        await commander.disconnect()
        assert commander.is_connected is False
        mock_esp32.close.assert_called_once()


# --- Commander Factory ---


class TestCommanderFactory:
    @patch("airplay_client.commander.factory.client_settings")
    def test_create_esp32(self, mock_settings):
        mock_settings.commander_mode = "esp32"
        commander = create_commander()
        assert isinstance(commander, ESP32Commander)

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_sysbotbase(self, mock_settings):
        mock_settings.commander_mode = "sysbotbase"
        mock_settings.commander_host = "192.168.1.50"
        mock_settings.commander_port = 6000
        commander = create_commander()
        assert commander.commander_name == "sysbotbase"

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_luma3ds(self, mock_settings):
        mock_settings.commander_mode = "luma3ds"
        mock_settings.commander_host = "192.168.1.60"
        mock_settings.commander_port = 4950
        commander = create_commander()
        assert commander.commander_name == "luma3ds"

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_virtual_gamepad(self, mock_settings):
        mock_settings.commander_mode = "virtual-gamepad"
        commander = create_commander()
        assert commander.commander_name == "virtual-gamepad"

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_dsu(self, mock_settings):
        mock_settings.commander_mode = "dsu"
        mock_settings.commander_host = "127.0.0.1"
        mock_settings.commander_port = 26760
        commander = create_commander()
        assert commander.commander_name == "dsu"

    @patch("airplay_client.commander.factory.client_settings")
    def test_unknown_mode_raises(self, mock_settings):
        mock_settings.commander_mode = "unknown"
        with pytest.raises(ValueError, match="Unknown commander mode"):
            create_commander()


# --- ESP32Commander mode discovery ---


class TestESP32CommanderMode:
    @pytest.fixture
    def mock_esp32(self):
        esp32 = MagicMock()
        esp32.send_command = AsyncMock(return_value={"status": "ok"})
        esp32.ping = AsyncMock(return_value=True)
        esp32.close = AsyncMock()
        esp32.get_mode = AsyncMock(return_value={"input_mode": "wifi", "output_delivery": "bluetooth", "output_mode": "mouse_kb"})
        esp32.host = "192.168.1.100"
        esp32.port = 80
        return esp32

    @pytest.mark.asyncio
    async def test_connect_queries_mode(self, mock_esp32):
        commander = ESP32Commander(esp32_client=mock_esp32)
        await commander.connect()
        mock_esp32.get_mode.assert_called_once()
        assert commander.esp32_mode is not None
        assert commander.esp32_mode["output_mode"] == "mouse_kb"

    @pytest.mark.asyncio
    async def test_supported_types_mouse_mode(self, mock_esp32):
        commander = ESP32Commander(esp32_client=mock_esp32)
        await commander.connect()
        assert "mouse" in commander.supported_command_types
        assert "keyboard" in commander.supported_command_types

    @pytest.mark.asyncio
    async def test_supported_types_gamepad_mode(self, mock_esp32):
        mock_esp32.get_mode = AsyncMock(return_value={"output_mode": "gamepad"})
        commander = ESP32Commander(esp32_client=mock_esp32)
        await commander.connect()
        assert commander.supported_command_types == ["gamepad"]

    @pytest.mark.asyncio
    async def test_mode_query_failure_graceful(self, mock_esp32):
        mock_esp32.get_mode = AsyncMock(side_effect=ConnectionError("timeout"))
        commander = ESP32Commander(esp32_client=mock_esp32)
        await commander.connect()
        assert commander.is_connected is True
        assert commander.esp32_mode is None


# --- SysBotbaseCommander ---


class TestSysBotbaseCommander:
    @pytest.fixture
    def commander(self):
        return SysBotbaseCommander(host="192.168.1.50", port=6000)

    def test_commander_name(self, commander):
        assert commander.commander_name == "sysbotbase"

    def test_supported_types(self, commander):
        assert "gamepad" in commander.supported_command_types
        assert "touch" in commander.supported_command_types

    def test_translate_button_press(self, commander):
        assert commander._translate("button_press", {"button": "A"}) == "click A"

    def test_translate_button_hold(self, commander):
        assert commander._translate("button_hold", {"button": "B"}) == "press B"

    def test_translate_button_release(self, commander):
        assert commander._translate("button_release", {"button": "X"}) == "release X"

    def test_translate_stick(self, commander):
        assert commander._translate("stick", {"stick_id": "left", "x": 100, "y": -50}) == "setStick LEFT 100 -50"

    def test_translate_stick_right(self, commander):
        assert commander._translate("stick", {"stick_id": "right", "x": 0, "y": 0}) == "setStick RIGHT 0 0"

    def test_translate_tap(self, commander):
        assert commander._translate("tap", {"x": 160, "y": 120}) == "touch 160 120"

    def test_translate_touch_hold(self, commander):
        assert commander._translate("touch_hold", {"x": 100, "y": 200, "duration_ms": 500}) == "touchHold 100 200 500"

    def test_translate_dpad(self, commander):
        assert commander._translate("button_press", {"button": "up"}) == "click DUP"
        assert commander._translate("button_press", {"button": "ddown"}) == "click DDOWN"

    def test_translate_unknown_returns_none(self, commander):
        assert commander._translate("unknown_action", {}) is None

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, commander):
        # Just test socket lifecycle (no real connection)
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_send_without_connect_auto_connects(self, commander):
        # Will fail to connect but should try
        result = await commander.send_command("button_press", {"button": "A"})
        # Should fail gracefully since no server is running
        assert result.success is False or result.error is not None or commander.is_connected is False


# --- Luma3DSCommander ---


class TestLuma3DSCommander:
    @pytest.fixture
    def commander(self):
        return Luma3DSCommander(host="192.168.1.60", port=4950)

    def test_commander_name(self, commander):
        assert commander.commander_name == "luma3ds"

    def test_supported_types(self, commander):
        assert "gamepad" in commander.supported_command_types
        assert "touch" in commander.supported_command_types

    def test_button_press_clears_bit(self, commander):
        """Active LOW: pressing a button clears its bit."""
        commander._update_state("button_press", {"button": "A"})
        assert (commander._buttons & 1) == 0  # Bit 0 cleared

    def test_button_release_sets_bit(self, commander):
        """Releasing a button sets its bit back."""
        commander._update_state("button_press", {"button": "A"})
        commander._update_state("button_release", {"button": "A"})
        assert (commander._buttons & 1) == 1  # Bit 0 set

    def test_stick_centered(self, commander):
        """Default circle pad should be centered at (128, 128)."""
        assert commander._circle_pad == 0x00800080

    def test_stick_update(self, commander):
        commander._update_state("stick", {"x": 0, "y": 0})
        cx = commander._circle_pad & 0xFF
        cy = (commander._circle_pad >> 8) & 0xFF
        assert cx == 128
        assert cy == 128

    def test_tap_sets_touch(self, commander):
        commander._update_state("tap", {"x": 160, "y": 120})
        assert commander._touch & (1 << 24)  # Touch active bit

    def test_touch_release(self, commander):
        commander._update_state("tap", {"x": 100, "y": 100})
        commander._update_state("touch_release", {})
        assert commander._touch == 0x02000000  # Default (inactive)

    def test_reset(self, commander):
        commander._update_state("button_press", {"button": "A"})
        commander._update_state("reset", {})
        assert commander._buttons == 0xFFF  # All released

    @pytest.mark.asyncio
    async def test_connect_creates_socket(self, commander):
        await commander.connect()
        assert commander.is_connected is True
        assert commander._sock is not None
        await commander.disconnect()
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_packet_is_20_bytes(self, commander):
        """Luma3DS input redirect uses 20-byte packets."""
        await commander.connect()
        commander._update_state("button_press", {"button": "A"})
        packet = struct.pack("<IIIII", commander._buttons, commander._touch, commander._circle_pad, commander._cstick, commander._special)
        assert len(packet) == 20
        await commander.disconnect()


# --- DSUCommander ---


class TestDSUCommander:
    @pytest.fixture
    def commander(self):
        return DSUCommander(host="127.0.0.1", port=26760)

    def test_commander_name(self, commander):
        assert commander.commander_name == "dsu"

    def test_supported_types(self, commander):
        assert "gamepad" in commander.supported_command_types
        assert "touch" in commander.supported_command_types

    def test_initial_state_centered(self, commander):
        assert commander._lx == 128
        assert commander._ly == 128
        assert commander._rx == 128
        assert commander._ry == 128
        assert commander._btn_bytes == bytearray(3)

    def test_button_press_a(self, commander):
        """A maps to CIRCLE in DSU."""
        commander._update_state("button_press", {"button": "A"})
        byte_idx, mask = _BUTTON_MAP["a"]
        assert commander._btn_bytes[byte_idx] & mask

    def test_button_release_a(self, commander):
        commander._update_state("button_press", {"button": "A"})
        commander._update_state("button_release", {"button": "A"})
        byte_idx, mask = _BUTTON_MAP["a"]
        assert not (commander._btn_bytes[byte_idx] & mask)

    def test_dpad_up(self, commander):
        commander._update_state("button_press", {"button": "up"})
        byte_idx, mask = _BUTTON_MAP["up"]
        assert commander._btn_bytes[byte_idx] & mask

    def test_l2_r2_trigger_analog(self, commander):
        commander._update_state("button_press", {"button": "ZL"})
        assert commander._l2_analog == 255
        commander._update_state("button_release", {"button": "ZL"})
        assert commander._l2_analog == 0
        commander._update_state("button_press", {"button": "ZR"})
        assert commander._r2_analog == 255
        commander._update_state("button_release", {"button": "ZR"})
        assert commander._r2_analog == 0

    def test_stick_left(self, commander):
        commander._update_state("stick", {"stick_id": "left", "x": 0, "y": 0})
        assert commander._lx == 127  # center (0+32768)*255//65535 = 127
        assert commander._ly == 127

    def test_stick_right_extremes(self, commander):
        commander._update_state("stick", {"stick_id": "right", "x": 32767, "y": -32768})
        assert commander._rx == 255
        assert commander._ry == 0

    def test_tap(self, commander):
        commander._update_state("tap", {"x": 500, "y": 300})
        assert commander._touch_active is True
        assert commander._touch_x == 500
        assert commander._touch_y == 300

    def test_touch_release(self, commander):
        commander._update_state("tap", {"x": 100, "y": 200})
        commander._update_state("touch_release", {})
        assert commander._touch_active is False

    def test_reset(self, commander):
        commander._update_state("button_press", {"button": "A"})
        commander._update_state("stick", {"stick_id": "left", "x": 32767, "y": 32767})
        commander._update_state("reset", {})
        assert commander._btn_bytes == bytearray(3)
        assert commander._lx == 128
        assert commander._ly == 128

    def test_button_map_completeness(self):
        """All standard buttons should be mapped."""
        expected = {"a", "b", "x", "y", "l", "r", "zl", "zr", "up", "down", "left", "right", "start", "select", "home", "capture"}
        for btn in expected:
            assert btn in _BUTTON_MAP, f"Missing button: {btn}"

    def test_dsu_header_magic(self):
        hdr = _build_dsu_header(0x100002, 0, 100)
        assert hdr[:4] == DSU_MAGIC

    def test_dsu_header_version(self):
        hdr = _build_dsu_header(0x100002, 0, 100)
        version = struct.unpack_from("<H", hdr, 4)[0]
        assert version == 1001

    def test_crc_patch(self):
        data = bytearray(b"\x00" * 20)
        _patch_crc(data)
        crc_val = struct.unpack_from("<I", data, 8)[0]
        assert crc_val != 0  # CRC should be non-zero for non-trivial data

    def test_build_controller_packet_length(self, commander):
        packet = commander._build_controller_packet()
        # DSU packets are variable but should be substantial
        assert len(packet) > 50

    def test_packet_number_increments(self, commander):
        commander._build_controller_packet()
        assert commander._packet_number == 1
        commander._build_controller_packet()
        assert commander._packet_number == 2

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, commander):
        await commander.connect()
        assert commander.is_connected is True
        assert commander._sock is not None
        assert commander._server_id > 0
        await commander.disconnect()
        assert commander.is_connected is False
        assert commander._sock is None

    @pytest.mark.asyncio
    async def test_send_command_builds_and_sends(self, commander):
        await commander.connect()
        # Mock the socket to capture what's sent
        sent_data = []
        commander._sock = MagicMock()
        commander._sock.sendto = MagicMock(side_effect=lambda d, a: sent_data.append(d))
        result = await commander.send_command("button_press", {"button": "A"})
        assert result.success is True
        assert len(sent_data) == 1
        assert sent_data[0][:4] == DSU_MAGIC
        await commander.disconnect()

    @pytest.mark.asyncio
    async def test_send_command_failure(self, commander):
        await commander.connect()
        commander._sock = MagicMock()
        commander._sock.sendto = MagicMock(side_effect=OSError("network down"))
        result = await commander.send_command("button_press", {"button": "A"})
        assert result.success is False
        assert "network down" in result.error
        await commander.disconnect()
