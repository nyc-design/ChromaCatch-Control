"""WebSocket handler for client connections."""

import logging
import time
import uuid

import cv2
from fastapi import WebSocket, WebSocketDisconnect

from backend.config import backend_settings
from backend.video_decoder import VideoDecoder
from backend.session_manager import ChannelType, ClientSession, SessionManager
from shared.frame_codec import decode_frame
from shared.messages import (
    AudioChunk,
    AudioFrameMetadata,
    ClientStatus,
    CommandAck,
    FrameMetadata,
    H264FrameMetadata,
    HeartbeatPing,
    HeartbeatPong,
    parse_message,
)

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Handles the WebSocket protocol for client connections."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager
        self._decoders: dict[str, VideoDecoder] = {}

    def _get_decoder(self, client_id: str, codec: str = "h264") -> VideoDecoder:
        """Get or create a video decoder for a client, resetting if codec changes."""
        decoder = self._decoders.get(client_id)
        if decoder is None:
            self._decoders[client_id] = VideoDecoder(codec=codec)
            return self._decoders[client_id]
        if decoder.codec_type != codec:
            logger.info("Codec change for %s: %s → %s, resetting decoder", client_id, decoder.codec_type, codec)
            decoder.reset(codec=codec)
        return decoder

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
        # Reset H.264 decoder on new frame channel — stale state from previous
        # broadcast session would reject all P-frames until next keyframe.
        if channel == "frame" and resolved_client_id in self._decoders:
            self._decoders[resolved_client_id].reset()
            logger.info("H.264 decoder reset for reconnected client %s", resolved_client_id)
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
            # Only clean up if this websocket is still the registered one.
            # A newer connection may have already replaced us — don't clobber it.
            current_session = self._session_manager.get_session(resolved_client_id)
            current_ws = None
            if current_session is not None:
                current_ws = current_session.control_websocket if channel == "control" else current_session.frame_websocket
            if current_ws is websocket:
                await self._session_manager.unregister(resolved_client_id, channel=channel)
                if channel == "frame":
                    self._decoders.pop(resolved_client_id, None)
            else:
                logger.info("Client %s: skipping cleanup, connection already replaced (channel=%s)", resolved_client_id, channel)

    async def _frame_message_loop(
        self,
        client_id: str,
        session: ClientSession,
        websocket: WebSocket,
    ) -> None:
        """Process incoming frame-channel messages from the client."""
        expecting_frame_data: FrameMetadata | None = None
        expecting_h264_data: H264FrameMetadata | None = None
        expecting_audio_data: AudioChunk | None = None
        expecting_opus_data: AudioFrameMetadata | None = None
        msg_count = 0

        while True:
            message = await websocket.receive()
            msg_count += 1
            if msg_count <= 5 or msg_count % 300 == 0:
                msg_type = "text" if "text" in message else "bytes" if "bytes" in message else "other"
                msg_size = len(message.get("text", message.get("bytes", b"")))
                logger.info(
                    "Frame loop msg #%d from %s: type=%s, size=%d",
                    msg_count, client_id, msg_type, msg_size,
                )

            if "text" in message:
                raw = message["text"]
                try:
                    msg = parse_message(raw)
                except Exception:
                    logger.error("Invalid message from %s: %s", client_id, raw[:200])
                    continue

                if isinstance(msg, FrameMetadata):
                    expecting_frame_data = msg
                    expecting_h264_data = None
                    expecting_audio_data = None
                    expecting_opus_data = None

                elif isinstance(msg, H264FrameMetadata):
                    expecting_h264_data = msg
                    expecting_frame_data = None
                    expecting_audio_data = None
                    expecting_opus_data = None

                elif isinstance(msg, AudioFrameMetadata):
                    expecting_opus_data = msg
                    expecting_frame_data = None
                    expecting_h264_data = None
                    expecting_audio_data = None

                elif isinstance(msg, AudioChunk):
                    expecting_audio_data = msg
                    expecting_frame_data = None
                    expecting_h264_data = None
                    expecting_opus_data = None

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
                binary_data = message["bytes"]

                # --- Opus audio frame ---
                if expecting_opus_data is not None:
                    if len(binary_data) > backend_settings.max_audio_bytes:
                        logger.warning("Opus frame too large from %s: %d bytes", client_id, len(binary_data))
                        expecting_opus_data = None
                        continue
                    session.latest_audio_chunk = binary_data
                    session.latest_audio_sequence = expecting_opus_data.sequence
                    session.latest_audio_sample_rate = expecting_opus_data.sample_rate
                    session.latest_audio_channels = expecting_opus_data.channels
                    session.latest_audio_format = "opus"
                    session.audio_chunks_received += 1
                    session.last_audio_at = time.time()
                    expecting_opus_data = None
                    continue

                # --- Audio chunk (legacy PCM) ---
                if expecting_audio_data is not None:
                    if len(binary_data) > backend_settings.max_audio_bytes:
                        logger.warning(
                            "Audio chunk too large from %s: %d bytes",
                            client_id,
                            len(binary_data),
                        )
                        expecting_audio_data = None
                        continue
                    session.latest_audio_chunk = binary_data
                    session.latest_audio_sequence = expecting_audio_data.sequence
                    session.latest_audio_sample_rate = expecting_audio_data.sample_rate
                    session.latest_audio_channels = expecting_audio_data.channels
                    session.latest_audio_format = expecting_audio_data.sample_format
                    session.audio_chunks_received += 1
                    session.last_audio_at = time.time()
                    expecting_audio_data = None
                    continue

                # --- H.264 Access Unit ---
                if expecting_h264_data is not None:
                    if len(binary_data) > backend_settings.max_frame_bytes:
                        logger.warning(
                            "H.264 AU too large from %s: %d bytes",
                            client_id,
                            len(binary_data),
                        )
                        expecting_h264_data = None
                        continue

                    try:
                        codec = getattr(expecting_h264_data, "codec", "h264") or "h264"
                        decoder = self._get_decoder(client_id, codec=codec)
                        frame = decoder.decode(binary_data)
                        if frame is not None:
                            _, jpeg_buf = cv2.imencode(
                                ".jpg",
                                frame,
                                [cv2.IMWRITE_JPEG_QUALITY, 80],
                            )
                            session.latest_frame = frame
                            session.latest_frame_jpeg = jpeg_buf.tobytes()
                            session.latest_frame_sequence = (
                                expecting_h264_data.sequence
                            )
                            session.frames_received += 1
                            session.last_frame_at = time.time()

                            latency_ms = (
                                time.time()
                                - expecting_h264_data.capture_timestamp
                            ) * 1000
                            transport_latency_ms = None
                            if expecting_h264_data.sent_timestamp is not None:
                                transport_latency_ms = (
                                    time.time()
                                    - expecting_h264_data.sent_timestamp
                                ) * 1000
                            logger.debug(
                                "%s #%d from %s: %dx%d, kf=%s, latency=%.0fms, transport=%s",
                                codec.upper(),
                                expecting_h264_data.sequence,
                                client_id,
                                frame.shape[1],
                                frame.shape[0],
                                expecting_h264_data.is_keyframe,
                                latency_ms,
                                (
                                    f"{transport_latency_ms:.0f}ms"
                                    if transport_latency_ms is not None
                                    else "n/a"
                                ),
                            )
                    except Exception as e:
                        logger.error(
                            "Failed to decode %s from %s: %s", codec, client_id, e
                        )

                    expecting_h264_data = None
                    continue

                # --- JPEG frame ---
                if expecting_frame_data is not None:
                    if len(binary_data) > backend_settings.max_frame_bytes:
                        logger.warning(
                            "Frame too large from %s: %d bytes",
                            client_id,
                            len(binary_data),
                        )
                        expecting_frame_data = None
                        continue

                    try:
                        frame = decode_frame(binary_data)
                        session.latest_frame = frame
                        session.latest_frame_jpeg = binary_data
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
                        logger.error(
                            "Failed to decode frame from %s: %s", client_id, e
                        )

                    expecting_frame_data = None
                    continue

                logger.warning(
                    "Received binary data without media metadata from %s",
                    client_id,
                )

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
