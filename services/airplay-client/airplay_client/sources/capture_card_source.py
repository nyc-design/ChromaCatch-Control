"""Capture-card / camera based frame source."""

from __future__ import annotations

import logging
import queue
import threading
import time

import cv2
import numpy as np

from airplay_client.config import client_settings as settings
from airplay_client.sources.base import FrameSource

logger = logging.getLogger(__name__)


class CaptureCardFrameSource(FrameSource):
    """Frame source backed by OpenCV VideoCapture device."""

    def __init__(self) -> None:
        self._device = settings.capture_device
        self._width = settings.capture_width
        self._height = settings.capture_height
        self._fps = max(1, settings.capture_fps)

        self._capture: cv2.VideoCapture | None = None
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._running = False

    @property
    def source_name(self) -> str:
        return "capture"

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _capture_loop(self) -> None:
        assert self._capture is not None
        frame_interval = 1.0 / self._fps
        while self._running:
            t0 = time.time()
            ok, frame = self._capture.read()
            if ok and frame is not None:
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                self._queue.put(frame)
            else:
                time.sleep(0.01)

            elapsed = time.time() - t0
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

    def start(self) -> None:
        if self._running:
            return

        device: int | str
        try:
            device = int(self._device)
        except ValueError:
            device = self._device

        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open capture device '{self._device}'")

        if self._width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
        if self._height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._height))
        if self._fps > 0:
            cap.set(cv2.CAP_PROP_FPS, float(self._fps))

        self._capture = cap
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(
            "Capture card source started (device=%s, width=%d, height=%d, fps=%d)",
            self._device,
            self._width,
            self._height,
            self._fps,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._capture:
            self._capture.release()
            self._capture = None
        logger.info("Capture card source stopped")

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

