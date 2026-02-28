"""Tests for frame capture service."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from airplay_client.capture.frame_capture import CaptureBackend, FrameCapture


class TestCaptureBackend:
    def test_gstreamer_detection(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  YES (1.20.3)"):
            backend = FrameCapture._detect_backend()
            assert backend == CaptureBackend.GSTREAMER

    def test_ffmpeg_fallback(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  NO"):
            backend = FrameCapture._detect_backend()
            assert backend == CaptureBackend.FFMPEG


class TestFrameCapture:
    @pytest.fixture
    def capture(self):
        return FrameCapture(udp_port=5000, backend=CaptureBackend.GSTREAMER)

    def test_gstreamer_pipeline_string(self, capture):
        pipeline = capture._build_gstreamer_pipeline()
        assert "udpsrc port=5000" in pipeline
        assert "rtph264depay" in pipeline
        assert "appsink" in pipeline

    def test_not_running_initially(self, capture):
        assert capture.is_running is False

    def test_get_frame_returns_none_when_empty(self, capture):
        assert capture.get_frame(timeout=0.01) is None

    def test_frame_queue_operations(self, capture):
        test_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        capture.frame_queue.put(test_frame)
        result = capture.get_frame(timeout=0.1)
        assert result is not None
        assert result.shape == (100, 100, 3)

    def test_frame_queue_drops_old_when_full(self, capture):
        for i in range(5):
            frame = np.full((10, 10, 3), i, dtype=np.uint8)
            capture.frame_queue.put(frame)

        assert capture.frame_queue.full()
        capture.frame_queue.get_nowait()
        new_frame = np.full((10, 10, 3), 99, dtype=np.uint8)
        capture.frame_queue.put(new_frame)

        frames = []
        while not capture.frame_queue.empty():
            frames.append(capture.frame_queue.get_nowait())
        assert frames[0][0, 0, 0] == 1
        assert frames[-1][0, 0, 0] == 99

    def test_sdp_file_content(self, capture):
        capture.backend = CaptureBackend.FFMPEG
        path = capture._build_sdp_file()
        with open(path) as f:
            content = f.read()
        assert "m=video 5000 RTP/AVP 96" in content
        assert "a=rtpmap:96 H264/90000" in content
        import os
        os.unlink(path)

    def test_push_frame_drops_oldest_when_full(self, capture):
        for i in range(5):
            capture._push_frame(np.full((10, 10, 3), i, dtype=np.uint8))
        assert capture.frame_queue.full()
        capture._push_frame(np.full((10, 10, 3), 99, dtype=np.uint8))
        frames = []
        while not capture.frame_queue.empty():
            frames.append(capture.frame_queue.get_nowait())
        assert frames[-1][0, 0, 0] == 99

    def test_stop_when_not_started(self, capture):
        capture.stop()
