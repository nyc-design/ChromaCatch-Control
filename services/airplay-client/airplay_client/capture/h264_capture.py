"""Captures raw H.264 Access Units from UxPlay's RTP stream via GStreamer.

Unlike FrameCapture (which decodes H.264 to BGR frames), this outputs raw H.264
byte-stream data for passthrough over WebSocket. No decode or re-encode on the
client — the backend decodes H.264 directly.

GStreamer pipeline: udpsrc → rtph264depay → h264parse → multifilesink
Output: one file per Access Unit (alignment=au), Annex B byte-stream format.
"""

import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)

# H.264 NAL unit start code (4-byte)
_NAL_START_CODE = b"\x00\x00\x00\x01"
# IDR slice NAL type (keyframe)
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


class H264Capture:
    """Captures raw H.264 Access Units from UxPlay's RTP stream.

    Starts a GStreamer pipeline that receives H.264 RTP, depayloads, parses,
    and outputs Annex B byte-stream AUs via multifilesink (one file per AU).
    Python polls for new files, reads AU bytes, and detects keyframes.
    """

    def __init__(self, udp_port: int | None = None, max_queue_size: int = 30):
        self.udp_port = udp_port or settings.airplay_udp_port
        self._au_queue: queue.Queue[tuple[bytes, bool, float]] = queue.Queue(
            maxsize=max_queue_size
        )
        self._gst_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._au_dir: str | None = None

    def start(self) -> None:
        """Start capturing H.264 AUs in a background thread."""
        if self._running:
            logger.warning("H264 capture is already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(
            "H264 capture started (port=%d, passthrough mode)", self.udp_port
        )

    def stop(self) -> None:
        """Stop capturing."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._gst_proc and self._gst_proc.poll() is None:
            self._gst_proc.kill()
            self._gst_proc.wait(timeout=3)
            self._gst_proc = None
        if self._au_dir:
            shutil.rmtree(self._au_dir, ignore_errors=True)
            self._au_dir = None
        logger.info("H264 capture stopped")

    def get_au(self, timeout: float = 1.0) -> tuple[bytes, bool, float] | None:
        """Get the next H.264 Access Unit.

        Returns (au_bytes, is_keyframe, timestamp) or None on timeout.
        """
        try:
            return self._au_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _capture_loop(self) -> None:
        """Main capture loop with auto-restart on GStreamer exit."""
        logger.info(
            "Waiting for AirPlay stream on port %d (H.264 passthrough)...",
            self.udp_port,
        )

        while self._running:
            au_dir = None
            try:
                au_dir = self._start_gst_process()
                logger.info("H.264 passthrough pipeline connected!")
            except RuntimeError as e:
                logger.info(
                    "GStreamer H.264 pipe not ready: %s — retrying in 3s...", e
                )
                if au_dir:
                    shutil.rmtree(au_dir, ignore_errors=True)
                time.sleep(3)
                continue

            if not self._running:
                break

            last_au_at = time.time()
            saw_data = False
            au_idx = 0
            reconnect_timeout_s = max(2.0, settings.airplay_reconnect_timeout_s)

            while self._running:
                proc = self._gst_proc
                if proc is None or proc.poll() is not None:
                    rc = proc.returncode if proc else -1
                    logger.warning(
                        "GStreamer H.264 process exited (rc=%d), restarting", rc
                    )
                    break

                # Poll for next AU file
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
                            logger.info(
                                "First H.264 AU received (%d bytes, keyframe=%s)",
                                len(au_bytes),
                                is_keyframe,
                            )
                        au_idx += 1
                    continue

                # No new file yet
                if saw_data and (time.time() - last_au_at) > reconnect_timeout_s:
                    logger.warning(
                        "No H.264 data for %.1fs; restarting pipeline",
                        reconnect_timeout_s,
                    )
                    break
                time.sleep(0.001)

            # Cleanup
            if self._gst_proc and self._gst_proc.poll() is None:
                self._gst_proc.kill()
                self._gst_proc.wait(timeout=3)
            self._gst_proc = None
            if au_dir:
                shutil.rmtree(au_dir, ignore_errors=True)
                self._au_dir = None

        logger.info("H.264 capture loop stopped")

    def _start_gst_process(self) -> str:
        """Start gst-launch-1.0 with multifilesink for H.264 AU output.

        Returns au_dir path. Stores proc in self._gst_proc.
        """
        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found. Install GStreamer.")

        au_dir = tempfile.mkdtemp(prefix="chromacatch_h264_")
        self._au_dir = au_dir
        au_pattern = os.path.join(au_dir, "au_%06d.h264")

        cmd = [
            gst_path,
            "-q",
            "-e",
            "udpsrc",
            f"port={self.udp_port}",
            "do-timestamp=true",
            "caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
            "!",
            "rtpjitterbuffer",
            "latency=20",
            "drop-on-latency=true",
            "!",
            "rtph264depay",
            "!",
            "h264parse",
            "config-interval=-1",
            "!",
            "video/x-h264,stream-format=byte-stream,alignment=au",
            "!",
            "multifilesink",
            f"location={au_pattern}",
            "next-file=buffer",
            "max-files=60",
            "sync=false",
            "async=false",
        ]
        logger.info("Starting GStreamer H.264 capture: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._gst_proc = proc

        # Drain stdout+stderr in background
        for stream in (proc.stdout, proc.stderr):
            threading.Thread(
                target=self._drain_stream, args=(stream,), daemon=True
            ).start()

        # Wait for first AU file (up to 120s for AirPlay connection)
        deadline = time.time() + 120
        logger.info(
            "GStreamer H.264 pid=%d, waiting for first AU file...", proc.pid
        )
        while time.time() < deadline and self._running:
            if proc.poll() is not None:
                shutil.rmtree(au_dir, ignore_errors=True)
                raise RuntimeError(
                    f"GStreamer H.264 exited early (rc={proc.returncode})"
                )
            first_file = os.path.join(au_dir, "au_000000.h264")
            if os.path.exists(first_file) and os.path.getsize(first_file) > 0:
                logger.info("GStreamer H.264: first AU file written")
                return au_dir
            time.sleep(0.5)

        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        shutil.rmtree(au_dir, ignore_errors=True)
        raise RuntimeError("GStreamer H.264: no AU received within timeout")

    @staticmethod
    def _read_stable_file(path: str, timeout: float = 0.1) -> bytes | None:
        """Read a file once its size has stabilized (write complete)."""
        try:
            size1 = os.path.getsize(path)
            if size1 == 0:
                return None
            time.sleep(0.001)
            size2 = os.path.getsize(path)
            if size1 != size2:
                time.sleep(timeout)
            with open(path, "rb") as f:
                data = f.read()
            os.unlink(path)
            return data if data else None
        except (OSError, FileNotFoundError):
            return None

    @staticmethod
    def _drain_stream(stream) -> None:
        """Read and log a subprocess stream to prevent pipe buffer blocking."""
        try:
            for line in stream:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[gst-h264] %s", text)
        except Exception:
            pass

    def _push_au(
        self, au_bytes: bytes, is_keyframe: bool, timestamp: float
    ) -> None:
        """Push an AU to the queue, dropping oldest if full."""
        if self._au_queue.full():
            try:
                self._au_queue.get_nowait()
            except queue.Empty:
                pass
        self._au_queue.put((au_bytes, is_keyframe, timestamp))
