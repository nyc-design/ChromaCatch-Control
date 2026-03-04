"""Tests for the namespaced client API."""

import asyncio
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.main import app, session_manager


class TestClientApiV1:
    def test_list_clients(self):
        client = TestClient(app)
        response = client.get("/api/client/v1/clients")
        assert response.status_code == 200
        payload = response.json()
        assert "connected_clients" in payload
        assert "total_clients" in payload

    def test_send_command_broadcast_no_clients(self):
        client = TestClient(app)
        response = client.post(
            "/api/client/v1/commands",
            json={"action": "click", "params": {"x": 10, "y": 20}},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "sent"

    def test_hid_mode_to_connected_client(self):
        ws = AsyncMock()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(session_manager.register("client-v1-test", ws, channel="control"))
        try:
            client = TestClient(app)
            response = client.post(
                "/api/client/v1/hid-mode",
                json={"client_id": "client-v1-test", "hid_mode": "gamepad"},
            )
            assert response.status_code == 200
            ws.send_text.assert_called_once()
        finally:
            loop.run_until_complete(session_manager.unregister("client-v1-test", channel="control"))
