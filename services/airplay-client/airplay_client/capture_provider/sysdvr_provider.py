"""SysDVR zero-copy passthrough capture provider.

Gets raw H.264 AUs from a modded Switch's SysDVR RTSP stream via GStreamer.
Same pattern as AirPlay H264Capture but with rtspsrc instead of udpsrc.
No decode/re-encode — H.264 AUs pass through untouched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame

from .base import CaptureProvider

logger = logging.getLogger(__name__)

_NAL_START_CODE = b"\x00\x00\x00\x01"
_NAL_TYPE_IDR = 5


def _has_nal_type(data: bytes, nal_type: int) -> bool:
    """Check if data contains a NAL unit of the given type."""
    search_start = 0
    while True:
        idx = data.find(_NAL_START_CODE, search_start)
        if idx == -1 or idx + 4 >= len(data):
            break
        if (data[idx + 4] & 0x1F) == nal_type:
            return True
        search_start = idx + 4
    return False


class SysDVRCaptureProvider(CaptureProvider):
    """Zero-copy H.264 passthrough from SysDVR RTSP.

    GStreamer pipeline: rtspsrc → rtph264depay → h264parse → multifilesink
    Same file-polling pattern as H264Capture.
    """

    def __init__(self, rtsp_url: str, max_queue_size: int = 30):
        self._rtsp_url = rtsp_url
        self._au_queue: queue.Queue[tuple[bytes, bool, float]] = queue.Queue(maxsize=max_queue_size)
        self._gst_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._au_dir: str | None = None
        self._sequence = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("SysDVR capture provider started: %s", self._rtsp_url)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._gst_proc and self._gst_proc.poll() is None:
            self._gst_proc.kill()
            self._gst_proc.wait(timeout=3)
            self._gst_proc = None
        if self._au_dir:
            shutil.rmtree(self._au_dir, ignore_errors=True)
            self._au_dir = None
        logger.info("SysDVR capture provider stopped")

    async def get_au(self, timeout: float = 0.5) -> EncodedAccessUnit | None:
        result = await asyncio.to_thread(self._au_queue.get, timeout=timeout)
        if result is None:
            return None
        au_bytes, is_keyframe, timestamp = result
        self._sequence += 1
        return EncodedAccessUnit(
            data=au_bytes,
            is_keyframe=is_keyframe,
            capture_timestamp=timestamp,
            sequence=self._sequence,
            codec="h264",
        )

    async def get_audio(self, timeout: float = 0.1) -> EncodedAudioFrame | None:
        return None  # SysDVR audio not implemented yet

    @property
    def provider_name(self) -> str:
        return "sysdvr-passthrough"

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def codec(self) -> str:
        return "h264"

    def _capture_loop(self) -> None:
        while self._running:
            au_dir = None
            try:
                au_dir = self._start_gst_process()
            except RuntimeError as e:
                logger.info("SysDVR GStreamer not ready: %s — retrying in 3s", e)
                if au_dir:
                    shutil.rmtree(au_dir, ignore_errors=True)
                time.sleep(3)
                continue

            if not self._running:
                break

            last_au_at = time.time()
            saw_data = False
            au_idx = 0

            while self._running:
                proc = self._gst_proc
                if proc is None or proc.poll() is not None:
                    logger.warning("SysDVR GStreamer exited, restarting")
                    break

                au_path = os.path.join(au_dir, f"au_{au_idx:06d}.h264")
                if os.path.exists(au_path):
                    au_bytes = self._read_stable_file(au_path)
                    if au_bytes:
                        timestamp = time.time()
                        is_keyframe = _has_nal_type(au_bytes, _NAL_TYPE_IDR)
                        self._push_au(au_bytes, is_keyframe, timestamp)
                        last_au_at = timestamp
                        if not saw_data:
                            saw_data = True
                            logger.info("First SysDVR AU: %d bytes, keyframe=%s", len(au_bytes), is_keyframe)
                        au_idx += 1
                    continue

                if saw_data and (time.time() - last_au_at) > 10.0:
                    logger.warning("No SysDVR data for 10s, restarting")
                    break
                time.sleep(0.001)

            if self._gst_proc and self._gst_proc.poll() is None:
                self._gst_proc.kill()
                self._gst_proc.wait(timeout=3)
            self._gst_proc = None
            if au_dir:
                shutil.rmtree(au_dir, ignore_errors=True)
                self._au_dir = None

    def _start_gst_process(self) -> str:
        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found")

        au_dir = tempfile.mkdtemp(prefix="chromacatch_sysdvr_")
        self._au_dir = au_dir
        au_pattern = os.path.join(au_dir, "au_%06d.h264")

        cmd = [
            gst_path, "-q", "-e",
            "rtspsrc", f"location={self._rtsp_url}", "latency=0", "protocols=tcp",
            "!", "rtph264depay",
            "!", "h264parse", "config-interval=-1",
            "!", "video/x-h264,stream-format=byte-stream,alignment=au",
            "!", "multifilesink", f"location={au_pattern}",
            "next-file=buffer", "max-files=60", "sync=false", "async=false",
        ]
        logger.info("Starting SysDVR GStreamer: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._gst_proc = proc

        for stream in (proc.stdout, proc.stderr):
            threading.Thread(target=self._drain_stream, args=(stream,), daemon=True).start()

        deadline = time.time() + 30
        while time.time() < deadline and self._running:
            if proc.poll() is not None:
                shutil.rmtree(au_dir, ignore_errors=True)
                raise RuntimeError(f"SysDVR GStreamer exited early (rc={proc.returncode})")
            first_file = os.path.join(au_dir, "au_000000.h264")
            if os.path.exists(first_file) and os.path.getsize(first_file) > 0:
                return au_dir
            time.sleep(0.5)

        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        shutil.rmtree(au_dir, ignore_errors=True)
        raise RuntimeError("SysDVR: no AU received within timeout")

    @staticmethod
    def _read_stable_file(path: str) -> bytes | None:
        try:
            size1 = os.path.getsize(path)
            if size1 == 0:
                return None
            time.sleep(0.001)
            size2 = os.path.getsize(path)
            if size1 != size2:
                time.sleep(0.1)
            with open(path, "rb") as f:
                data = f.read()
            os.unlink(path)
            return data if data else None
        except (OSError, FileNotFoundError):
            return None

    @staticmethod
    def _drain_stream(stream) -> None:
        try:
            for line in stream:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[gst-sysdvr] %s", text)
        except Exception:
            pass

    def _push_au(self, au_bytes: bytes, is_keyframe: bool, timestamp: float) -> None:
        if self._au_queue.full():
            try:
                self._au_queue.get_nowait()
            except queue.Empty:
                pass
        self._au_queue.put((au_bytes, is_keyframe, timestamp))
