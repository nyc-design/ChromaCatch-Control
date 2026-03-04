"""Automation-facing API router.

This namespace is designed for external automation repos that need:
- client discovery
- live frame access
- CV operations on latest frames
- low-latency command submission over WebSocket
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.app_state import session_manager
from backend.config import backend_settings
from shared.frame_codec import encode_frame
from shared.messages import GameCommandMessage, HIDCommandMessage, SetHIDModeMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automation/v1", tags=["automation-api"])


class AutomationCommandRequest(BaseModel):
    client_id: str | None = None
    action: str
    params: dict[str, int | float | str] = Field(default_factory=dict)
    command_type: str | None = None


class TemplateMatchRequest(BaseModel):
    client_id: str
    template_base64: str
    threshold: float = 0.9
    max_results: int = 10
    grayscale: bool = True


class TemplateMatchResult(BaseModel):
    x: int
    y: int
    width: int
    height: int
    score: float


class TemplateMatchResponse(BaseModel):
    client_id: str
    frame_width: int
    frame_height: int
    match_count: int
    matches: list[TemplateMatchResult]


@dataclass
class _MatchPoint:
    x: int
    y: int
    score: float


def _resolve_token_from_websocket(websocket: WebSocket, api_key: str | None) -> str | None:
    auth_header = websocket.headers.get("authorization", "")
    return api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )


def _validate_automation_token(token: str | None) -> bool:
    if backend_settings.automation_api_key:
        return token == backend_settings.automation_api_key
    if backend_settings.api_key:
        return token == backend_settings.api_key
    return True


def _decode_template_image(template_base64: str, grayscale: bool) -> np.ndarray:
    raw = template_base64
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]

    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid template_base64 payload") from exc

    np_buf = np.frombuffer(decoded, dtype=np.uint8)
    flag = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
    template = cv2.imdecode(np_buf, flag)
    if template is None:
        raise HTTPException(status_code=400, detail="Template image decode failed")
    return template


def _find_template_matches(
    frame: np.ndarray,
    template: np.ndarray,
    threshold: float,
    max_results: int,
    grayscale: bool,
) -> list[TemplateMatchResult]:
    working_frame = frame
    if grayscale and frame.ndim == 3:
        working_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    if template.shape[0] > working_frame.shape[0] or template.shape[1] > working_frame.shape[1]:
        raise HTTPException(status_code=400, detail="Template is larger than frame")

    result = cv2.matchTemplate(working_frame, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(result >= threshold)
    found = [_MatchPoint(x=int(x), y=int(y), score=float(result[y, x])) for y, x in zip(ys, xs)]

    # Keep strongest matches first and lightly suppress close duplicates.
    found.sort(key=lambda m: m.score, reverse=True)
    accepted: list[_MatchPoint] = []
    suppression_radius = max(6, min(template.shape[0], template.shape[1]) // 8)

    for candidate in found:
        is_duplicate = any(
            abs(candidate.x - existing.x) <= suppression_radius
            and abs(candidate.y - existing.y) <= suppression_radius
            for existing in accepted
        )
        if not is_duplicate:
            accepted.append(candidate)
        if len(accepted) >= max_results:
            break

    width = int(template.shape[1])
    height = int(template.shape[0])
    return [
        TemplateMatchResult(x=m.x, y=m.y, width=width, height=height, score=m.score)
        for m in accepted
    ]


async def _send_command_internal(req: AutomationCommandRequest) -> dict[str, Any]:
    has_str_params = any(isinstance(v, str) for v in req.params.values())
    if req.command_type or has_str_params:
        cmd = GameCommandMessage(
            command_type=req.command_type or "keyboard",
            action=req.action,
            params=req.params,
        )
    else:
        cmd = HIDCommandMessage(action=req.action, params=req.params)

    if req.client_id:
        sent_cmd = await session_manager.send_command(req.client_id, cmd)
        return {
            "status": "sent",
            "client_id": req.client_id,
            "action": req.action,
            "command_id": sent_cmd.command_id,
            "command_sequence": sent_cmd.command_sequence,
        }

    sent = await session_manager.broadcast_command(cmd)
    return {"status": "sent", "action": req.action, "sent_to": len(sent)}


@router.get("/clients")
async def list_clients_for_automation():
    clients = session_manager.connected_clients
    return {
        "connected_clients": clients,
        "total_clients": len(clients),
    }


@router.get("/clients/{client_id}/frame")
async def get_latest_frame(client_id: str):
    jpeg_bytes, _ = session_manager.get_latest_frame_jpeg(client_id)
    if jpeg_bytes is None:
        frame = session_manager.get_latest_frame(client_id)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame available")
        jpeg_bytes, _, _ = encode_frame(frame, quality=85, max_dimension=0)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


@router.get("/clients/{client_id}/stream")
async def get_stream_url(client_id: str):
    if session_manager.get_session(client_id) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return {
        "stream_path": f"/stream/{client_id}",
        "stream_mjpeg_url": f"/stream/{client_id}",
    }


@router.post("/commands")
async def send_command(req: AutomationCommandRequest):
    try:
        return await _send_command_internal(req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cv/template-match", response_model=TemplateMatchResponse)
async def cv_template_match(req: TemplateMatchRequest):
    frame = session_manager.get_latest_frame(req.client_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="No frame available for client")

    template = _decode_template_image(req.template_base64, grayscale=req.grayscale)
    matches = _find_template_matches(
        frame=frame,
        template=template,
        threshold=max(0.0, min(1.0, req.threshold)),
        max_results=max(1, min(100, req.max_results)),
        grayscale=req.grayscale,
    )

    return TemplateMatchResponse(
        client_id=req.client_id,
        frame_width=int(frame.shape[1]),
        frame_height=int(frame.shape[0]),
        match_count=len(matches),
        matches=matches,
    )


@router.websocket("/ws/commands")
async def automation_command_websocket(
    websocket: WebSocket,
    api_key: str = Query(default=None),
):
    token = _resolve_token_from_websocket(websocket, api_key)
    if not _validate_automation_token(token):
        await websocket.close(code=4003, reason="Unauthorized")
        return

    await websocket.accept()
    await websocket.send_text(
        json.dumps(
            {
                "type": "hello",
                "role": "automation-api",
                "connected_clients": session_manager.connected_clients,
            }
        )
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "Invalid JSON payload"})
                )
                continue

            msg_type = str(payload.get("type", "")).strip().lower()

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if msg_type == "command":
                req = AutomationCommandRequest.model_validate(payload)
                try:
                    result = await _send_command_internal(req)
                    await websocket.send_text(
                        json.dumps({"type": "command_ack", **result})
                    )
                except ValueError as exc:
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": str(exc)})
                    )
                continue

            if msg_type == "set_hid_mode":
                mode = str(payload.get("hid_mode", "")).strip()
                client_id = payload.get("client_id")
                if not mode:
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": "Missing hid_mode"})
                    )
                    continue

                msg = SetHIDModeMessage(hid_mode=mode)
                try:
                    if client_id:
                        session = session_manager.get_session(str(client_id))
                        if session is None:
                            raise ValueError(f"No client connected with id: {client_id}")
                        ws = session.control_websocket or session.frame_websocket
                        if ws is None:
                            raise ValueError(f"No active transport for client: {client_id}")
                        await ws.send_text(msg.model_dump_json())
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "hid_mode_ack",
                                    "status": "sent",
                                    "client_id": client_id,
                                    "hid_mode": mode,
                                }
                            )
                        )
                    else:
                        sent_to = 0
                        for cid in session_manager.connected_clients:
                            session = session_manager.get_session(cid)
                            if session is None:
                                continue
                            ws = session.control_websocket or session.frame_websocket
                            if ws is not None:
                                await ws.send_text(msg.model_dump_json())
                                sent_to += 1
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "hid_mode_ack",
                                    "status": "sent",
                                    "hid_mode": mode,
                                    "sent_to": sent_to,
                                }
                            )
                        )
                except ValueError as exc:
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": str(exc)})
                    )
                continue

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "detail": "Unknown message type. Expected ping|command|set_hid_mode",
                    }
                )
            )

    except WebSocketDisconnect:
        logger.info("Automation command WS disconnected")


@router.get("/stream/{client_id}")
async def stream_frames_alias(client_id: str):
    """Alias for MJPEG stream under automation API namespace."""
    async def _mjpeg_generator(cid: str):
        last_sequence = -1
        gone_count = 0
        while True:
            session = session_manager.get_session(cid)
            if session is None:
                gone_count += 1
                if gone_count > 300:
                    return
                await asyncio.sleep(0.1)
                continue
            gone_count = 0
            jpeg_bytes, sequence = session_manager.get_latest_frame_jpeg(cid)
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

    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return StreamingResponse(
        _mjpeg_generator(client_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
