"""AirPlay-backed frame source (UxPlay + local RTP capture)."""

from __future__ import annotations

import logging

import numpy as np

from airplay_client.capture.airplay_manager import AirPlayManager
from airplay_client.capture.frame_capture import FrameCapture
from airplay_client.sources.base import FrameSource

logger = logging.getLogger(__name__)


class AirPlayFrameSource(FrameSource):
    """Frame source using UxPlay RTP forwarding."""

    def __init__(self) -> None:
        self._airplay = AirPlayManager()
        self._capture = FrameCapture()

    @property
    def source_name(self) -> str:
        return "airplay"

    @property
    def is_running(self) -> bool:
        return self._capture.is_running and self._airplay.is_running

    @property
    def airplay_running(self) -> bool:
        return self._airplay.is_running

    @property
    def airplay_pid(self) -> int | None:
        return self._airplay.pid

    def start(self) -> None:
        # Capture must start first (SPS/PPS/IDR timing on connect).
        self._capture.start()
        self._airplay.start()
        logger.info("AirPlay frame source started")

    def stop(self) -> None:
        self._capture.stop()
        self._airplay.stop()
        logger.info("AirPlay frame source stopped")

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        return self._capture.get_frame(timeout=timeout)

