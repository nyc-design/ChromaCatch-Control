# Media Pipeline Standardization Plan

## Problem Statement

We have 6 transport modes, 3 backend decode paths, 5 frame source types, and inconsistent audio handling. Each transport has different metrics tracking, error handling, and subprocess management. The codebase is hard to extend and test.

## Goals

1. **Canonical media types** — one `EncodedAccessUnit` for video, one `EncodedAudioFrame` for audio
2. **Two transport modes** — RTP+FEC (primary, UDP to server with public IP) and H.265/H.264 WebSocket (fallback for restrictive networks)
3. **One backend decode path** — PyAV for both H.264 and H.265
4. **Encoder abstraction** — GPU-first with CPU fallback (NVENC, VAAPI, VideoToolbox, x264/x265), Sunshine-inspired probing
5. **Zero-copy passthrough** for sources already producing H.264/H.265 (AirPlay, SysDVR, iOS ReplayKit)
6. **First-class audio** — Opus encoding, same transport as video, proper `EncodedAudioFrame` type
7. **H.265 preferred** where encoder/decoder support exists, H.264 fallback

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  CaptureProvider (one per source type)                         │
│                                                                 │
│  Passthrough sources:          Raw-frame sources:              │
│  ┌─────────────┐               ┌──────────────────────────┐    │
│  │ AirPlay     │               │ UVC / Screen / NTR       │    │
│  │ SysDVR      │ → H.264 AUs  │ → BGR frames             │    │
│  │ iOS Replay  │               │ → EncoderBackend.encode() │    │
│  └──────┬──────┘               └────────────┬─────────────┘    │
│         │                                    │                  │
│         └──────────┬─────────────────────────┘                  │
│                    ▼                                            │
│           EncodedAccessUnit                                    │
│           (H.264 or H.265 Annex-B bytes)                      │
│                                                                 │
│  Audio:                                                        │
│  ┌───────────────────┐                                         │
│  │ AirPlay / System  │ → PCM → OpusEncoder → EncodedAudioFrame│
│  └───────────────────┘                                         │
└─────────────────────────────────────────────────────────────────┘
                    │ video AUs          │ audio frames
                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  MediaTransport (2 implementations)                            │
│                                                                 │
│  ┌───────────────────┐    ┌────────────────────────────┐       │
│  │ RTP+FEC (primary) │    │ WebSocket (TCP fallback)   │       │
│  │ UDP to server IP  │    │ H.264/H.265 AUs + Opus     │       │
│  │ Video + Audio mux │    │ over wss:// with TLS       │       │
│  └───────────────────┘    └────────────────────────────┘       │
│                                                                 │
│  ┌────────────────────────────────────────┐                    │
│  │ AutoTransport (auto-probe)            │                    │
│  │ Try UDP → fall back to WS if blocked  │                    │
│  └────────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼ (internet / LAN)
┌─────────────────────────────────────────────────────────────────┐
│  Backend Ingest (single pipeline)                              │
│                                                                 │
│  RTP+FEC receiver ─┐                                           │
│  WS handler ───────┼→ EncodedAccessUnit queue                  │
│                     │         │                                  │
│                     │         ▼                                  │
│                     │  PyAV Decoder (H.264 / H.265)             │
│                     │         │                                  │
│                     │         ▼                                  │
│                     │  SessionManager.update_frame(bgr)         │
│                     │         │                                  │
│                     │    ┌────┴────┐                             │
│                     │    │  CV     │  Dashboard                  │
│                     │    │pipeline │  (JPEG encode on demand)    │
│                     │    └─────────┘                             │
│                                                                 │
│  Audio:                                                        │
│  Opus frames → OpusDecoder → PCM → AudioSessionManager         │
└─────────────────────────────────────────────────────────────────┘
```

## Canonical Data Types

### `EncodedAccessUnit` — `services/shared/encoded_au.py`

```python
@dataclass(frozen=True, slots=True)
class EncodedAccessUnit:
    data: bytes                     # Annex-B byte stream
    is_keyframe: bool = False
    capture_timestamp: float        # time.time() at capture
    sequence: int = 0
    codec: str = "h264"             # "h264" | "h265"
    width: int = 0                  # 0 = unknown
    height: int = 0                 # 0 = unknown
```

### `EncodedAudioFrame` — `services/shared/encoded_au.py`

```python
@dataclass(frozen=True, slots=True)
class EncodedAudioFrame:
    data: bytes                     # Opus-encoded bytes
    capture_timestamp: float
    sequence: int = 0
    sample_rate: int = 48000
    channels: int = 2
    duration_ms: int = 20           # Opus frame duration
