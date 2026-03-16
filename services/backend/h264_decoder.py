"""Backward compatibility shim — imports from video_decoder.py.

Use backend.video_decoder.VideoDecoder for new code.
"""

from backend.video_decoder import VideoDecoder as H264Decoder  # noqa: F401
from backend.video_decoder import detect_codec  # noqa: F401
