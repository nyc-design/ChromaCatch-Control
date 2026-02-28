"""Desktop/screen capture frame source."""

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


def _parse_region(region: str) -> dict[str, int] | None:
    if not region:
        return None
    parts = [p.strip() for p in region.split(",")]
    if len(parts) != 4:
        raise ValueError("CC_CLIENT_SCREEN_REGION must be 'x,y,width,height'")
    x, y, w, h = [int(v) for v in parts]
    return {"left": x, "top": y, "width": w, "height": h}


class ScreenFrameSource(FrameSource):
    """Frame source capturing the desktop via mss."""

    def __init__(self) -> None:
        self._fps = max(1, settings.capture_fps)
        self._monitor = max(1, settings.screen_monitor)
        self._region = _parse_region(settings.screen_region)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._running = False
        self._sct = None

    @property
    def source_name(self) -> str:
        return "screen"

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _capture_loop(self) -> None:
        import mss

        assert self._sct is not None
        interval = 1.0 / self._fps
        while self._running:
            t0 = time.time()
            if self._region is not None:
                bbox = self._region
            else:
                bbox = self._sct.monitors[self._monitor]
            shot = self._sct.grab(bbox)
            frame = np.array(shot)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            self._queue.put(frame)
            elapsed = time.time() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    def start(self) -> None:
        if self._running:
            return
        try:
            import mss  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "Screen capture source requires 'mss'. Install with: pip install mss"
            ) from e

        import mss

        self._sct = mss.mss()
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(
            "Screen source started (monitor=%d, region=%s, fps=%d)",
            self._monitor,
            self._region,
            self._fps,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
        logger.info("Screen source stopped")

    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

