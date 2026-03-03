"""Configuration for the remote backend."""

from pydantic_settings import BaseSettings


class BackendSettings(BaseSettings):
    # Authentication
    api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Frame handling
    frame_queue_size: int = 10
    max_frame_bytes: int = 500_000  # ~500KB max JPEG size
    max_audio_bytes: int = 200_000  # ~100ms 44.1kHz stereo s16le ~= 17.6KB

    # MediaMTX settings (SRT media router)
    mediamtx_enabled: bool = False  # set True when MediaMTX is installed
    mediamtx_binary: str = "mediamtx"
    mediamtx_config: str = ""  # path to mediamtx.yml; auto-detected if empty
    mediamtx_srt_port: int = 8890
    mediamtx_rtsp_port: int = 8554
    mediamtx_webrtc_port: int = 8889

    # RTSP consumer (reads frames from MediaMTX for CV pipeline)
    rtsp_consumer_enabled: bool = False  # set True when MediaMTX is running
    rtsp_base_url: str = "rtsp://127.0.0.1:8554"

    # RTP+FEC receiver (lowest-latency UDP transport)
    rtp_fec_enabled: bool = False  # set True to enable RTP+FEC receiver
    rtp_fec_bind_host: str = "0.0.0.0"
    rtp_fec_bind_port: int = 7000
    rtp_fec_client_id: str = "rtp-fec"  # client_id for session manager

    model_config = {"env_file": ".env", "env_prefix": "CC_BACKEND_"}


backend_settings = BackendSettings()
