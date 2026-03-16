"""FastAPI application for ChromaCatch-Go remote backend."""

import asyncio
import io
import logging
import wave
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from backend.app_state import (
    rtp_fec_receiver,
    session_manager,
    ws_handler,
)
from backend.config import backend_settings
from backend.routers import automation_router, client_router
from shared.frame_codec import encode_frame
from shared.constants import setup_logging
from shared.messages import GameCommandMessage, HIDCommandMessage, SetHIDModeMessage

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ChromaCatch-Go backend starting")
    # Start RTP+FEC receiver if enabled
    if backend_settings.rtp_fec_enabled:
        await rtp_fec_receiver.start()
    yield
    # Shutdown
    await rtp_fec_receiver.stop()
    logger.info("ChromaCatch-Go backend shutting down")


app = FastAPI(
    title="ChromaCatch Control Backend",
    description="Control-plane backend for connected clients, CV, and automation APIs",
    version="0.3.0",
    lifespan=lifespan,
)

# New namespaced APIs (keep legacy endpoints below for backward compatibility).
app.include_router(client_router)
app.include_router(automation_router)


# --- WebSocket Endpoint --- $TODO: REORG: move to separate router file


@app.websocket("/ws/client")
async def websocket_endpoint(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """Frame/status channel from client to backend."""
    auth_header = websocket.headers.get("authorization", "")
    token = api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )
    await ws_handler.handle_connection(
        websocket,
        api_key=token,
        channel="frame",
        client_id=client_id,
    )


@app.websocket("/ws/control")
async def websocket_control_endpoint(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """Dedicated low-latency control channel (commands + acks)."""
    auth_header = websocket.headers.get("authorization", "")
    token = api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )
    await ws_handler.handle_connection(
        websocket,
        api_key=token,
        channel="control",
        client_id=client_id,
    )


# --- REST Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok", "role": "backend"}


class BackendStatus(BaseModel):
    connected_clients: list[str]
    total_clients: int


@app.get("/status", response_model=BackendStatus)
async def get_status():
    clients = session_manager.connected_clients
    return BackendStatus(connected_clients=clients, total_clients=len(clients))


class SendCommandRequest(BaseModel):
    client_id: str | None = None
    action: str
    params: dict[str, int | float | str] = {}
    command_type: str | None = None  # If set, use GameCommandMessage


@app.post("/command")
async def send_command(req: SendCommandRequest):
    """Send a HID or game command to a connected client (for manual/debug use)."""
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
        else:
            sent = await session_manager.broadcast_command(cmd)
            return {
                "status": "sent",
                "action": req.action,
                "sent_to": len(sent),
            }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class SetHIDModeRequest(BaseModel):
    client_id: str | None = None
    hid_mode: str  # "combo", "gamepad", "mouse", "keyboard"


@app.post("/hid-mode")
async def set_hid_mode(req: SetHIDModeRequest):
    """Tell client(s) to switch HID profile (gamepad vs combo mouse+keyboard)."""
    valid_modes = {"combo", "gamepad", "mouse", "keyboard"}
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
            return {"status": "sent", "hid_mode": req.hid_mode, "client_id": req.client_id}
        else:
            sent_count = 0
            for cid in session_manager.connected_clients:
                sess = session_manager.get_session(cid)
                if sess is None:
                    continue
                ws = sess.control_websocket or sess.frame_websocket
                if ws is not None:
                    await ws.send_text(msg.model_dump_json())
                    sent_count += 1
            return {"status": "sent", "hid_mode": req.hid_mode, "sent_to": sent_count}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/clients/{client_id}/status")
async def get_client_status(client_id: str):
    """Get the latest status from a connected client."""
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


@app.get("/clients/{client_id}/frame")
async def get_latest_frame(client_id: str):
    """Get the latest frame as JPEG (for debug viewing)."""
    jpeg_bytes, _ = session_manager.get_latest_frame_jpeg(client_id)
    if jpeg_bytes is None:
        frame = session_manager.get_latest_frame(client_id)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame available")
        jpeg_bytes, _, _ = encode_frame(frame, quality=85, max_dimension=0)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


