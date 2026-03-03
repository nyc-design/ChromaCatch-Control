"""DSU (Cemuhook) commander — sends controller input via UDP to DSU clients.

Implements the Cemuhook/DSU (DualShock UDP) protocol, which is used by emulators
like Cemu, Dolphin, Citra, and others to receive controller input over the
network. Sends 100-byte padded controller data packets over UDP.

Protocol: https://v1993.github.io/cemern-protocol/

Useful for feeding input to emulators running on the same or different machine.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from zlib import crc32

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)

# DSU protocol constants
DSU_MAGIC = b"DSUS"  # Server magic (we're the "server" sending data to emulators)
DSU_PROTOCOL_VERSION = 1001
DSU_MAX_PROTOCOL_VERSION = DSU_PROTOCOL_VERSION
DSU_PACKET_TYPE_DATA = 0x100002  # Controller data packet

# D-pad flags (byte 5 in controller data)
_DPAD_LEFT = 0x80
_DPAD_DOWN = 0x40
_DPAD_RIGHT = 0x20
_DPAD_UP = 0x10
_DPAD_OPTIONS = 0x08  # Start/Plus
_DPAD_R3 = 0x04
_DPAD_L3 = 0x02
_DPAD_SHARE = 0x01  # Select/Minus

# Face button flags (byte 6)
_BTN_SQUARE = 0x80  # Y
_BTN_CROSS = 0x40  # B
_BTN_CIRCLE = 0x20  # A
_BTN_TRIANGLE = 0x10  # X
_BTN_R1 = 0x08  # R/RB
_BTN_L1 = 0x04  # L/LB
_BTN_R2 = 0x02  # ZR/RT
_BTN_L2 = 0x01  # ZL/LT

# PS button flag (byte 7)
_BTN_PS = 0x01  # Home
_BTN_TOUCH = 0x02  # Capture (mapped to touch button)

# Button name → (byte_index_offset, bitmask) relative to the button bytes
_BUTTON_MAP: dict[str, tuple[int, int]] = {
    # D-pad (byte 0 of buttons)
    "up": (0, _DPAD_UP), "dup": (0, _DPAD_UP),
    "down": (0, _DPAD_DOWN), "ddown": (0, _DPAD_DOWN),
    "left": (0, _DPAD_LEFT), "dleft": (0, _DPAD_LEFT),
    "right": (0, _DPAD_RIGHT), "dright": (0, _DPAD_RIGHT),
    "start": (0, _DPAD_OPTIONS), "plus": (0, _DPAD_OPTIONS),
    "select": (0, _DPAD_SHARE), "minus": (0, _DPAD_SHARE),
    "l3": (0, _DPAD_L3), "lstick": (0, _DPAD_L3),
    "r3": (0, _DPAD_R3), "rstick": (0, _DPAD_R3),
    # Face buttons (byte 1 of buttons)
    "a": (1, _BTN_CIRCLE), "b": (1, _BTN_CROSS),
    "x": (1, _BTN_TRIANGLE), "y": (1, _BTN_SQUARE),
    "l": (1, _BTN_L1), "lb": (1, _BTN_L1), "l1": (1, _BTN_L1),
    "r": (1, _BTN_R1), "rb": (1, _BTN_R1), "r1": (1, _BTN_R1),
    "zl": (1, _BTN_L2), "lt": (1, _BTN_L2), "l2": (1, _BTN_L2),
    "zr": (1, _BTN_R2), "rt": (1, _BTN_R2), "r2": (1, _BTN_R2),
    # Special (byte 2 of buttons)
    "home": (2, _BTN_PS), "capture": (2, _BTN_TOUCH),
}


def _build_dsu_header(packet_type: int, server_id: int, length: int) -> bytes:
    """Build a 16-byte DSU header (before CRC patching)."""
    # magic(4) + version(2) + length(2) + crc32(4) + server_id(4)
    return struct.pack("<4sHHII", DSU_MAGIC, DSU_PROTOCOL_VERSION, length, 0, server_id)


def _patch_crc(packet: bytearray) -> None:
    """Compute CRC32 over the full packet (with crc field zeroed) and patch it in."""
    packet[8:12] = b"\x00\x00\x00\x00"
    crc = crc32(packet) & 0xFFFFFFFF
    struct.pack_into("<I", packet, 8, crc)


class DSUCommander(Commander):
    """Routes game commands to DSU (Cemuhook) protocol clients (emulators)."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self._host = host or settings.commander_host or "127.0.0.1"
        self._port = port or settings.commander_port or 26760
        self._sock: socket.socket | None = None
        self._connected = False
        self._server_id = 0  # Unique per session
        self._packet_number = 0
        # Controller state (3 button bytes + analog sticks + triggers)
        self._btn_bytes = bytearray(3)  # [dpad+misc, face+shoulder, special]
        self._lx = 128  # Left stick X (0-255, 128=center)
        self._ly = 128  # Left stick Y
        self._rx = 128  # Right stick X
        self._ry = 128  # Right stick Y
        self._l2_analog = 0  # L2 trigger (0-255)
        self._r2_analog = 0  # R2 trigger (0-255)
        # Touch
        self._touch_active = False
        self._touch_id = 0
        self._touch_x = 0
        self._touch_y = 0

    def _build_controller_packet(self) -> bytes:
        """Build a full DSU controller data packet (100 bytes)."""
        # Controller data payload (after 16-byte header + 4-byte packet type)
        slot = 0  # Controller slot 0
        slot_state = 2  # Connected
        device_model = 2  # Full gyro (DS4)
        connection_type = 0  # None/not applicable
        mac = b"\x00" * 6  # MAC address (zeroed)
        battery = 5  # Full

        # Build controller data:
        # slot(1) + slot_state(1) + device_model(1) + conn_type(1) +
        # mac(6) + battery(1) + connected(1) = 12 bytes
        ctrl_header = struct.pack(
            "<BBBB6sBB",
            slot, slot_state, device_model, connection_type,
            mac, battery, 1,  # 1 = is active
        )

        # Packet number (4 bytes)
        pkt_num = struct.pack("<I", self._packet_number)
        self._packet_number += 1

        # Buttons + analog (variable part)
        # btn1(1) + btn2(1) + btn_ps(1) + btn_touch(1) +
        # lx(1) + ly(1) + rx(1) + ry(1) +
        # dpad_left(1) + dpad_down(1) + dpad_right(1) + dpad_up(1) +
        # square(1) + cross(1) + circle(1) + triangle(1) +
        # r1(1) + l1(1) + r2(1) + l2(1) = 20 bytes
        btn_data = struct.pack(
            "<BBBB"  # button bytes 0-2 + touch button byte
            "BBBB"   # sticks: lx, ly, rx, ry
            "BBBB"   # analog dpad (left, down, right, up)
            "BBBB"   # analog face (square, cross, circle, triangle)
            "BBBB",  # analog shoulder (r1, l1, r2_analog, l2_analog)
            self._btn_bytes[0], self._btn_bytes[1],
            self._btn_bytes[2] & _BTN_PS, self._btn_bytes[2] & _BTN_TOUCH,
            self._lx, self._ly, self._rx, self._ry,
            # Analog pressure for d-pad (255 if pressed, 0 otherwise)
            255 if (self._btn_bytes[0] & _DPAD_LEFT) else 0,
            255 if (self._btn_bytes[0] & _DPAD_DOWN) else 0,
            255 if (self._btn_bytes[0] & _DPAD_RIGHT) else 0,
            255 if (self._btn_bytes[0] & _DPAD_UP) else 0,
            # Analog pressure for face buttons
            255 if (self._btn_bytes[1] & _BTN_SQUARE) else 0,
            255 if (self._btn_bytes[1] & _BTN_CROSS) else 0,
            255 if (self._btn_bytes[1] & _BTN_CIRCLE) else 0,
            255 if (self._btn_bytes[1] & _BTN_TRIANGLE) else 0,
            # Analog shoulder
            255 if (self._btn_bytes[1] & _BTN_R1) else 0,
            255 if (self._btn_bytes[1] & _BTN_L1) else 0,
            self._r2_analog,
            self._l2_analog,
        )

        # Touch data (1 touch point)
        # active(1) + id(1) + x(2) + y(2) = 6 bytes
        touch_data = struct.pack(
            "<BBhh",
            1 if self._touch_active else 0,
            self._touch_id & 0xFF,
            self._touch_x, self._touch_y,
        )

        # Timestamp in microseconds (8 bytes)
        timestamp = struct.pack("<Q", int(time.time() * 1_000_000))

        # Gyro/accel (6 floats = 24 bytes), zeroed (no motion data)
        motion = b"\x00" * 24  # accel_x, accel_y, accel_z, gyro_pitch, gyro_yaw, gyro_roll

        # Assemble payload
        payload = ctrl_header + pkt_num + btn_data + touch_data + timestamp + motion

        # Build full packet with header
        payload_len = len(payload) + 4  # +4 for packet type field
        header = _build_dsu_header(DSU_PACKET_TYPE_DATA, self._server_id, payload_len)
        packet_type_bytes = struct.pack("<I", DSU_PACKET_TYPE_DATA)

        full = bytearray(header + packet_type_bytes + payload)
        _patch_crc(full)
        return bytes(full)

    def _update_state(self, action: str, params: dict) -> None:
        """Update internal controller state from a command."""
        if action == "button_press":
            btn_name = str(params.get("button", "")).lower()
            mapping = _BUTTON_MAP.get(btn_name)
            if mapping:
                byte_idx, mask = mapping
                self._btn_bytes[byte_idx] |= mask
                # Set analog triggers for L2/R2
                if mask == _BTN_L2 and byte_idx == 1:
                    self._l2_analog = 255
                elif mask == _BTN_R2 and byte_idx == 1:
                    self._r2_analog = 255

        elif action == "button_release":
            btn_name = str(params.get("button", "")).lower()
            mapping = _BUTTON_MAP.get(btn_name)
            if mapping:
                byte_idx, mask = mapping
                self._btn_bytes[byte_idx] &= ~mask
                if mask == _BTN_L2 and byte_idx == 1:
                    self._l2_analog = 0
                elif mask == _BTN_R2 and byte_idx == 1:
                    self._r2_analog = 0

        elif action == "stick":
            stick_id = str(params.get("stick_id", "left")).lower()
            # Input range: -32768..32767 → output range: 0..255
            raw_x = int(params.get("x", 0))
            raw_y = int(params.get("y", 0))
            mapped_x = max(0, min(255, (raw_x + 32768) * 255 // 65535))
            mapped_y = max(0, min(255, (raw_y + 32768) * 255 // 65535))
            if stick_id in ("left", "l"):
                self._lx = mapped_x
                self._ly = mapped_y
            else:
                self._rx = mapped_x
                self._ry = mapped_y

        elif action == "tap":
            self._touch_active = True
            self._touch_id = (self._touch_id + 1) & 0xFF
            self._touch_x = int(params.get("x", 0))
            self._touch_y = int(params.get("y", 0))

        elif action == "touch_release":
            self._touch_active = False

        elif action == "reset":
            self._btn_bytes = bytearray(3)
            self._lx = self._ly = self._rx = self._ry = 128
            self._l2_analog = self._r2_analog = 0
            self._touch_active = False

    async def send_command(self, action: str, params: dict) -> CommandResult:
        if not self._connected:
            await self.connect()

        forwarded_at = time.time()
        try:
            self._update_state(action, params)
            packet = self._build_controller_packet()
            await asyncio.get_event_loop().run_in_executor(
                None, self._sock.sendto, packet, (self._host, self._port)
            )
            logger.debug("dsu: %s %s → %d bytes", action, params, len(packet))
            return CommandResult(success=True, forwarded_at=forwarded_at, completed_at=time.time())
        except Exception as e:
            logger.error("DSU command failed: %s", e)
            return CommandResult(success=False, forwarded_at=forwarded_at, completed_at=time.time(), error=str(e))

    async def connect(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._connected = True
            self._server_id = int(time.time()) & 0xFFFFFFFF
            logger.info("DSU commander ready → %s:%d", self._host, self._port)
        except Exception as e:
            self._connected = False
            logger.error("DSU socket creation failed: %s", e)

    async def disconnect(self) -> None:
        if self._sock:
            self._sock.close()
        self._sock = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def commander_name(self) -> str:
        return "dsu"

    @property
    def supported_command_types(self) -> list[str]:
        return ["gamepad", "touch"]
