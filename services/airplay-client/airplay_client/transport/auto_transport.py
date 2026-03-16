"""Auto transport — probes UDP connectivity and selects the best transport.

Tries RTP+FEC (UDP) first. If UDP is blocked (corporate firewall, restrictive NAT),
falls back to AU WebSocket (TCP). Periodically re-probes to upgrade back to UDP.
"""

from __future__ import annotations

import asyncio
import logging
import socket

from airplay_client.capture_provider.base import CaptureProvider
from airplay_client.config import client_settings as settings
from airplay_client.transport.au_ws_transport import AUWebSocketTransport
from airplay_client.transport.base import MediaTransport
from airplay_client.transport.unified_rtp_fec import UnifiedRTPFECTransport
from airplay_client.ws_client import WebSocketClient

logger = logging.getLogger(__name__)

_UDP_PROBE_TIMEOUT = 3.0  # seconds to wait for UDP probe


async def _probe_udp(host: str, port: int, timeout: float = _UDP_PROBE_TIMEOUT) -> bool:
    """Test if we can send/receive UDP to the backend.

    Sends a small probe packet. If the backend has a RTP+FEC receiver
    running, it will silently consume it. We just check that the send
    doesn't error and that the local socket can be created.

    For stricter probing (actual response), the backend would need a
    UDP echo endpoint — but for most cases, outbound UDP sendto()
    succeeding is sufficient (firewalls that block UDP will fail here).
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.setblocking(False)
        # Try sending a small probe — if the network blocks UDP entirely,
        # this will raise (e.g., ICMP unreachable → ConnectionRefusedError)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sock.sendto, b"\x00", (host, port))
        # Small delay to let ICMP unreachable come back
        await asyncio.sleep(0.5)
        # Try sending again — if first one triggered ICMP unreachable,
        # this may raise ConnectionRefusedError on some platforms
        await loop.run_in_executor(None, sock.sendto, b"\x00", (host, port))
        sock.close()
        logger.debug("UDP probe to %s:%d succeeded", host, port)
        return True
    except Exception as e:
        logger.debug("UDP probe to %s:%d failed: %s", host, port, e)
        return False


class AutoTransport(MediaTransport):
    """Auto-selects between RTP+FEC (UDP) and AU WebSocket (TCP).

    Probes UDP connectivity on start. If UDP works, uses RTP+FEC.
    If not, falls back to AU WebSocket. Periodically re-probes.
    """

    def __init__(
        self,
        frame_ws: WebSocketClient,
        dest_host: str | None = None,
        dest_port: int | None = None,
        reprobe_interval: float = 60.0,
    ):
        self._frame_ws = frame_ws
        self._dest_host = dest_host or settings.rtp_fec_dest_host or "localhost"
        self._dest_port = dest_port or settings.rtp_fec_dest_port or 7000
        self._reprobe_interval = reprobe_interval
        self._active_transport: MediaTransport | None = None
        self._provider: CaptureProvider | None = None
        self._running = False
        self._reprobe_task: asyncio.Task | None = None
        self._using_udp = False

    async def start(self) -> None:
        raise RuntimeError("AutoTransport requires start_with_provider()")

    async def start_with_provider(self, provider: CaptureProvider) -> None:
        self._provider = provider
        self._running = True

        # Probe UDP
        udp_ok = await _probe_udp(self._dest_host, self._dest_port)

        if udp_ok:
            logger.info("UDP probe succeeded — using RTP+FEC transport")
            self._active_transport = UnifiedRTPFECTransport(
                dest_host=self._dest_host, dest_port=self._dest_port
            )
            self._using_udp = True
        else:
            logger.info("UDP probe failed — falling back to AU WebSocket transport")
            self._active_transport = AUWebSocketTransport(frame_ws=self._frame_ws)
            self._using_udp = False

        await self._active_transport.start_with_provider(provider)

        # Start periodic re-probe if we're on WS (try to upgrade to UDP)
        if not self._using_udp:
            self._reprobe_task = asyncio.create_task(self._reprobe_loop())

    async def _reprobe_loop(self) -> None:
        """Periodically probe UDP and upgrade from WS to RTP+FEC if possible."""
        while self._running:
            await asyncio.sleep(self._reprobe_interval)
            if not self._running or self._using_udp:
                break

            udp_ok = await _probe_udp(self._dest_host, self._dest_port)
            if udp_ok and self._provider:
                logger.info("UDP re-probe succeeded — upgrading to RTP+FEC")
                await self._active_transport.stop()
                self._active_transport = UnifiedRTPFECTransport(
                    dest_host=self._dest_host, dest_port=self._dest_port
                )
                self._using_udp = True
                await self._active_transport.start_with_provider(self._provider)
                break

    async def stop(self) -> None:
        self._running = False
        if self._reprobe_task:
            self._reprobe_task.cancel()
            try:
                await self._reprobe_task
            except asyncio.CancelledError:
                pass
            self._reprobe_task = None
        if self._active_transport:
            await self._active_transport.stop()
            self._active_transport = None

    @property
    def is_connected(self) -> bool:
        return self._active_transport.is_connected if self._active_transport else False

    @property
    def transport_name(self) -> str:
        if self._active_transport:
            return f"auto({self._active_transport.transport_name})"
        return "auto"

    @property
    def frames_sent(self) -> int:
        return self._active_transport.frames_sent if self._active_transport else 0

    @property
    def audio_frames_sent(self) -> int:
        return self._active_transport.audio_frames_sent if self._active_transport else 0

    @property
    def bytes_sent(self) -> int:
        return self._active_transport.bytes_sent if self._active_transport else 0