def _pcm_chunk_to_wav(
    pcm_bytes: bytes,
    sample_rate: int,
    channels: int,
    sample_format: str,
) -> bytes:
    """Wrap raw PCM bytes in a WAV container for easy playback/debug."""
    if sample_format.lower() != "s16le":
        raise HTTPException(status_code=415, detail="Unsupported sample format")
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(max(1, channels))
            wav.setsampwidth(2)  # s16le
            wav.setframerate(max(1, sample_rate))
            wav.writeframes(pcm_bytes)
        return buffer.getvalue()


@app.get("/clients/{client_id}/audio")
async def get_latest_audio_chunk(client_id: str):
    """Get latest audio chunk wrapped as a WAV snippet (debug)."""
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


# --- MJPEG Stream + Dashboard --- #TODO: REORG: move to separate file


async def _mjpeg_generator(client_id: str):
    """Yield JPEG frames as a multipart MJPEG stream."""
    last_sequence = -1
    gone_count = 0
    while True:
        session = session_manager.get_session(client_id)
        if session is None:
            gone_count += 1
            # Wait up to 30s for client to reconnect before giving up
            if gone_count > 300:
                return
            await asyncio.sleep(0.1)
            continue
        gone_count = 0
        jpeg_bytes, sequence = session_manager.get_latest_frame_jpeg(client_id)
        if jpeg_bytes is not None and sequence != last_sequence:
            last_sequence = sequence
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n"
                + jpeg_bytes + b"\r\n"
            )
        else:
            await asyncio.sleep(0.01)


