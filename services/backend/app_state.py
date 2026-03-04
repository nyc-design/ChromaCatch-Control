"""Shared runtime objects for the backend app.

Keeping these in one module allows multiple routers to import the same
session/transport state without creating circular imports with `main.py`.
"""

from backend.config import backend_settings
from backend.mediamtx_manager import MediaMTXManager
from backend.rtp_fec_receiver import RTPFECReceiver
from backend.rtsp_consumer import RTSPFrameConsumer
from backend.session_manager import SessionManager
from backend.ws_handler import WebSocketHandler

session_manager = SessionManager()
ws_handler = WebSocketHandler(session_manager)
mediamtx_manager = MediaMTXManager()
rtsp_consumer = RTSPFrameConsumer(session_manager)
rtp_fec_receiver = RTPFECReceiver(
    session_manager,
    bind_host=backend_settings.rtp_fec_bind_host,
    bind_port=backend_settings.rtp_fec_bind_port,
    client_id=backend_settings.rtp_fec_client_id,
)