```

Both types are immutable, thread-safe, and carry enough metadata for latency tracking.

## Phase 0: Foundation (no existing code changed)

**Add new abstractions alongside existing code. Nothing breaks.**

### 0A: Canonical types
- New: `services/shared/encoded_au.py` — `EncodedAccessUnit` + `EncodedAudioFrame`
- Tests for serialization, construction, edge cases

### 0B: EncoderBackend ABC + implementations
- New: `services/airplay-client/airplay_client/encode/`
  - `base.py` — `EncoderBackend` ABC (`encode(bgr) → EncodedAccessUnit`)
  - `probe.py` — runtime GPU capability detection (Sunshine-style)
    - Check NVENC: `nvidia-smi` + PyAV `h264_nvenc`/`hevc_nvenc` codec
    - Check VAAPI: `/dev/dri/renderD128` + PyAV `h264_vaapi`/`hevc_vaapi`
    - Check VideoToolbox: `platform.system() == "Darwin"` + PyAV `hevc_videotoolbox`
    - Fallback: `libx264`/`libx265` (always available via PyAV)
  - `pyav_encoder.py` — single implementation that takes codec name from probe
    - Configurable: codec, preset, bitrate, tune
    - Supports both H.264 and H.265
    - `ultrafast` preset + `zerolatency` tune for real-time
  - `factory.py` — `create_encoder() → EncoderBackend` (probes, picks best)
- All encoder backends use PyAV (wraps FFmpeg). One class, parameterized by codec.
- Tests with mock frames, probe mocking

### 0C: OpusEncoder / OpusDecoder
- New: `services/shared/opus_codec.py`
  - `OpusEncoder` — PCM → Opus frames → `EncodedAudioFrame`
  - `OpusDecoder` — `EncodedAudioFrame` → PCM
  - Uses PyAV (`libopus`) or `opuslib` for encoding
  - 48kHz, stereo, 20ms frames, 128kbps default
- Tests for encode/decode round-trip

### 0D: CaptureProvider ABC + implementations
- New: `services/airplay-client/airplay_client/capture_provider/`
  - `base.py` — `CaptureProvider` ABC
    ```python
    class CaptureProvider(ABC):
        async def get_au(self, timeout: float = 0.5) -> EncodedAccessUnit | None: ...
        async def get_audio(self, timeout: float = 0.5) -> EncodedAudioFrame | None: ...
        def start(self) -> None: ...
        def stop(self) -> None: ...
        provider_name: str
        is_running: bool
    ```
  - **Passthrough providers** (zero-copy, source already produces H.264):
    - `airplay_provider.py` — wraps existing `H264Capture` + `AirPlayAudioSource`, converts to canonical types
    - `sysdvr_provider.py` — new GStreamer pipeline: `rtspsrc → rtph264depay → h264parse → fdsink` (same pattern as H264Capture but RTSP source). Zero decode/re-encode.
  - **Encoding providers** (source produces raw BGR, needs encoding):
    - `encoding_provider.py` — generic wrapper: any `FrameSource` + `EncoderBackend` + `OpusEncoder`
      - Works for UVC capture cards, screen capture, NTR (after JPEG decode)
      - Reuses existing `CaptureCardFrameSource`, `ScreenFrameSource`, `NTRFrameSource`
  - `factory.py` — `create_capture_provider() → CaptureProvider`
    - Routes based on `CC_CLIENT_CAPTURE_SOURCE`
    - For raw-frame sources, auto-probes best encoder
- Tests for each provider (mock frame sources, mock encoders)

### 0E: Updated RTP+FEC protocol for audio
- Modify: `services/shared/rtp_fec_protocol.py`
  - Add audio payload type (`PT=97` for Opus)
  - Audio packets: `[RTP Header (12B)] [Opus payload]` (no FEC needed for audio — Opus handles loss gracefully with PLC)
  - Video packets: unchanged (`PT=96`, with FEC)
  - Helper: `build_audio_rtp_packet(opus_data, sequence, timestamp)`
- Tests for audio packet construction/parsing

**Files added in Phase 0** (~15 new files, ~800 lines + tests):
```
services/shared/encoded_au.py
services/shared/opus_codec.py
services/airplay-client/airplay_client/encode/__init__.py
services/airplay-client/airplay_client/encode/base.py
services/airplay-client/airplay_client/encode/probe.py
services/airplay-client/airplay_client/encode/pyav_encoder.py
services/airplay-client/airplay_client/encode/factory.py
services/airplay-client/airplay_client/capture_provider/__init__.py
services/airplay-client/airplay_client/capture_provider/base.py
services/airplay-client/airplay_client/capture_provider/airplay_provider.py
services/airplay-client/airplay_client/capture_provider/sysdvr_provider.py
services/airplay-client/airplay_client/capture_provider/encoding_provider.py
services/airplay-client/airplay_client/capture_provider/factory.py
+ test files for each
```

## Phase 1: Wire new pipeline into transports

**Refactor the two surviving transports to consume CaptureProvider. Old transports still work.**

### 1A: New `MediaTransport` interface
- Modify: `transport/base.py`
  - `start(capture_provider: CaptureProvider)` — transport owns the send loop
  - Transport pulls `EncodedAccessUnit` and `EncodedAudioFrame` from provider
  - Remove `frame_source`, `h264_capture`, `audio_source` from transport constructors

### 1B: Refactor `RTPFECTransport`
- Modify: `transport/rtp_fec_transport.py`
  - Consume `CaptureProvider` instead of `H264Capture`
  - Add audio sending: interleave Opus RTP packets (PT=97) on same UDP socket
  - Track unified metrics (frames, audio frames, bytes, latency)

### 1C: Refactor `H264WebSocketTransport` → `AUWebSocketTransport`
- Rename to reflect it handles both H.264 and H.265
- Modify to consume `CaptureProvider`
- Audio: send `EncodedAudioFrame` (Opus bytes) instead of raw PCM
  - New WS message type: `AudioFrameMetadata` with codec="opus"
  - Backend decodes Opus to PCM for dashboard/CV audio analysis

### 1D: `AutoTransport` (smart failover)
- New: `transport/auto_transport.py`
  - Probes UDP connectivity to backend (send test packet, wait for response)
  - If UDP works → use RTP+FEC
  - If UDP blocked → fall back to WebSocket
  - Periodic re-probe (every 60s) to upgrade back to RTP+FEC
  - Replaces old `FailoverTransport`

### 1E: Simplify `main.py`
- Rewrite `ChromaCatchClient.__init__` and `run()`:
  ```python
  self._provider = create_capture_provider()     # One call
  self._transport = create_media_transport()      # rtp-fec | h264-ws | auto
  self._control_ws = WebSocketClient(...)
  self._commander = create_commander()
  self._forwarder = CommandForwarder(self._commander)
  ```
- Remove conditional H264Capture/FrameSource/AudioSource creation
- Status reporting reads metrics from `_provider` and `_transport`

### 1F: Update transport factory
- Modify: `transport/factory.py`
  - Accept only: `rtp-fec`, `websocket` (new name for AU-WS), `auto`
  - Old modes (`srt`, `webrtc`, etc.) emit deprecation warnings, still work via legacy path

## Phase 2: Unified backend decode

**Converge all backend ingest to single PyAV decode path.**

### 2A: Backend audio decode
- Modify: `services/backend/ws_handler.py`
  - Handle `AudioFrameMetadata` with `codec="opus"` → decode via `OpusDecoder`
  - Keep backward compat for raw PCM `AudioChunk` (deprecation warning)

### 2B: RTP+FEC receiver audio
- Modify: `services/backend/rtp_fec_receiver.py`
  - Demux audio (PT=97) from video (PT=96) in `datagram_received()`
  - Audio packets → `OpusDecoder` → PCM → `SessionManager.update_audio()`

### 2C: H.265 decode support
- Modify: `services/backend/h264_decoder.py` → rename to `video_decoder.py`
  - Support both `h264` and `hevc` codec contexts
  - Auto-detect codec from AU NALU types (H.264: 0x67 SPS / H.265: 0x40 VPS)
  - Or negotiate codec via metadata in `H264FrameMetadata` (add `codec` field)

### 2D: Simplify `SessionManager.update_frame()`
- Remove `jpeg_bytes` parameter — JPEG encoding happens on-demand for dashboard
- `update_frame(client_id, bgr, capture_timestamp)` — stores BGR, lazy-encodes JPEG
- Add `update_audio(client_id, pcm, sample_rate, channels)`

### 2E: Remove JPEG WS decode path
- Modify: `ws_handler.py`
  - Remove `FrameMetadata` + JPEG binary handling
  - Only accept `H264FrameMetadata` (with codec field) + encoded video bytes
  - Log deprecation if old JPEG frames arrive

## Phase 3: Remove deprecated code

**Clean deletion of everything no longer needed.**

### Delete transport files:
- `transport/srt_transport.py`
- `transport/webrtc_transport.py`
- `transport/ws_transport.py` (old JPEG WebSocket)
- `transport/failover_transport.py` (replaced by `auto_transport.py`)

### Delete backend files:
- `backend/rtsp_consumer.py` (was for MediaMTX RTSP)
- `backend/mediamtx_manager.py`
- `backend/mediamtx/mediamtx.yml`

### Delete/simplify client files:
- `capture/frame_capture.py` (decoded-frame capture, replaced by H264 pipeline)
- `shared/frame_codec.py` (JPEG encode/decode for WS transport)

### Delete tests for removed code:
- `test_transport.py` (SRT + WS tests)
- `test_webrtc_transport.py`
- `test_frame_capture.py`
- `test_rtsp_consumer.py`
- `test_mediamtx_manager.py`
- `test_frame_codec.py`

### Config cleanup:
- Remove: `CC_CLIENT_SRT_*`, `CC_CLIENT_WEBRTC_*`, `CC_CLIENT_JPEG_QUALITY`, `CC_CLIENT_MAX_DIMENSION`
- Remove: `CC_BACKEND_MEDIAMTX_*`, `CC_BACKEND_RTSP_*`
- Keep: `CC_CLIENT_TRANSPORT_MODE` (accept: `rtp-fec`, `websocket`, `auto`)
- Add: `CC_CLIENT_CODEC` (`h265` default, `h264` fallback)
- Add: `CC_CLIENT_ENCODER_PRESET` (`auto` = probe GPU, or explicit `nvenc`/`vaapi`/`videotoolbox`/`software`)

## Phase 4: Polish

- Update `CLAUDE.md` architecture section
- Update all config documentation
- Dashboard: decode H.265 for preview (use JS WASM decoder or server-side JPEG conversion)
- Latency budget instrumentation per stage:
  - `t_capture` → `t_encode` → `t_packetize` → `t_transit` → `t_decode` → `t_cv`
- Codec negotiation: client tells backend its codec in metadata, backend auto-selects decoder

## Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Primary transport | RTP+FEC (UDP) | Server has public IP, client→server UDP works through NAT. Lowest latency with FEC loss recovery. |
| Fallback transport | AU WebSocket (TCP) | For restrictive networks or HTTP-only platforms. Works everywhere. |
| Auto mode | UDP probe → RTP+FEC or WS | Best of both worlds. Transparent to user. |
| Preferred codec | H.265/HEVC | ~50% bitrate savings over H.264 at same quality. Better for internet transport. |
| Codec fallback | H.264 | Universal support. Used when H.265 encoder unavailable. |
| Encoder library | PyAV (FFmpeg) | Same dependency as backend decode. Supports all GPU backends. One class, parameterized. |
| Encoder probing | Sunshine-style runtime detect | NVENC → VAAPI → VideoToolbox → x264/x265. |
| Audio codec | Opus | Best low-latency audio codec. Wide support. Handles packet loss gracefully. |
| Audio transport | Interleaved with video | Same RTP+FEC socket (PT=97) or same WebSocket. First-class, not afterthought. |
| SRT/WebRTC | Remove entirely | Not needed. RTP+FEC is better for our use case (no MediaMTX dependency). |
| JPEG WS | Remove entirely | Replaced by H.264/H.265 WS which is 10x more efficient. |
| MediaMTX | Remove entirely | No longer needed without SRT/WebRTC. |
| RTSP consumer | Remove entirely | Backend gets frames via RTP+FEC or WebSocket only. |
| SysDVR | Zero-copy GStreamer passthrough | rtspsrc → rtph264depay → h264parse → fdsink. Same pattern as AirPlay. |
| NTR | Decode JPEG + re-encode H.264/H.265 | Unavoidable — NTR only produces JPEG fragments. |

## Migration Risk Mitigation

- **Phase 0 adds code only** — zero risk of breaking existing functionality
- **Phase 1 adds new transport interface** alongside old — old transports still work
- **Phase 2** can be done incrementally (add H.265 support, then remove JPEG path)
- **Phase 3** is pure deletion of already-deprecated code
- **All phases maintain test count** — new tests added before old tests removed

## Test Strategy

| Phase | Tests Added | Tests Removed | Net Change |
|-------|-------------|---------------|------------|
| 0 | ~80 (canonical types, encoder, providers, opus) | 0 | +80 |
| 1 | ~40 (new transport interface, auto-failover) | 0 | +40 |
| 2 | ~20 (H.265 decode, opus decode, unified ingest) | 0 | +20 |
| 3 | 0 | ~60 (deleted transport/consumer tests) | -60 |
| **Total** | ~140 | ~60 | **+80** |

## Rough Scope Per Phase

- **Phase 0**: ~15 new files, ~1500 lines (code + tests). Foundation only.
- **Phase 1**: ~5 modified files, ~500 lines changed. Transport refactor.
- **Phase 2**: ~5 modified files, ~400 lines changed. Backend unification.
- **Phase 3**: ~15 deleted files, ~2000 lines removed. Cleanup.
- **Phase 4**: Documentation + instrumentation.