@app.get("/stream/{client_id}")
async def stream_frames(client_id: str):
    """MJPEG stream of frames from a connected client."""
    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return StreamingResponse(
        _mjpeg_generator(client_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>ChromaCatch-Go Dashboard</title>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }
        h1 { color: #e94560; }
        .client { border: 1px solid #333; border-radius: 8px; padding: 16px; margin: 16px 0; background: #16213e; }
        .client h2 { margin-top: 0; color: #4ecca3; }
        .status { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; margin: 8px 0; }
        .status-item { background: #0f3460; padding: 8px 12px; border-radius: 4px; }
        .status-item .label { font-size: 0.8em; color: #888; }
        .status-item .value { font-size: 1.1em; font-weight: bold; }
        .ok { color: #4ecca3; }
        .fail { color: #e94560; }
        .stream-container { position: relative; display: inline-block; }
        .stream { max-width: 100%; border-radius: 4px; background: #000; }
        .no-clients { color: #888; font-style: italic; padding: 40px; text-align: center; }
    </style>
</head>
<body>
    <h1>ChromaCatch-Go</h1>
    <div id="clients"><div class="no-clients">Loading...</div></div>
    <script>
        // Track which clients have active streams to avoid destroying them on refresh
        let activeClients = new Set();

        function reconnectStream(clientId) {
            const imgEl = document.getElementById('mjpeg-' + clientId);
            if (!imgEl) return;
            // Cache-busting param forces a fresh MJPEG connection
            imgEl.src = '/stream/' + clientId + '?t=' + Date.now();
        }

        function buildStatusHtml(s) {
            if (s.airplay_running === undefined) {
                return '<div class="status-item"><span class="label">Status</span><br><span class="value">Waiting for report...</span></div>';
            }
            return `
                <div class="status-item"><span class="label">AirPlay</span><br>
                    <span class="value ${s.airplay_running ? 'ok' : 'fail'}">${s.airplay_running ? 'Running' : 'Stopped'}</span></div>
                <div class="status-item"><span class="label">ESP32</span><br>
                    <span class="value ${s.esp32_reachable ? 'ok' : 'fail'}">${s.esp32_reachable ? 'Reachable' : 'Unreachable'}</span></div>
                <div class="status-item"><span class="label">ESP32 BLE</span><br>
                    <span class="value ${s.esp32_ble_connected ? 'ok' : 'fail'}">${s.esp32_ble_connected ? 'Connected' : 'Disconnected'}</span></div>
                <div class="status-item"><span class="label">Frames Sent</span><br>
                    <span class="value">${s.frames_sent || 0}</span></div>
                <div class="status-item"><span class="label">Transport</span><br>
                    <span class="value">${s.transport_mode || s.capture_source || 'websocket'}</span></div>
                <div class="status-item"><span class="label">Control WS</span><br>
                    <span class="value ${s.control_channel_connected ? 'ok' : 'fail'}">${s.control_channel_connected ? 'Connected' : 'Disconnected'}</span></div>
                <div class="status-item"><span class="label">Cmd Ack RTT</span><br>
                    <span class="value">${s.last_command_rtt_ms ? Math.round(s.last_command_rtt_ms) + ' ms' : 'n/a'}</span></div>
                <div class="status-item"><span class="label">Audio Chunks</span><br>
                    <span class="value">${s.audio_chunks_sent || 0}</span></div>
                <div class="status-item"><span class="label">Audio Source</span><br>
                    <span class="value">${s.audio_source || 'n/a'}</span></div>
                <div class="status-item"><span class="label">Frame Latency</span><br>
                    <span class="value">${s.backend_frame_latency_ms ? Math.round(s.backend_frame_latency_ms) + ' ms' : 'n/a'}</span></div>
                <div class="status-item"><span class="label">Uptime</span><br>
                    <span class="value">${Math.floor((s.uptime_seconds || 0) / 60)}m ${Math.floor((s.uptime_seconds || 0) % 60)}s</span></div>`;
        }

        async function refresh() {
            try {
                const resp = await fetch('/status');
                const data = await resp.json();
                const container = document.getElementById('clients');
                const currentClients = new Set(data.connected_clients);

                if (data.total_clients === 0) {
                    container.innerHTML = '<div class="no-clients">No clients connected. Start the airplay client to begin.</div>';
                    activeClients.clear();
                    return;
                }

                // Remove clients that disconnected
                for (const old of activeClients) {
                    if (!currentClients.has(old)) {
                        const el = document.getElementById('client-' + old);
                        if (el) el.remove();
                        activeClients.delete(old);
                    }
                }

                for (const clientId of data.connected_clients) {
                    // Update status for existing clients without touching the stream
                    let statusEl = document.getElementById('status-' + clientId);
                    if (statusEl) {
                        try {
                            const sResp = await fetch('/clients/' + clientId + '/status');
                            const s = await sResp.json();
                            statusEl.innerHTML = buildStatusHtml(s);
                        } catch(e) {
                            statusEl.innerHTML = '<div class="status-item"><span class="label">Status</span><br><span class="value fail">Error loading</span></div>';
                        }
                        continue;
                    }

                    // New client — create full layout
                    let statusHtml = '';
                    try {
                        const sResp = await fetch('/clients/' + clientId + '/status');
                        const s = await sResp.json();
                        statusHtml = buildStatusHtml(s);
                    } catch(e) {
                        statusHtml = '<div class="status-item"><span class="label">Status</span><br><span class="value fail">Error loading</span></div>';
                    }

                    // Remove "no clients" placeholder if present
                    const placeholder = container.querySelector('.no-clients');
                    if (placeholder) placeholder.remove();

                    const div = document.createElement('div');
                    div.className = 'client';
                    div.id = 'client-' + clientId;
                    div.innerHTML = `
                        <h2>Client: ${clientId}</h2>
                        <div id="status-${clientId}" class="status">${statusHtml}</div>
                        <h3>Live Stream</h3>
                        <div class="stream-container">
                            <img id="mjpeg-${clientId}" class="stream" src="/stream/${clientId}?t=${Date.now()}" alt="Waiting for frames..." width="640"
                                 onerror="setTimeout(() => reconnectStream('${clientId}'), 2000)">
                        </div>`;
                    container.appendChild(div);
                    activeClients.add(clientId);
                }
            } catch(e) {
                document.getElementById('clients').innerHTML = '<div class="no-clients">Error: ' + e.message + '</div>';
            }
        }
        refresh();
        setInterval(refresh, 5000);
    </script>
</body>
</html>"""


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Browser dashboard showing connected clients and their frame streams."""
    return HTMLResponse(content=DASHBOARD_HTML)
