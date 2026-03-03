"""Tests for the CV visual tester endpoints."""

import asyncio
import json
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app, session_manager
from backend.cv_tester import _test_config, _draw_overlay


@pytest.fixture(autouse=True)
def reset_config():
    """Reset test config between tests."""
    _test_config.update({
        "tool": None,
        "reference": None,
        "reference_name": None,
        "threshold": 0.5,
        "params": {},
        "region": None,
    })
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def red_jpeg():
    """A solid red JPEG image as bytes."""
    img = np.full((100, 100, 3), [0, 0, 255], dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


@pytest.fixture
def registered_client():
    """Register a test client with a frame available."""
    ws = AsyncMock()
    frame = np.full((100, 100, 3), [0, 0, 255], dtype=np.uint8)
    cid = "test-cv-client"
    asyncio.get_event_loop().run_until_complete(session_manager.register(cid, ws))
    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    session_manager.update_frame(cid, frame, jpeg.tobytes())
    yield cid
    asyncio.get_event_loop().run_until_complete(session_manager.unregister(cid))


class TestCvTesterDashboard:
    def test_dashboard_page(self, client):
        resp = client.get("/test/cv")
        assert resp.status_code == 200
        assert "CV Tool Tester" in resp.text
        assert "toolSelect" in resp.text

    def test_tools_list(self, client):
        resp = client.get("/test/cv/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "locate" in data
        assert "confirm" in data
        assert "extract" in data
        assert data["total"] == 25
        assert "locate_color" in data["locate"]
        assert "brightness_check" in data["confirm"]
        assert "read_text" in data["extract"]


class TestCvTesterConfig:
    def test_set_config(self, client):
        resp = client.post("/test/cv/config?tool=brightness_check&threshold=0.3&params=%7B%7D")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tool"] == "brightness_check"
        assert _test_config["tool"] == "brightness_check"
        assert _test_config["threshold"] == 0.3

    def test_set_config_unknown_tool(self, client):
        resp = client.post("/test/cv/config?tool=nonexistent&threshold=0.5")
        assert resp.status_code == 400
        assert "Unknown tool" in resp.json()["detail"]

    def test_set_config_with_params(self, client):
        params = json.dumps({"hsv_lower": [0, 100, 100], "hsv_upper": [10, 255, 255]})
        resp = client.post(f"/test/cv/config?tool=locate_color&threshold=0.5&params={params}")
        assert resp.status_code == 200
        assert _test_config["params"]["hsv_lower"] == [0, 100, 100]

    def test_set_config_bad_reference_path(self, client):
        resp = client.post("/test/cv/config?tool=brightness_check&reference_path=/nonexistent.png")
        assert resp.status_code == 400
        assert "Cannot load" in resp.json()["detail"]

    def test_get_config(self, client):
        _test_config["tool"] = "locate_color"
        _test_config["threshold"] = 0.7
        resp = client.get("/test/cv/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tool"] == "locate_color"
        assert data["threshold"] == 0.7
        assert "available_tools" in data


class TestCvTesterReference:
    def test_upload_reference(self, client, red_jpeg):
        resp = client.post("/test/cv/reference", files={"file": ("red.jpg", red_jpeg, "image/jpeg")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "red.jpg"
        assert _test_config["reference"] is not None
        assert _test_config["reference"].shape[0] > 0

    def test_upload_invalid_reference(self, client):
        resp = client.post("/test/cv/reference", files={"file": ("bad.txt", b"not an image", "text/plain")})
        assert resp.status_code == 400


class TestCvTesterRun:
    def test_run_single_no_tool(self, client, red_jpeg):
        resp = client.post("/test/cv/run", files={"file": ("frame.jpg", red_jpeg, "image/jpeg")})
        assert resp.status_code == 400

    def test_run_single_with_tool(self, client, red_jpeg):
        resp = client.post(
            "/test/cv/run?tool=brightness_check&threshold=0.0",
            files={"file": ("frame.jpg", red_jpeg, "image/jpeg")},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        result = json.loads(resp.headers["X-CV-Result"])
        assert result["tool"] == "brightness_check"
        assert 0.0 <= result["score"] <= 1.0


class TestCvTesterStream:
    def test_stream_no_client(self, client):
        _test_config["tool"] = "brightness_check"
        resp = client.get("/test/cv/stream/nonexistent")
        assert resp.status_code == 404

    def test_stream_no_tool_configured(self, client, registered_client):
        resp = client.get(f"/test/cv/stream/{registered_client}")
        assert resp.status_code == 400
        assert "No tool configured" in resp.json()["detail"]


class TestDrawOverlay:
    def test_draw_matches(self):
        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        result = {
            "score": 0.85,
            "match": True,
            "threshold": 0.5,
            "details": {
                "matches": [
                    {"bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, "confidence": 0.9},
                    {"bbox": {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}, "confidence": 0.6},
                ],
            },
        }
        annotated = _draw_overlay(frame, result, "locate_template", 15.3)
        assert annotated.shape == frame.shape
        # Should not be all black — overlay was drawn
        assert annotated.sum() > 0
        # Original frame not modified
        assert frame.sum() == 0

    def test_draw_no_matches(self):
        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        result = {"score": 0.0, "match": False, "threshold": 0.5, "details": {}}
        annotated = _draw_overlay(frame, result, "brightness_check", 5.0)
        assert annotated.shape == frame.shape
        # Info bar should still be drawn
        assert annotated.sum() > 0
