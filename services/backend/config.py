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

    model_config = {"env_file": ".env", "env_prefix": "CC_BACKEND_"}


backend_settings = BackendSettings()
