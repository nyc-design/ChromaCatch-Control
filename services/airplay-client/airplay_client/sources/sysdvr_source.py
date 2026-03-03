"""SysDVR frame source — captures video from modded Nintendo Switch."""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

from airplay_client.config import client_settings as settings
from airplay_client.sources.base import FrameSource

logger = logging.getLogger(__name__)


class SysDVRFrameSource(FrameSource):
    """Captures frames from a modded Switch via SysDVR RTSP."""

    def __init__(self, url: str | None = None):
        self._url = url or settings.sysdvr_url or "rtsp://192.168.1.50:6666/video"
        self._cap: cv2.VideoCapture | None = None
        self._running = False
        self._latest_frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def source_name(self) -> str:
        return "sysdvr"

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        logger.info("Starting SysDVR source: %s", self._url)
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        while self._running:
            try:
                self._cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
                if not self._cap.isOpened():
                    logger.warning("SysDVR RTSP connection failed, retrying in 2s...")
                    time.sleep(2)
                    continue
                logger.info("SysDVR RTSP connected")
                while self._running and self._cap.isOpened():
                    ret, frame = self._cap.read()
                    if not ret:
                        logger.warning("SysDVR frame read failed, reconnecting...")
                        break
                    with self._lock:
                        self._latest_frame = frame
            except Exception:
                logger.exception("SysDVR capture error")
            finally:
                if self._cap is not None:
                    self._cap.release()
                    self._cap = None
            if self._running:
                time.sleep(1)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        logger.info("SysDVR source stopped")

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest_frame is not None:
                    return self._latest_frame.copy()
            time.sleep(0.01)
        return None
