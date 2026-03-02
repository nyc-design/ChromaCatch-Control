"""FastAPI application for ChromaCatch-Go location service.

Standalone service for GPS coordinate management. iOS apps connect via WebSocket
to receive coordinates. The main backend's orchestrator (or manual API calls)
pushes coordinates via POST /location.
"""

import logging

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from location_service.config import location_settings
from location_service.session_manager import LocationSessionManager
from shared.constants import MessageType, setup_logging
from shared.messages import LocationStatusMessage, LocationUpdateMessage, parse_message

setup_logging()
logger = logging.getLogger(__name__)

session_manager = LocationSessionManager()

# In-memory store for current spoofed location per client
_current_locations: dict[str, LocationUpdateMessage] = {}

app = FastAPI(
    title="ChromaCatch-Go Location Service",
    description="GPS coordinate management for location spoofing",
    version="0.1.0",
)


# --- WebSocket Endpoint ---


@app.websocket("/ws/location")
async def websocket_location(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """WebSocket endpoint for iOS apps to receive location updates."""
    # Auth check
    auth_header = websocket.headers.get("authorization", "")
    token = api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )
    if location_settings.api_key and token != location_settings.api_key:
        await websocket.close(code=4003, reason="Unauthorized")
        return

    await websocket.accept()
    cid = client_id or f"ios-{id(websocket)}"
    await session_manager.register(cid, websocket)
    logger.info("Location WS connected: %s", cid)

    try:
        while True:
            text = await websocket.receive_text()
            # Handle ping/pong, location_status, and other messages from client
            try:
                msg = parse_message(text)
                if msg.type == MessageType.PING:
                    from shared.messages import HeartbeatPong

                    await websocket.send_text(HeartbeatPong().model_dump_json())
                elif msg.type == MessageType.LOCATION_STATUS:
                    assert isinstance(msg, LocationStatusMessage)
                    session_manager.update_gps_status(cid, msg)
            except Exception:
                logger.debug("Unhandled location WS message: %s", text[:100])
    except WebSocketDisconnect:
        logger.info("Location WS disconnected: %s", cid)
    except Exception as e:
        logger.error("Location WS error for %s: %s", cid, e)
    finally:
        await session_manager.unregister(cid)


# --- REST Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok", "role": "location-service"}


class SendLocationRequest(BaseModel):
    client_id: str | None = None
    latitude: float
    longitude: float
    altitude: float = 10.0
    speed_knots: float = 0.0
    heading: float = 0.0


@app.post("/location")
async def send_location(req: SendLocationRequest):
    """Send GPS coordinates to connected iOS app(s) for dongle spoofing."""
    msg = LocationUpdateMessage(
        latitude=req.latitude,
        longitude=req.longitude,
        altitude=req.altitude,
        speed_knots=req.speed_knots,
        heading=req.heading,
    )
    try:
        if req.client_id:
            await session_manager.send_location(req.client_id, msg)
            _current_locations[req.client_id] = msg
            return {"status": "sent", "client_id": req.client_id}
        else:
            sent = await session_manager.broadcast_location(msg)
            for cid in session_manager.connected_clients:
                _current_locations[cid] = msg
            return {"status": "sent", "sent_to": sent}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _build_location_response(cid: str, loc: LocationUpdateMessage) -> dict:
    """Build a location response with GPS verification data if available."""
    result = loc.model_dump()
    gps_status = session_manager.get_gps_status(cid)
    if gps_status is not None:
        result["gps_verification"] = {
            "gps_accurate": gps_status.gps_accurate,
            "gps_drift_meters": gps_status.gps_drift_meters,
            "ios_reported_latitude": gps_status.ios_reported_latitude,
            "ios_reported_longitude": gps_status.ios_reported_longitude,
        }
    else:
        result["gps_verification"] = None
    return result


@app.get("/location")
async def get_location(client_id: str = Query(default=None)):
    """Get the most recently sent spoofed location + GPS verification status."""
    if client_id:
        loc = _current_locations.get(client_id)
        if loc is None:
            raise HTTPException(status_code=404, detail="No location set for client")
        return _build_location_response(client_id, loc)
    return {
        cid: _build_location_response(cid, loc)
        for cid, loc in _current_locations.items()
    }
