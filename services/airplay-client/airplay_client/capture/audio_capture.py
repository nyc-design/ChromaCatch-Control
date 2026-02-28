"""Captures AirPlay audio forwarded by UxPlay RTP output."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class AudioCapture:
    """Capture decoded RTP L16 audio chunks via GStreamer CLI."""

    def __init__(self, udp_port: int | None = None):
        self.udp_port = udp_port or settings.airplay_audio_udp_port
        self.sample_rate = settings.audio_sample_rate
        self.channels = settings.audio_channels
        self.chunk_ms = settings.audio_chunk_ms
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=20)
        self._thread: threading.Thread | None = None
        self._running = False
        self._gst_proc: subprocess.Popen | None = None
        self._chunk_dir: str | None = None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @staticmethod
    def _list_audio_files(chunk_dir: str) -> list[tuple[int, str]]:
        files: list[tuple[int, str]] = []
        for path in Path(chunk_dir).glob("audio_*.raw"):
            try:
                idx = int(path.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            files.append((idx, str(path)))
        files.sort(key=lambda x: x[0])
        return files

    @staticmethod
    def _stable_size(path: str, timeout: float = 0.5) -> int:
        end = time.time() + timeout
        prev = -1
        stable = 0
        while time.time() < end:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size > 0 and size == prev:
                stable += 1
                if stable >= 2:
                    return size
            else:
                stable = 0
            prev = size
            time.sleep(0.03)
        return 0

    def _start_gstreamer(self) -> str:
        import tempfile

        gst = shutil.which("gst-launch-1.0")
        if not gst:
            raise RuntimeError("gst-launch-1.0 not found for audio capture")

        chunk_dir = tempfile.mkdtemp(prefix="chromacatch_audio_")
        pattern = os.path.join(chunk_dir, "audio_%06d.raw")

        caps = (
            "application/x-rtp,media=audio,encoding-name=L16,"
            f"clock-rate={self.sample_rate},channels={self.channels},payload=96"
        )
        cmd = [
            gst,
            "-q",
            "udpsrc",
            f"port={self.udp_port}",
            f"caps={caps}",
            "!",
            "rtpL16depay",
            "!",
            "audioconvert",
            "!",
            (
                f"audio/x-raw,format=S16LE,channels={self.channels},"
                f"rate={self.sample_rate}"
            ),
            "!",
            "multifilesink",
            f"location={pattern}",
            "next-file=buffer",
            "max-files=600",
            "sync=false",
            "async=false",
        ]
        logger.info("Starting audio capture pipeline: %s", " ".join(cmd))
        self._gst_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._chunk_dir = chunk_dir
        return chunk_dir

    def _push_chunk(self, chunk: bytes) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
        self._queue.put(chunk)

    def _capture_loop(self) -> None:
        bytes_per_chunk = int(
            (self.sample_rate * self.channels * 2) * (self.chunk_ms / 1000.0)
        )

        chunk_dir = self._start_gstreamer()
        last_idx = -1
        aggregate = bytearray()
        aggregate_started = time.time()

        while self._running:
            if self._gst_proc and self._gst_proc.poll() is not None:
                logger.warning("Audio capture gst exited (rc=%s)", self._gst_proc.returncode)
                break

            files = self._list_audio_files(chunk_dir)
            newer = [(idx, path) for idx, path in files if idx > last_idx]
            if not newer:
                if aggregate and (time.time() - aggregate_started) > 0.2:
                    self._push_chunk(bytes(aggregate))
                    aggregate.clear()
                time.sleep(0.01)
                continue

            for idx, path in newer:
                if self._stable_size(path, timeout=0.4) <= 0:
                    continue
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    if data:
                        if not aggregate:
                            aggregate_started = time.time()
                        aggregate.extend(data)
                        if len(aggregate) >= bytes_per_chunk:
                            self._push_chunk(bytes(aggregate))
                            aggregate.clear()
                    last_idx = idx
                except OSError:
                    continue

        if aggregate:
            self._push_chunk(bytes(aggregate))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Audio capture started (port=%d)", self.udp_port)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._gst_proc and self._gst_proc.poll() is None:
            self._gst_proc.kill()
            self._gst_proc.wait(timeout=2)
        self._gst_proc = None
        if self._chunk_dir and os.path.isdir(self._chunk_dir):
            shutil.rmtree(self._chunk_dir, ignore_errors=True)
        self._chunk_dir = None
        logger.info("Audio capture stopped")

    def get_chunk(self, timeout: float = 0.5) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
