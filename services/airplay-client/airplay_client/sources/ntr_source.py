"""NTR frame source — captures video from modded Nintendo 3DS."""

from __future__ import annotations

import logging
import socket
import threading
import time

import cv2
import numpy as np

from airplay_client.config import client_settings as settings
from airplay_client.sources.base import FrameSource

logger = logging.getLogger(__name__)

# NTR UDP packet header: 4 bytes
# byte 0: frame_id (wrapping)
# byte 1: is_top (1 = top screen, 0 = bottom)
# byte 2: packet_index within frame
# byte 3: total_packets in frame
_NTR_HEADER_SIZE = 4
_NTR_MAX_PACKET_SIZE = 1448


class NTRFrameSource(FrameSource):
    """Captures frames from a modded 3DS via NTR UDP JPEG stream."""

    def __init__(self, host: str | None = None, port: int | None = None):
        self._host = host or settings.ntr_host or "0.0.0.0"
        self._port = port or settings.ntr_port or 8000
        self._running = False
        self._latest_frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    @property
    def source_name(self) -> str:
        return "ntr"

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        logger.info("Starting NTR source on %s:%d", self._host, self._port)
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()

    def _receive_loop(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(1.0)
        self._sock.bind((self._host, self._port))
        logger.info("NTR UDP socket listening on %s:%d", self._host, self._port)

        # Reassembly buffer: frame_id -> {packet_index: data}
        current_frame_id: int | None = None
        frame_packets: dict[int, bytes] = {}
        expected_total = 0

        while self._running:
            try:
                data, addr = self._sock.recvfrom(_NTR_MAX_PACKET_SIZE + _NTR_HEADER_SIZE)
                if len(data) < _NTR_HEADER_SIZE:
                    continue

                frame_id = data[0]
                is_top = data[1]
                packet_index = data[2]
                total_packets = data[3]
                payload = data[_NTR_HEADER_SIZE:]

                # Only capture top screen
                if not is_top:
                    continue

                # New frame — reset buffer
                if frame_id != current_frame_id:
                    current_frame_id = frame_id
                    frame_packets = {}
                    expected_total = total_packets

                frame_packets[packet_index] = payload

                # Frame complete?
                if len(frame_packets) >= expected_total and expected_total > 0:
                    jpeg_data = b"".join(
                        frame_packets[i]
                        for i in range(expected_total)
                        if i in frame_packets
                    )
                    frame = cv2.imdecode(
                        np.frombuffer(jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if frame is not None:
                        with self._lock:
                            self._latest_frame = frame
                    current_frame_id = None
                    frame_packets = {}

            except socket.timeout:
                continue
            except Exception:
                logger.exception("NTR receive error")
                time.sleep(0.1)

        if self._sock:
            self._sock.close()
            self._sock = None

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        logger.info("NTR source stopped")

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest_frame is not None:
                    return self._latest_frame.copy()
            time.sleep(0.01)
        return None
