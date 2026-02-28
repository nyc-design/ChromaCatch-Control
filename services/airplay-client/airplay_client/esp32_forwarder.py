"""Receives HID commands from the backend WebSocket and forwards to ESP32."""

import logging
import time
from uuid import uuid4

from airplay_client.commander.esp32_client import ESP32Client, HIDCommand
from shared.messages import CommandAck, HIDCommandMessage

logger = logging.getLogger(__name__)


class ESP32Forwarder:
    """Translates HIDCommandMessage from backend into ESP32 HTTP calls."""

    def __init__(self, esp32_client: ESP32Client) -> None:
        self._esp32 = esp32_client
        self._commands_sent = 0
        self._commands_acked = 0
        self._last_command_rtt_ms: float | None = None

    @property
    def commands_sent(self) -> int:
        return self._commands_sent

    @property
    def commands_acked(self) -> int:
        return self._commands_acked

    @property
    def last_command_rtt_ms(self) -> float | None:
        return self._last_command_rtt_ms

    async def handle_command(self, msg: HIDCommandMessage) -> CommandAck:
        """Forward a HID command to the ESP32."""
        received_at = time.time()
        command_id = msg.command_id or str(uuid4())
        self._commands_sent += 1

        try:
            cmd = HIDCommand(msg.action, **msg.params)
            forwarded_at = time.time()
            result = await self._esp32.send_command(cmd)
            completed_at = time.time()
            self._commands_acked += 1
            if msg.dispatched_at_backend is not None:
                self._last_command_rtt_ms = max(
                    0.0,
                    (completed_at - msg.dispatched_at_backend) * 1000,
                )
            logger.debug("ESP32 executed %s: %s", msg.action, result)
            return CommandAck(
                command_id=command_id,
                command_sequence=msg.command_sequence,
                received_at_client=received_at,
                forwarded_at_client=forwarded_at,
                completed_at_client=completed_at,
                success=True,
            )
        except Exception as e:
            completed_at = time.time()
            self._commands_acked += 1
            logger.error("Failed to forward command to ESP32: %s", e)
            return CommandAck(
                command_id=command_id,
                command_sequence=msg.command_sequence,
                received_at_client=received_at,
                forwarded_at_client=None,
                completed_at_client=completed_at,
                success=False,
                error=str(e),
            )
