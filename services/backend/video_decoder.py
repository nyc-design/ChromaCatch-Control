"""Streaming video decoder using PyAV (FFmpeg wrapper).

Decodes individual H.264 or H.265 Access Units received over
WebSocket or RTP+FEC into BGR numpy arrays for the CV pipeline.
"""

import logging

import av
import numpy as np

logger = logging.getLogger(__name__)

# H.264 NALU types
_H264_NAL_TYPE_SPS = 7
_H264_NAL_TYPE_VPS_H265 = 32  # H.265 VPS


def detect_codec(au_data: bytes) -> str:
    """Auto-detect codec from AU NALU types.

    H.264: NAL type byte = (byte & 0x1F), SPS = type 7
    H.265: NAL type byte = ((byte >> 1) & 0x3F), VPS = type 32

    Returns 'h264' or 'h265'.
    """
    i = 0
    while i < len(au_data) - 4:
        if au_data[i : i + 4] == b"\x00\x00\x00\x01":
            nal_byte = au_data[i + 4]
            # H.265 check: forbidden_zero_bit=0, nal_unit_type in bits 1-6
            h265_type = (nal_byte >> 1) & 0x3F
            if h265_type in (32, 33, 34):  # VPS, SPS, PPS
                return "h265"
            # H.264: type 7 (SPS) or type 8 (PPS)
            h264_type = nal_byte & 0x1F
            if h264_type in (7, 8):
                return "h264"
            i += 5
        elif au_data[i : i + 3] == b"\x00\x00\x01":
            nal_byte = au_data[i + 3]
            h265_type = (nal_byte >> 1) & 0x3F
            if h265_type in (32, 33, 34):
                return "h265"
            h264_type = nal_byte & 0x1F
            if h264_type in (7, 8):
                return "h264"
            i += 4
        else:
            i += 1
    return "h264"  # default


class VideoDecoder:
    """Stateful video decoder supporting H.264 and H.265.

    Maintains a codec context across calls so that SPS/PPS/VPS state
    persists between keyframes and predicted frames.
    """

    def __init__(self, codec: str = "h264") -> None:
        self._codec_name = "h264" if codec == "h264" else "hevc"
        self._codec_type = codec
        self._ctx = av.CodecContext.create(self._codec_name, "r")
        self._frames_decoded = 0
        self._decode_errors = 0

    def decode(self, au_data: bytes) -> np.ndarray | None:
        """Decode an Access Unit to a BGR numpy array.

        Args:
            au_data: Raw Annex B bytes (one Access Unit).

        Returns:
            BGR numpy array, or None if the AU didn't produce a frame.
        """
        if not au_data:
            return None
        total_attempts = self._frames_decoded + self._decode_errors
        try:
            packet = av.Packet(au_data)
            frames = self._ctx.decode(packet)
            frame_list = list(frames)
            if not frame_list:
                if total_attempts < 20:
                    nalu_types = self._parse_nalu_types(au_data)
                    logger.warning(
                        "%s decode produced 0 frames: %d bytes, NALUs=%s",
                        self._codec_type, len(au_data), nalu_types,
                    )
                self._decode_errors += 1
                return None
            for frame in frame_list:
                bgr = frame.to_ndarray(format="bgr24")
                self._frames_decoded += 1
                if self._frames_decoded <= 3:
                    logger.info(
                        "%s frame #%d decoded: %dx%d (%d bytes in)",
                        self._codec_type, self._frames_decoded,
                        frame.width, frame.height, len(au_data),
                    )
                return bgr
        except av.error.InvalidDataError:
            if total_attempts < 20:
                nalu_types = self._parse_nalu_types(au_data)
                logger.warning(
                    "%s decode error (invalid data): %d bytes, NALUs=%s, decoded_so_far=%d",
                    self._codec_type, len(au_data), nalu_types, self._frames_decoded,
                )
            elif self._decode_errors < 5:
                logger.warning("%s decode error (invalid data): %d bytes", self._codec_type, len(au_data))
            self._decode_errors += 1
        except Exception as e:
            logger.error("%s decode error: %s", self._codec_type, e)
        return None

    def _parse_nalu_types(self, data: bytes) -> list[int]:
        """Extract NALU type codes from Annex-B data for diagnostics."""
        types = []
        i = 0
        while i < len(data) - 4:
            if data[i : i + 4] == b"\x00\x00\x00\x01":
                if self._codec_type == "h265":
                    types.append((data[i + 4] >> 1) & 0x3F)
                else:
                    types.append(data[i + 4] & 0x1F)
                i += 5
            elif data[i : i + 3] == b"\x00\x00\x01":
                if self._codec_type == "h265":
                    types.append((data[i + 3] >> 1) & 0x3F)
                else:
                    types.append(data[i + 3] & 0x1F)
                i += 4
            else:
                i += 1
        return types

    def reset(self, codec: str | None = None) -> None:
        """Reset the decoder state (e.g., after stream restart or codec change)."""
        if codec:
            self._codec_name = "h264" if codec == "h264" else "hevc"
            self._codec_type = codec
        self._ctx = av.CodecContext.create(self._codec_name, "r")
        self._frames_decoded = 0
        self._decode_errors = 0
        logger.debug("%s decoder reset", self._codec_type)

    @property
    def codec_type(self) -> str:
        return self._codec_type

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded


# Backward compatibility alias
H264Decoder = VideoDecoder
