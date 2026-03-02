"""Streaming H.264 decoder using PyAV (FFmpeg wrapper).

Decodes individual H.264 Access Units received over WebSocket into
BGR numpy arrays for the CV pipeline and dashboard.
"""

import logging

import av
import numpy as np

logger = logging.getLogger(__name__)


class H264Decoder:
    """Stateful H.264 decoder that processes individual Access Units.

    Maintains a codec context across calls so that SPS/PPS state
    persists between keyframes and predicted frames.
    """

    def __init__(self) -> None:
        self._codec = av.CodecContext.create("h264", "r")
        self._frames_decoded = 0
        self._decode_errors = 0

    def decode(self, h264_au: bytes) -> np.ndarray | None:
        """Decode an H.264 Access Unit to a BGR numpy array.

        Args:
            h264_au: Raw H.264 Annex B bytes (one Access Unit).

        Returns:
            BGR numpy array, or None if the AU didn't produce a frame
            (e.g., SPS/PPS only, or decoder still buffering).
        """
        if not h264_au:
            return None
        total_attempts = self._frames_decoded + self._decode_errors
        try:
            packet = av.Packet(h264_au)
            frames = self._codec.decode(packet)
            frame_list = list(frames)
            if not frame_list:
                if total_attempts < 20:
                    nalu_types = self._parse_nalu_types(h264_au)
                    logger.warning(
                        "H.264 decode produced 0 frames: %d bytes, NALUs=%s",
                        len(h264_au), nalu_types,
                    )
                self._decode_errors += 1
                return None
            for frame in frame_list:
                bgr = frame.to_ndarray(format="bgr24")
                self._frames_decoded += 1
                if self._frames_decoded <= 3:
                    logger.info(
                        "H.264 frame #%d decoded: %dx%d (%d bytes in)",
                        self._frames_decoded, frame.width, frame.height,
                        len(h264_au),
                    )
                return bgr
        except av.error.InvalidDataError:
            if total_attempts < 20:
                nalu_types = self._parse_nalu_types(h264_au)
                logger.warning(
                    "H.264 decode error (invalid data): %d bytes, "
                    "starts=%s, NALUs=%s, decoded_so_far=%d",
                    len(h264_au),
                    h264_au[:8].hex() if len(h264_au) >= 8 else h264_au.hex(),
                    nalu_types,
                    self._frames_decoded,
                )
            elif self._decode_errors < 5:
                logger.warning("H.264 decode error (invalid data): %d bytes", len(h264_au))
            self._decode_errors += 1
        except Exception as e:
            logger.error("H.264 decode error: %s", e)
        return None

    @staticmethod
    def _parse_nalu_types(data: bytes) -> list[int]:
        """Extract NALU type codes from Annex-B data for diagnostics."""
        types = []
        i = 0
        while i < len(data) - 4:
            # Look for start codes (00 00 00 01 or 00 00 01)
            if data[i:i+4] == b'\x00\x00\x00\x01':
                types.append(data[i+4] & 0x1F)
                i += 4
            elif data[i:i+3] == b'\x00\x00\x01':
                types.append(data[i+3] & 0x1F)
                i += 3
            else:
                i += 1
        return types

    def reset(self) -> None:
        """Reset the decoder state (e.g., after stream restart)."""
        self._codec = av.CodecContext.create("h264", "r")
        self._frames_decoded = 0
        self._decode_errors = 0
        logger.debug("H.264 decoder reset")

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded
