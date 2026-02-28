"""WebSocket handler for client connections."""

import logging
import time
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from backend.config import backend_settings
from backend.session_manager import ChannelType, ClientSession, SessionManager
from shared.frame_codec import decode_frame
from shared.messages import (
    AudioChunk,
    ClientStatus,
    CommandAck,
    FrameMetadata,
    HeartbeatPing,
    HeartbeatPong,
    parse_message,
)

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Handles the WebSocket protocol for client connections."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager

    async def handle_connection(
        self,
        websocket: WebSocket,
        api_key: str | None = None,
        channel: ChannelType = "frame",
        client_id: str | None = None,
    ) -> None:
        """Main handler for a client WebSocket connection."""
        if backend_settings.api_key and api_key != backend_settings.api_key:
            await websocket.close(code=4001, reason="Invalid API key")
            return

        await websocket.accept()
        resolved_client_id = (client_id or str(uuid.uuid4())[:8]).strip()
        session = await self._session_manager.register(
            resolved_client_id,
            websocket,
            channel=channel,
        )
        logger.info("Client %s connected (channel=%s)", resolved_client_id, channel)

        try:
            if channel == "control":
                await self._control_message_loop(resolved_client_id, websocket)
            else:
                await self._frame_message_loop(resolved_client_id, session, websocket)
        except WebSocketDisconnect:
            logger.info("Client %s disconnected (channel=%s)", resolved_client_id, channel)
        except Exception as e:
            logger.error("Client %s error (channel=%s): %s", resolved_client_id, channel, e)
        finally:
            await self._session_manager.unregister(resolved_client_id, channel=channel)

    async def _frame_message_loop(
        self,
        client_id: str,
        session: ClientSession,
        websocket: WebSocket,
    ) -> None:
        """Process incoming frame-channel messages from the client."""
        expecting_frame_data: FrameMetadata | None = None
        expecting_audio_data: AudioChunk | None = None

        while True:
            message = await websocket.receive()

            if "text" in message:
                raw = message["text"]
                try:
                    msg = parse_message(raw)
                except Exception:
                    logger.error("Invalid message from %s: %s", client_id, raw[:200])
                    continue

                if isinstance(msg, FrameMetadata):
                    expecting_frame_data = msg
                    expecting_audio_data = None

                elif isinstance(msg, AudioChunk):
                    expecting_audio_data = msg
                    expecting_frame_data = None

                elif isinstance(msg, ClientStatus):
                    session.last_status = msg
                    logger.debug(
                        "Client %s status: airplay=%s, esp32=%s",
                        client_id,
                        msg.airplay_running,
                        msg.esp32_reachable,
                    )

                elif isinstance(msg, CommandAck):
                    self._session_manager.mark_command_ack(client_id, msg)

                elif isinstance(msg, HeartbeatPing):
                    await websocket.send_text(HeartbeatPong().model_dump_json())

                else:
                    logger.debug("Unhandled message type from %s: %s", client_id, msg.type)

            elif "bytes" in message:
                jpeg_bytes = message["bytes"]

                if expecting_frame_data is None:
                    if expecting_audio_data is None:
                        logger.warning(
                            "Received binary data without media metadata from %s",
                            client_id,
                        )
                        continue
                    if len(jpeg_bytes) > backend_settings.max_audio_bytes:
                        logger.warning(
                            "Audio chunk too large from %s: %d bytes",
                            client_id,
                            len(jpeg_bytes),
                        )
                        expecting_audio_data = None
                        continue
                    session.latest_audio_chunk = jpeg_bytes
                    session.latest_audio_sequence = expecting_audio_data.sequence
                    session.latest_audio_sample_rate = expecting_audio_data.sample_rate
                    session.latest_audio_channels = expecting_audio_data.channels
                    session.latest_audio_format = expecting_audio_data.sample_format
                    session.audio_chunks_received += 1
                    session.last_audio_at = time.time()
                    expecting_audio_data = None
                    continue

                if len(jpeg_bytes) > backend_settings.max_frame_bytes:
                    logger.warning(
                        "Frame too large from %s: %d bytes", client_id, len(jpeg_bytes)
                    )
                    expecting_frame_data = None
                    continue

                try:
                    frame = decode_frame(jpeg_bytes)
                    session.latest_frame = frame
                    session.latest_frame_jpeg = jpeg_bytes
                    session.latest_frame_sequence = expecting_frame_data.sequence
                    session.frames_received += 1
                    session.last_frame_at = time.time()

                    latency_ms = (
                        time.time() - expecting_frame_data.capture_timestamp
                    ) * 1000
                    transport_latency_ms = None
                    if expecting_frame_data.sent_timestamp is not None:
                        transport_latency_ms = (
                            time.time() - expecting_frame_data.sent_timestamp
                        ) * 1000
                    logger.debug(
                        "Frame #%d from %s: %dx%d, latency=%.0fms, transport=%s",
                        expecting_frame_data.sequence,
                        client_id,
                        frame.shape[1],
                        frame.shape[0],
                        latency_ms,
                        (
                            f"{transport_latency_ms:.0f}ms"
                            if transport_latency_ms is not None
                            else "n/a"
                        ),
                    )
                except Exception as e:
                    logger.error("Failed to decode frame from %s: %s", client_id, e)

                expecting_frame_data = None

    async def _control_message_loop(self, client_id: str, websocket: WebSocket) -> None:
        """Process incoming control-channel messages from the client."""
        while True:
            message = await websocket.receive()
            if "text" not in message:
                continue

            raw = message["text"]
            try:
                msg = parse_message(raw)
            except Exception:
                logger.error("Invalid control message from %s: %s", client_id, raw[:200])
                continue

            if isinstance(msg, CommandAck):
                self._session_manager.mark_command_ack(client_id, msg)
            elif isinstance(msg, HeartbeatPing):
                await websocket.send_text(HeartbeatPong().model_dump_json())
            elif isinstance(msg, ClientStatus):
                session = self._session_manager.get_session(client_id)
                if session is not None:
                    session.last_status = msg
            else:
                logger.debug("Unhandled control message type from %s: %s", client_id, msg.type)
