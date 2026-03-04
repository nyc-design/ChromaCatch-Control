"""Tests for automation-facing API endpoints."""

import asyncio
import base64
from unittest.mock import AsyncMock

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.main import app, session_manager


class TestAutomationApi:
    def test_clients_endpoint(self):
        client = TestClient(app)
        response = client.get("/api/automation/v1/clients")
        assert response.status_code == 200
        payload = response.json()
        assert "connected_clients" in payload
        assert "total_clients" in payload

    def test_template_match(self):
        ws = AsyncMock()
        loop = asyncio.get_event_loop()
        session = loop.run_until_complete(session_manager.register("auto-cv", ws, channel="frame"))

        frame = np.zeros((120, 120, 3), dtype=np.uint8)
        # Non-constant template (all-255 templates produce unstable normalized scores).
        patch = np.zeros((20, 20, 3), dtype=np.uint8)
        patch[:, :, 0] = 180
        patch[:, :, 1] = np.tile(np.arange(20, dtype=np.uint8), (20, 1))
        patch[:, :, 2] = np.tile(np.arange(20, dtype=np.uint8).reshape(20, 1), (1, 20))
        frame[35:55, 40:60] = patch
        session.latest_frame = frame

        template = frame[35:55, 40:60]
        ok, template_buf = cv2.imencode(".png", template)
        assert ok
        template_b64 = base64.b64encode(template_buf.tobytes()).decode("utf-8")

        try:
            client = TestClient(app)
            response = client.post(
                "/api/automation/v1/cv/template-match",
                json={
                    "client_id": "auto-cv",
                    "template_base64": template_b64,
                    "threshold": 0.95,
                    "max_results": 5,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["client_id"] == "auto-cv"
            assert payload["match_count"] >= 1
        finally:
            loop.run_until_complete(session_manager.unregister("auto-cv", channel="frame"))

    def test_ws_commands_ping_and_command(self):
        client = TestClient(app)
        with client.websocket_connect("/api/automation/v1/ws/commands") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"

            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"

            ws.send_json({"type": "command", "action": "click", "params": {"x": 1, "y": 2}})
            ack = ws.receive_json()
            assert ack["type"] == "command_ack"
            assert ack["status"] == "sent"
            assert ack["sent_to"] == 0
