"""Sync/async clients for the ChromaCatch Control API."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import websockets
from websockets.client import WebSocketClientProtocol

from .models import CommandResult, ConnectedClients, TemplateMatchResponse


class ChromaCatchControlClient:
    """Synchronous REST SDK wrapper for the control backend."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def list_clients(self) -> ConnectedClients:
        with httpx.Client(timeout=self.timeout_seconds, headers=self._headers()) as client:
            resp = client.get(f"{self.base_url}/api/automation/v1/clients")
            resp.raise_for_status()
            return ConnectedClients.model_validate(resp.json())

    def get_frame_jpeg(self, client_id: str) -> bytes:
        with httpx.Client(timeout=self.timeout_seconds, headers=self._headers()) as client:
            resp = client.get(f"{self.base_url}/api/automation/v1/clients/{client_id}/frame")
            resp.raise_for_status()
            return resp.content

    def send_command(
        self,
        action: str,
        params: dict[str, int | float | str] | None = None,
        *,
        client_id: str | None = None,
        command_type: str | None = None,
    ) -> CommandResult:
        payload: dict[str, Any] = {
            "action": action,
            "params": params or {},
        }
        if client_id:
            payload["client_id"] = client_id
        if command_type:
            payload["command_type"] = command_type

        with httpx.Client(timeout=self.timeout_seconds, headers=self._headers()) as client:
            resp = client.post(f"{self.base_url}/api/automation/v1/commands", json=payload)
            resp.raise_for_status()
            return CommandResult.model_validate(resp.json())

    def template_match(
        self,
        *,
        client_id: str,
        template_bytes: bytes,
        threshold: float = 0.9,
        max_results: int = 10,
        grayscale: bool = True,
    ) -> TemplateMatchResponse:
        payload = {
            "client_id": client_id,
            "template_base64": base64.b64encode(template_bytes).decode("utf-8"),
            "threshold": threshold,
            "max_results": max_results,
            "grayscale": grayscale,
        }
        with httpx.Client(timeout=self.timeout_seconds, headers=self._headers()) as client:
            resp = client.post(
                f"{self.base_url}/api/automation/v1/cv/template-match",
                json=payload,
            )
            resp.raise_for_status()
            return TemplateMatchResponse.model_validate(resp.json())


class ChromaCatchAutomationSocket:
    """Async WebSocket helper for low-latency automation commands."""

    def __init__(self, ws_url: str, api_key: str | None = None) -> None:
        self.ws_url = ws_url
        self.api_key = api_key
        self._ws: WebSocketClientProtocol | None = None

    async def connect(self) -> dict[str, Any]:
        headers = []
        if self.api_key:
            headers.append(("Authorization", f"Bearer {self.api_key}"))
        self._ws = await websockets.connect(self.ws_url, extra_headers=headers)
        hello_raw = await self._ws.recv()
        return json.loads(hello_raw)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def ping(self) -> dict[str, Any]:
        return await self._send_and_recv({"type": "ping"})

    async def send_command(
        self,
        *,
        action: str,
        params: dict[str, int | float | str] | None = None,
        client_id: str | None = None,
        command_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "command",
            "action": action,
            "params": params or {},
        }
        if client_id:
            payload["client_id"] = client_id
        if command_type:
            payload["command_type"] = command_type
        return await self._send_and_recv(payload)

    async def set_hid_mode(self, hid_mode: str, client_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "set_hid_mode", "hid_mode": hid_mode}
        if client_id:
            payload["client_id"] = client_id
        return await self._send_and_recv(payload)

    async def _send_and_recv(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected. Call connect() first.")
        await self._ws.send(json.dumps(payload))
        raw = await self._ws.recv()
        return json.loads(raw)
