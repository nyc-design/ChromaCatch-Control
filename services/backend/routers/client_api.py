"""Client-facing API router.

This namespace is the control-plane surface used by first-party clients
(CLI/iOS) and by operators who need to inspect or target connected clients.
"""

from __future__ import annotations

import io
import wave

from fastapi import APIRouter, HTTPException, Query, WebSocket
from pydantic import BaseModel

from backend.app_state import rtsp_consumer, session_manager, ws_handler
from backend.config import backend_settings
from shared.frame_codec import encode_frame
from shared.messages import GameCommandMessage, HIDCommandMessage, SetHIDModeMessage

router = APIRouter(prefix="/api/client/v1", tags=["client-api"])


class ConnectedClientsResponse(BaseModel):
    connected_clients: list[str]
    total_clients: int


class SendCommandRequest(BaseModel):
    client_id: str | None = None
    action: str
    params: dict[str, int | float | str] = {}
    command_type: str | None = None


class SetHIDModeRequest(BaseModel):
    client_id: str | None = None
    hid_mode: str


def _resolve_token_from_websocket(websocket: WebSocket, api_key: str | None) -> str | None:
    auth_header = websocket.headers.get("authorization", "")
    return api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )


def _pcm_chunk_to_wav(
    pcm_bytes: bytes,
    sample_rate: int,
    channels: int,
    sample_format: str,
) -> bytes:
    if sample_format.lower() != "s16le":
        raise HTTPException(status_code=415, detail="Unsupported sample format")
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(max(1, channels))
            wav.setsampwidth(2)
            wav.setframerate(max(1, sample_rate))
            wav.writeframes(pcm_bytes)
        return buffer.getvalue()


@router.websocket("/ws/client")
async def websocket_client_channel(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """Client frame/status channel."""
    token = _resolve_token_from_websocket(websocket, api_key)
    await ws_handler.handle_connection(
        websocket,
        api_key=token,
        channel="frame",
        client_id=client_id,
    )


@router.websocket("/ws/control")
async def websocket_control_channel(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """Dedicated low-jitter command channel."""
    token = _resolve_token_from_websocket(websocket, api_key)
    await ws_handler.handle_connection(
        websocket,
        api_key=token,
        channel="control",
        client_id=client_id,
    )


@router.get("/clients", response_model=ConnectedClientsResponse)
async def list_connected_clients() -> ConnectedClientsResponse:
    clients = session_manager.connected_clients
    return ConnectedClientsResponse(connected_clients=clients, total_clients=len(clients))


@router.post("/commands")
async def send_command(req: SendCommandRequest):
    """Send HID/game commands to one client or broadcast to all clients."""
    has_str_params = any(isinstance(v, str) for v in req.params.values())
    if req.command_type or has_str_params:
        cmd = GameCommandMessage(
            command_type=req.command_type or "keyboard",
            action=req.action,
            params=req.params,
        )
    else:
        cmd = HIDCommandMessage(action=req.action, params=req.params)

    try:
        if req.client_id:
            sent_cmd = await session_manager.send_command(req.client_id, cmd)
            return {
                "status": "sent",
                "action": req.action,
                "client_id": req.client_id,
                "command_id": sent_cmd.command_id,
                "command_sequence": sent_cmd.command_sequence,
            }

        sent = await session_manager.broadcast_command(cmd)
        return {
            "status": "sent",
            "action": req.action,
            "sent_to": len(sent),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/hid-mode")
async def set_hid_mode(req: SetHIDModeRequest):
    """Tell clients to switch HID profile."""
    valid_modes = {"combo", "gamepad", "mouse", "keyboard", "switch_pro"}
    if req.hid_mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid hid_mode: {req.hid_mode}. Must be one of: {', '.join(sorted(valid_modes))}",
        )

    msg = SetHIDModeMessage(hid_mode=req.hid_mode)

    try:
        if req.client_id:
            session = session_manager.get_session(req.client_id)
            if session is None:
                raise ValueError(f"No client connected with id: {req.client_id}")
            ws = session.control_websocket or session.frame_websocket
            if ws is None:
                raise ValueError(f"No active transport for client: {req.client_id}")
            await ws.send_text(msg.model_dump_json())
            return {
                "status": "sent",
                "hid_mode": req.hid_mode,
                "client_id": req.client_id,
            }

        sent_count = 0
        for cid in session_manager.connected_clients:
            session = session_manager.get_session(cid)
            if session is None:
                continue
            ws = session.control_websocket or session.frame_websocket
            if ws is not None:
                await ws.send_text(msg.model_dump_json())
                sent_count += 1

        return {"status": "sent", "hid_mode": req.hid_mode, "sent_to": sent_count}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/clients/{client_id}/status")
async def get_client_status(client_id: str):
    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if session.last_status:
        payload = session.last_status.model_dump()
        payload["backend_commands_sent"] = session.commands_sent
        payload["backend_commands_acked"] = session.commands_acked
        payload["backend_last_command_rtt_ms"] = session.last_command_rtt_ms
        payload["backend_audio_chunks_received"] = session.audio_chunks_received
        payload["backend_frame_latency_ms"] = session.last_frame_latency_ms
        return payload
    return {"detail": "No status received yet"}


@router.post("/clients/{client_id}/rtsp-start")
async def start_rtsp_consumer(client_id: str, stream_path: str = Query(default=None)):
    if not backend_settings.rtsp_consumer_enabled:
        raise HTTPException(status_code=503, detail="RTSP consumer not enabled")
    path = stream_path or f"chromacatch/{client_id}"
    await rtsp_consumer.add_stream(client_id, path)
    return {"status": "started", "client_id": client_id, "stream_path": path}


@router.get("/clients/{client_id}/frame")
async def get_latest_frame(client_id: str):
    from fastapi.responses import Response

    jpeg_bytes, _ = session_manager.get_latest_frame_jpeg(client_id)
    if jpeg_bytes is None:
        frame = session_manager.get_latest_frame(client_id)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame available")
        jpeg_bytes, _, _ = encode_frame(frame, quality=85, max_dimension=0)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.get("/clients/{client_id}/frame.jpeg")
async def get_latest_frame_jpeg(client_id: str):
    """Binary JPEG endpoint (automation-friendly alias)."""
    from fastapi.responses import Response

    jpeg_bytes, _ = session_manager.get_latest_frame_jpeg(client_id)
    if jpeg_bytes is None:
        frame = session_manager.get_latest_frame(client_id)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame available")
        jpeg_bytes, _, _ = encode_frame(frame, quality=85, max_dimension=0)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.get("/clients/{client_id}/audio.wav")
async def get_latest_audio_chunk(client_id: str):
    """Latest client audio chunk wrapped in WAV container."""
    from fastapi.responses import Response

    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if session.latest_audio_chunk is None:
        raise HTTPException(status_code=404, detail="No audio available")

    wav_bytes = _pcm_chunk_to_wav(
        pcm_bytes=session.latest_audio_chunk,
        sample_rate=session.latest_audio_sample_rate,
        channels=session.latest_audio_channels,
        sample_format=session.latest_audio_format,
    )
    return Response(content=wav_bytes, media_type="audio/wav")
