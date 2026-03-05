# ChromaCatch-Control

Automated shiny hunting bot for Pokemon Go using AirPlay screen mirroring, computer vision, and Bluetooth HID emulation.

## Architecture Overview

```
 [Source]              [Local Client]                  [Remote Backend]
┌──────────┐ AirPlay  ┌───────────────────────┐       ┌───────────────────────┐
│  iPhone   │────────►│  UxPlay ─── H.264 ────┼──────►│  MediaMTX (→RTSP)     │
│  Switch   │ SysDVR  │  (RTP)    passthru    │ SRT/  │       │               │
│  3DS      │ NTR     │           + Opus audio│ WebRTC│  RTSP consumer → CV   │
│  Emulator │ Screen  │                       │ or WS │       │               │
└──────────┘         │  CommandForwarder ◄───┤◄──────┤  Orchestrator          │
      ▲               └──────────┬────────────┘ cmds  └───────────────────────┘
      │                          │ Commander            (WS control always)
      │                          ▼
┌─────┴──────────────────────────────────┐
│  Commander targets:                     │
│  • ESP32 (BLE HID mouse/kb/gamepad)    │
│  • sys-botbase (Switch TCP)            │
│  • Luma3DS (3DS UDP)                   │
│  • Virtual gamepad (uinput/ViGEm)      │
└─────────────────────────────────────────┘
```

### Service Architecture
- **Airplay Client** (`services/airplay-client/`): Runs near the source device. Manages video capture (AirPlay, SysDVR, NTR, screen capture), delivers media to the backend (via WebRTC, SRT, or WebSocket), and routes commands from the backend to the target device via pluggable Commander interface (ESP32, sys-botbase, Luma3DS, virtual gamepad). Deployed as a CLI tool.
- **iOS Controller App** (`services/ios-app/`): Native iPhone app focused on **video + input control**. Relays backend HID/gamepad commands to ESP32 (HTTP/WS) or local BLE HID commander, and broadcasts screen via ReplayKit (H.264 over WebSocket, same h264-ws protocol as CLI). Connects to backend control WS (`/ws/control`).
- **iOS Location Spoofer App** (`services/ios-location-spoofer/`): Dedicated iPhone app for **location spoofing only**. Controls iTools BT GPS dongle (EA session + BLE NMEA), connects to external location service (`/ws/location`), verifies drift via CLLocationManager, and manages DNS location guard extension.
- **Remote Backend** (`services/backend/`): Runs in the cloud (Cloud Run, VM, etc.). Receives frames (via RTSP from MediaMTX or WebSocket), runs CV analysis, and exposes split APIs:
  - client API (`/api/client/v1/...`) for first-party client communication and ops tooling
  - automation API (`/api/automation/v1/...`) for external game automation repos
- **ESP32 Firmware** (`services/esp32/`): Single codebase targeting both ESP32-S3 and ESP32-WROOM.
  - **ESP32-S3**: wired USB HID + BLE modes, with command input priority `wired > websocket > http`.
  - **ESP32-WROOM**: BLE modes only, with command input priority `websocket > http`.
  - BLE Xbox mode now uses an Xbox-specific Composite HID profile (`ESP32-BLE-CompositeHID`) instead of generic BLE gamepad descriptors.
  - ESP32 BLE Switch mode now runs a dedicated Classic-BT HID "Pro Controller" backend with Switch subcommand reply handling (`0x01` output + `0x21/0x30` input reports) to improve pairing compatibility and home/wake behavior.
  - Wired Switch mode now uses `switch_ESP32` USB descriptor/profile path for Nintendo Switch-compatible wired gamepad enumeration on S3.
  - Output delivery is deterministic per emulation mode (Bluetooth modes stay BLE, wired modes stay USB); source input policy is configurable (`auto|wired|websocket|http`) independently.
  - BLE advertising name now changes per emulation mode (e.g. `ChromaCatch K + B`, `ChromaCatch Mouse`, `Xbox Wireless Controller`, `Pro Controller`).
  - Mode switch robustness: `POST /mode` now supports explicit BLE re-init (`force_reinit`) and exposes current BLE profile + advertised name in mode/status responses.
  - Mode persistence: last selected emulation mode and input policy are persisted in NVS (`Preferences`) and restored on boot, so devices can power-on directly into modes like wired Switch Pro.
  - BLE identity isolation: NimBLE modes now derive and apply a deterministic mode-scoped random MAC address per emulation mode, reducing host pairing confusion when switching between HID personalities.
  - BLE ops hardening commands: `restart_ble`, `clear_ble_bonds`, and `set_input_policy` (plus backward-compatible `set_delivery_policy` as input policy alias).
  - Build-time Wi-Fi config now supports environment injection: set `CC_WIFI_SSID` / `CC_WIFI_PASSWORD` (or `WIFI_SSID` / `WIFI_PASSWORD`) before `pio run` to avoid editing firmware source on pulls.
  - Wi-Fi env injection script now emits proper C string macros for SSID/password (fixes unquoted macro compile failures when using env vars).
  - Added dedicated `platformio` environment `esp32s3_switch` with `ARDUINO_USB_CDC_ON_BOOT=0` for cleaner Nintendo Switch USB controller enumeration on S3.
  - `esp32` environment now builds with dual framework (`arduino, espidf`) plus `sdkconfig.defaults.esp32` to enable Classic BT HID (`CONFIG_BT_HID_ENABLED=y`) required for Switch Pro Bluetooth emulation.
  - Switch wired stick mapping now follows Pokemon Automation semantics (inverted Y on wired Switch reports).
  - ESP32 classic-BT Switch mode now mirrors Pokemon Automation state semantics more closely: OEM button-byte mapping, 12-bit stick packing, and joystick magnitude projection thresholds (`min=1874`, `max=320`) used by PA’s Pro Controller path.
  - ESP32 classic-BT Switch mode still emits an explicit startup diagnostic when classic HID support is missing in the active SDK config, instead of silently failing with generic `hidd_dev_init` errors.
  - ESP32 classic-BT Switch mode now requires/enables `CONFIG_BT_HID_DEVICE_ENABLED` in the ESP32 sdkconfig (the flag used by `esp_hidd_dev_init(..., ESP_HID_TRANSPORT_BT, ...)`) to avoid runtime `hidd_dev_init failed: -1`.
  - ESP32 classic-BT Switch mode now aligns closer to established PA/BlueCon behavior for device-info status bytes, Bluetooth Class-of-Device gamepad signaling, and ~15ms wireless report cadence.
  - ESP32 classic-BT Switch mode now aligns further with EasyCon/BlueCon subcommand handling (`0x21` MCU set-mode ACK behavior) and default 0x30 input report markers (`status=0x80`, vibration ACK byte `0x08`).
  - Wired serial input now supports PA-style `SerialPABotBase` binary framing (inverted-length + CRC32C) in addition to legacy JSON lines, with request/ack handling for core PA protocol requests (versions, controller mode, status, MAC, controller list) and Switch/HID command message IDs (`0x90`, `0xa0`, `0xa1`, `0x82`).
  - PA-style long commands now use command ACK + queued timed execution + `PABB_MSG_REQUEST_COMMAND_FINISHED` callbacks (best-effort, queue size 4) so host-side PA command flow can complete without relying on JSON command semantics.
  - ESP32 Switch BT backend now includes a legacy Bluedroid HID API fallback path (`esp_bt_hid_device_*`) when `esp_hidd_dev_init(..., ESP_HID_TRANSPORT_BT, ...)` fails at runtime, improving compatibility with boards/core builds where the newer HID device init path is unstable.
  - BLE shutdown path now guards `NimBLEDevice::deinit()` behind `NimBLEDevice::isInitialized()`, preventing ESP32 boot-time mutex asserts when starting directly in classic-BT Switch mode.
  - ESP32 dual-framework (`arduino, espidf`) builds now auto-patch known `ESP32-BLE-CompositeHID` warning-as-error issues (constructor init-order + class-memaccess) so `esp32` environment compiles reliably under ESP-IDF `-Werror`.
  - Onboard status LED now reflects emulation mode with mode-specific colors and connection state: blinking while output transport is disconnected, solid once connected (S3 RGB on built-in NeoPixel; ESP32 mono fallback uses the same blink/solid behavior).
  - Wired Switch Pro mode now runs a periodic `switch_ESP32` loop tick so host detection/presence behaves like the upstream example’s continuous report cadence.
  - Emulation presets include bluetooth/wired mouse+keyboard variants, bluetooth Xbox-controller profile, bluetooth Switch-pro profile (ESP32 only), and wired Switch-pro profile (S3 only). Mode discovery via `GET /mode` and remote configuration via `POST /mode`.
- **Control SDK** (`services/control-sdk/`): Python SDK package for automation repos to consume control-plane REST + WebSocket APIs.
- **Shared** (`services/shared/`): Protocol contract between services — message models, frame codec, constants.

### Media Transport Modes

The client supports multiple transport modes for video/audio delivery, configurable via `CC_CLIENT_TRANSPORT_MODE`:

**RTP+FEC Mode** (`transport_mode=rtp-fec`) — Absolute lowest latency (~3-5ms LAN):
1. UxPlay decrypts AirPlay H.264 stream → RTP over localhost UDP
2. GStreamer extracts H.264 AUs (no decode) → Python RTP packetizer + zfec Reed-Solomon FEC
3. Sends RTP+FEC packets over UDP directly to backend (no intermediary)
4. Backend asyncio UDP receiver + FEC block recovery → H264Decoder (PyAV) → SessionManager
5. 30% FEC overhead (10 data + 3 parity shards), graceful loss recovery

**WebRTC Mode** (`transport_mode=webrtc`) — Lowest latency with NAT traversal:
1. UxPlay decrypts AirPlay H.264 stream → RTP over localhost UDP
2. GStreamer subprocess with `whipclientsink` forwards H.264 + Opus via WHIP to MediaMTX
3. MediaMTX serves as RTSP (for CV pipeline) and WebRTC/WHEP (for dashboard)
4. ICE/STUN/TURN for NAT traversal — works across networks
5. No decode/re-encode on client — Python never touches frame data

**SRT Mode** (`transport_mode=srt`) — Low-latency, recommended for UDP-capable hosts:
1. UxPlay decrypts AirPlay H.264 stream → RTP over localhost UDP
2. GStreamer subprocess forwards H.264 passthrough + Opus-encoded audio via SRT to MediaMTX
3. MediaMTX receives SRT, serves as RTSP (for CV pipeline) and WebRTC/WHEP (for dashboard)
4. Backend RTSP consumer reads frames from MediaMTX, feeds into CV pipeline
5. No decode/re-encode on client — Python never touches frame data

**H.264 WebSocket Mode** (`transport_mode=h264-ws`) — Cloud Run compatible, near-SRT efficiency:
1. UxPlay decrypts AirPlay H.264 stream → RTP over localhost UDP
2. GStreamer depayloads/parses H.264 → raw Annex B AUs piped to stdout (no decode/re-encode)
3. Client sends H.264 AUs directly over WebSocket (metadata + binary, ~10x smaller than JPEG)
4. Backend decodes H.264 using PyAV (FFmpeg), JPEG-encodes for dashboard
5. Works over TCP/HTTPS — compatible with Cloud Run, Fly.io, any HTTP-only platform

**WebSocket Mode** (`transport_mode=websocket`) — Fallback for low-power devices:
1. UxPlay decrypts H.264 → GStreamer captures frames (pipe or file backend)
2. Client JPEG-encodes frames (720px, q65), sends over WebSocket with PCM audio
3. Backend decodes frames directly from WebSocket

### Data Flow (Common)
1. iPhone runs Pokemon Go, screen mirrors via AirPlay to UxPlay (on local client)
2. Backend sends HID commands over WebSocket control channel to local client
3. Local client forwards command to ESP32 via HTTP (keep-alive connections)
4. ESP32 emits BLE HID mouse event to iPhone

### WebSocket Protocol
- **Frames (client → backend)**: Two-message pattern — JSON `FrameMetadata` followed by binary JPEG bytes (WS mode) or JSON `H264FrameMetadata` followed by binary H.264 AU bytes (h264-ws mode)
- **Commands (backend → client)**: JSON `HIDCommandMessage` (legacy) or `GameCommandMessage` (universal) with action + params
- **HID Mode (backend → client)**: JSON `SetHIDModeMessage` with `hid_mode` (combo/gamepad/mouse/keyboard) — switches BLE HID profile or ESP32 output mode
- **Command ACKs (client → backend)**: JSON `CommandAck` after command forward attempt
- **Audio (client → backend)**: JSON `AudioChunk` metadata + binary PCM payload
- **Status (client → backend)**: Periodic JSON `ClientStatus` updates
- **Channel split**:
  - `/ws/client` = frame/status channel
  - `/ws/control` = low-jitter command/ack channel (with backend fallback to frame channel)
- **Auth**: API key via query param or Authorization header

### Commander Abstraction

The client uses a pluggable `Commander` interface to route commands to different target devices. Configurable via `CC_CLIENT_COMMANDER_MODE`:

| Commander | Target | Protocol | Platform |
|-----------|--------|----------|----------|
| `esp32` | ESP32 → BLE HID (mouse/kb/gamepad) | HTTP POST + mode API | Any (existing) |
| `sysbotbase` | sys-botbase on Switch | TCP port 6000 (text) | Modded Switch |
| `luma3ds` | Luma3DS input redirect | UDP port 4950 (binary) | Modded 3DS |
| `virtual-gamepad` | OS-level gamepad (uinput/ViGEm) | OS API | Emulators |
| `dsu` | DSU/Cemuhook protocol | UDP port 26760 (binary) | Cemu, Dolphin, Citra |

- `CommandForwarder` (formerly `ESP32Forwarder`) translates `HIDCommandMessage` or `GameCommandMessage` → `Commander.send_command(action, params)` → `CommandAck`
- `GameCommandMessage` supports command types: `mouse`, `keyboard`, `gamepad`, `touch`
- Each commander handles its own connection lifecycle and protocol translation

## Project Structure

```
ChromaCatch-Go/
├── CLAUDE.md
├── pyproject.toml
├── conftest.py                              # Root conftest (sets up sys.path for services/)
├── services/
│   ├── shared/                              # Protocol contract between services
│   │   ├── constants.py                     # Message types, defaults
│   │   ├── messages.py                      # Pydantic models for all WS messages
│   │   ├── frame_codec.py                  # JPEG encode/decode with resize
│   │   └── rtp_fec_protocol.py             # RTP+FEC packet format, FEC params, header builders
│   ├── backend/                             # REMOTE: CV brain + command dispatch
│   │   ├── config.py                        # BackendSettings (CC_BACKEND_ prefix)
│   │   ├── main.py                          # FastAPI + WS endpoints + dashboard
│   │   ├── ws_handler.py                    # WebSocket frame/control handler
│   │   ├── h264_decoder.py                  # Streaming H.264 decoder (PyAV/FFmpeg)
│   │   ├── session_manager.py               # Dual-channel client session tracking
│   │   ├── mediamtx_manager.py              # MediaMTX subprocess lifecycle
│   │   ├── rtsp_consumer.py                 # RTSP frame consumer (reads from MediaMTX for CV)
│   │   ├── rtp_fec_receiver.py              # RTP+FEC receiver (asyncio UDP + zfec RS recovery)
│   │   ├── mediamtx/
│   │   │   └── mediamtx.yml                 # MediaMTX config (SRT/RTSP/WebRTC ports)
│   │   ├── cv/                              # Phase 2: computer vision
│   │   ├── orchestrator/                    # Phase 3: state machine
│   │   └── tests/                           # Backend tests
│   │       ├── test_backend_api.py
│   │       ├── test_session_manager.py
│   │       ├── test_ws_handler.py
│   │       ├── test_h264_decoder.py         # H264Decoder (PyAV) tests
│   │       ├── test_mediamtx_manager.py
│   │       ├── test_rtsp_consumer.py
│   │       ├── test_messages.py             # Shared protocol tests
│   │       ├── test_frame_codec.py          # Shared codec tests
│   │       └── integration/
│   │           └── test_client_backend.py   # End-to-end round-trip
│   ├── airplay-client/                      # LOCAL: AirPlay + ESP32 bridge (CLI tool)
│   │   ├── airplay_client/                  # Python package (airplay_client)
│   │   │   ├── cli.py                        # CLI entry point (connect, run)
│   │   │   ├── config.py                    # ClientSettings (CC_CLIENT_ prefix)
│   │   │   ├── main.py                      # asyncio entrypoint (transport + control WS)
│   │   │   ├── transport/
│   │   │   │   ├── base.py                  # MediaTransport ABC
│   │   │   │   ├── srt_transport.py         # SRT publisher (GStreamer→srtsink) + stats
│   │   │   │   ├── webrtc_transport.py      # WebRTC publisher (GStreamer→whipclientsink)
│   │   │   │   ├── rtp_fec_transport.py     # RTP+FEC sender (zfec RS + asyncio UDP, lowest latency)
│   │   │   │   ├── ws_transport.py          # WebSocket transport (JPEG frames + PCM)
│   │   │   │   ├── h264_ws_transport.py     # H.264 passthrough WS transport (raw AUs + PCM)
│   │   │   │   ├── failover_transport.py    # Primary→WS auto-failover with recovery
│   │   │   │   └── factory.py               # Transport factory (rtp-fec | webrtc | srt | *-failover | h264-ws | ws)
│   │   │   ├── audio/
│   │   │   │   ├── factory.py               # Runtime audio source selection
│   │   │   │   ├── airplay_audio_source.py  # AirPlay RTP audio source adapter
│   │   │   │   └── ffmpeg_audio_source.py   # System/capture-device audio adapter
│   │   │   ├── ws_client.py                 # WebSocket client (auto-reconnect + command ack)
│   │   │   ├── esp32_forwarder.py           # CommandForwarder: WS command → Commander (+ ack timing)
│   │   │   ├── capture/
│   │   │   │   ├── airplay_manager.py       # UxPlay process management
│   │   │   │   ├── frame_capture.py         # OpenCV GStreamer/FFmpeg frame capture
│   │   │   │   ├── h264_capture.py          # H.264 AU capture from GStreamer pipe (no decode)
│   │   │   │   └── audio_capture.py         # AirPlay RTP audio capture (PCM chunks)
│   │   │   ├── sources/
│   │   │   │   ├── airplay_source.py        # AirPlay/UxPlay source adapter
│   │   │   │   ├── capture_card_source.py   # USB capture/camera source adapter
│   │   │   │   ├── screen_source.py         # Desktop/window capture source adapter
│   │   │   │   ├── sysdvr_source.py         # SysDVR source (modded Switch, RTSP)
│   │   │   │   ├── ntr_source.py            # NTR source (modded 3DS, UDP JPEG)
│   │   │   │   └── factory.py               # Runtime source selection
│   │   │   └── commander/
│   │   │       ├── base.py                  # Commander ABC + CommandResult
│   │   │       ├── factory.py               # Commander factory (esp32 | sysbotbase | luma3ds | virtual-gamepad | dsu)
│   │   │       ├── esp32_client.py          # HTTP client for ESP32 commands + mode API
│   │   │       ├── esp32_commander.py       # ESP32Commander (wraps esp32_client, mode discovery)
│   │   │       ├── sysbotbase_client.py     # sys-botbase TCP (Switch, port 6000)
│   │   │       ├── luma3ds_client.py        # Luma3DS input redirect (3DS, UDP 4950)
│   │   │       ├── dsu_commander.py         # DSU/Cemuhook protocol (emulators, UDP 26760)
│   │   │       └── virtual_gamepad.py       # Virtual gamepad (Linux uinput / Windows ViGEm)
│   │   └── tests/                           # Client tests
│   │       ├── test_airplay_manager.py
│   │       ├── test_esp32_client.py
│   │       ├── test_esp32_forwarder.py      # ESP32Forwarder backward compat tests
│   │       ├── test_commander.py            # All commanders + factory + mode discovery + DSU
│   │       ├── test_command_forwarder.py    # CommandForwarder with GameCommandMessage
│   │       ├── test_stub_commanders.py      # sys-botbase, Luma3DS, virtual gamepad
│   │       ├── test_webrtc_transport.py     # WebRTC transport + factory tests
│   │       ├── test_rtp_fec_transport.py    # RTP+FEC protocol + transport + receiver (71 tests)
│   │       ├── test_sources.py              # SysDVR, NTR, source factory (17 tests)
│   │       ├── test_frame_capture.py
│   │       ├── test_h264_capture.py         # H264AUParser + keyframe detection tests
│   │       ├── test_h264_transport.py       # H264WebSocketTransport + factory tests
│   │       ├── test_transport.py            # SRT + WS transport + factory tests
│   │       ├── test_ws_client.py
│   │       └── test_cli.py
│   ├── ios-app/                              # iOS: controller app (video broadcast + input relay)
│   │   └── ChromaCatchController/
│   │       ├── ChromaCatchController.xcodeproj
│   │       ├── ChromaCatchController/
│   │       │   ├── ChromaCatchControllerApp.swift  # @main SwiftUI entry
│   │       │   ├── ContentView.swift              # UI (dashboard, video, input, settings)
│   │       │   ├── AppCoordinator.swift           # Control WS + ESP32/BLE HID orchestrator
│   │       │   ├── Info.plist                     # App metadata, BT + network permissions
│   │       │   ├── ChromaCatchController.entitlements  # App Group (group.com.chromacatch)
│   │       │   ├── Managers/
│   │       │   │   ├── BLEManager.swift           # CoreBluetooth central (FF12 service)
│   │       │   │   ├── EAManager.swift            # External Accessory session (MFi gate)
│   │       │   │   ├── WebSocketManager.swift     # URLSessionWebSocketTask (dual WS)
│   │       │   │   ├── LocationMonitor.swift      # CLLocationManager GPS verification + drift recovery
│   │       │   │   ├── DNSFilterManager.swift     # NETunnelProviderManager DNS sinkhole toggle
│   │       │   │   └── BLEHIDCommander.swift      # BLE HID peripheral (mouse/kb/gamepad to non-Apple hosts)
│   │       │   ├── Networking/
│   │       │   │   └── ESP32HTTPClient.swift      # HTTP POST /command to ESP32 (HID relay)
│   │       │   ├── Dongle/
│   │       │   │   ├── NMEAGenerator.swift        # RMC + GGA sentence builder
│   │       │   │   └── DongleController.swift     # AT+CN/RP init + NMEA loop
│   │       │   └── Protocol/
│   │       │       └── Messages.swift             # Swift mirror of shared/messages.py (full protocol)
│   │       ├── ChromaCatchBroadcast/              # ReplayKit Broadcast Upload Extension
│   │       │   ├── SampleHandler.swift            # RPBroadcastSampleHandler (screen capture → H.264)
│   │       │   ├── H264Encoder.swift              # VideoToolbox encoder (AVCC → Annex-B)
│   │       │   ├── BroadcastWSClient.swift        # Simplified WS client (h264-ws protocol)
│   │       │   ├── Info.plist                     # Extension config (broadcast-services-upload)
│   │       │   └── ChromaCatchBroadcast.entitlements  # App Group (group.com.chromacatch)
│   ├── ios-location-spoofer/                     # iOS: dedicated location spoof app package
│   │   ├── README.md
│   │   └── ChromaCatchLocationSpoof/
│   │       ├── ChromaCatchLocationSpoof.xcodeproj
│   │       ├── ChromaCatchController/            # spoof-focused SwiftUI app target
│   │       ├── ChromaCatchDNS/                   # DNS location guard extension
│   │       └── ChromaCatchBroadcast/             # retained copy (safe to remove after move if unused)
│   └── esp32/                               # ESP32 firmware (v2: multi-mode HID)
│       ├── platformio.ini                   # BLE Mouse/KB/Gamepad + GxEPD2 + AceButton
│       └── src/main.cpp                     # Multi-mode HID + e-ink menu + WiFi/Serial
│   └── control-sdk/                         # Python SDK consumed by external automation repos
│       ├── chromacatch_control_sdk/
│       └── tests/
└── scripts/
    ├── start.sh                             # Dev: run both services locally
    ├── start_client.sh                      # Launch client locally
    ├── start_backend.sh                     # Launch backend
    └── test_mouse.py                        # HID mouse movement test
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, uvicorn, pydantic |
| Media transport (primary) | WebRTC via GStreamer whipclientsink → MediaMTX WHIP |
| Media transport (alt) | SRT via GStreamer srtsink → MediaMTX → RTSP |
| Media transport (Cloud Run) | H.264 passthrough over WebSocket (h264-ws) |
| Media transport (fallback) | WebSocket (websockets library, JPEG + PCM) |
| Media router | MediaMTX (SRT ingest, RTSP local, WebRTC/WHEP dashboard) |
| CV | OpenCV (with GStreamer support), numpy |
| Frame encoding | JPEG via OpenCV (720px max, quality 65 default — WS mode only) |
| Audio encoding | Opus via GStreamer opusenc (128kbps — SRT mode) |
| AirPlay | UxPlay (C, installed separately) |
| Frame capture | GStreamer pipe (fdsink) or OpenCV GStreamer pipeline |
| ESP32 firmware | C++ / Arduino / PlatformIO, GxEPD2 e-ink, AceButton |
| ESP32 comms | WiFi HTTP (REST, keep-alive) or USB Serial (wired) |
| BLE HID | ESP32 NimBLE Mouse + BLE Keyboard + BLE Gamepad |
| H.264 decode | PyAV (av) — FFmpeg wrapper for backend H.264→BGR decode |
| iOS app | Swift, SwiftUI, CoreBluetooth, ExternalAccessory, URLSessionWebSocketTask |
| GPS spoofing | iTools BT dongle (Beken BK-BLE-1.0, MFi coprocessor, EA protocol) |
| Testing | pytest, pytest-asyncio (543 tests) |
| Linting | ruff, black, mypy |

## Phases

### Phase 1: Connectivity [COMPLETE]
- [x] ESP32 BLE HID firmware v2 (multi-mode: Mouse+KB, Gamepad; e-ink menu; WiFi HTTP + USB Serial input; mode discovery API)
- [x] AirPlay receiver management (UxPlay wrapper)
- [x] Frame capture service (GStreamer RTP → OpenCV)
- [x] Shared protocol (messages, frame codec)
- [x] WebSocket client (local client → remote backend)
- [x] WebSocket server (backend receives frames, dispatches commands)
- [x] ESP32 command forwarder (backend → client → ESP32)
- [x] CLI tool packaging (pip-installable `chromacatch-client`)
- [x] Backend live dashboard with MJPEG frame streaming
- [x] HID mouse test script
- [x] GStreamer CLI capture stability fixes (single long-lived pipeline + reliable resolution detection from caps)
- [x] Dual WebSocket channels (`/ws/client`, `/ws/control`) with command sequencing + ACK telemetry
- [x] Unified client frame source layer (AirPlay, capture card, desktop capture)
- [x] Initial AirPlay audio transport (`UxPlay -artp` + backend audio chunk ingestion)

### Phase 1.5: Low-Latency Transport [COMPLETE]
- [x] ESP32 HTTP keep-alive (persistent connections, ~20-30ms command latency reduction)
- [x] GStreamer jitter buffer reduction (50ms → 20ms)
- [x] Pipe-based frame capture backend (GStreamer stdout JPEG, eliminates file I/O)
- [x] SRT media transport (`SRTTransport` — H.264 passthrough + Opus audio via GStreamer srtsink)
- [x] WebSocket media transport (`WebSocketTransport` — JPEG + PCM fallback)
- [x] SRT failover transport (`FailoverTransport` — auto WS fallback after 3 SRT failures, periodic SRT recovery)
- [x] Transport factory and `MediaTransport` ABC (supports `srt`, `srt-failover`, `websocket` modes)
- [x] SRT stats parsing (RTT, bandwidth, packet loss from GStreamer stderr)
- [x] Frame latency instrumentation (capture timestamp → backend receipt tracking)
- [x] MediaMTX integration (subprocess manager, mediamtx.yml config)
- [x] RTSP frame consumer (MediaMTX RTSP → SessionManager for CV pipeline)
- [x] Dashboard WebRTC/WHEP support with audio playback (+ MJPEG fallback)
- [x] Dashboard SRT stats + frame latency display
- [x] Client main.py refactored for transport mode selection
- [x] H.264 passthrough WebSocket transport (`H264WebSocketTransport` — raw H.264 AUs over WS, Cloud Run compatible)
- [x] H264Capture (GStreamer pipe: udpsrc → rtph264depay → h264parse → fdsink, AU boundary/keyframe detection)
- [x] H264Decoder (backend PyAV/FFmpeg streaming H.264 → BGR decode)
- [x] 260 tests passing (95 new transport + failover + MediaMTX + RTSP + H.264 tests)

### Phase 1.7: Location Spoofing + iOS Split Apps [IN PROGRESS]
- [x] iTools BT dongle protocol reverse-engineered (AT+CN/RP init, NMEA RMC+GGA over BLE FF03)
- [x] Standalone location service extracted to external `ChromaCatch-Go` repo (decoupled from control-plane backend)
- [x] iOS app scaffold (SwiftUI, CoreBluetooth BLEManager, EA EAManager, WebSocket, NMEA generator)
- [x] Dongle controller (AT+CN init → NMEA loop at 1Hz with RP status monitoring)
- [x] iOS dual WebSocket (main backend `/ws/control` + location service `/ws/location`)
- [x] iOS ESP32 HID relay (receive command → HTTP POST to ESP32 → CommandAck back to backend)
- [x] iOS Protocol/Messages.swift aligned with full Python protocol (CommandAck, H264FrameMetadata, AudioChunkMetadata, ClientStatus)
- [x] ReplayKit Broadcast Upload Extension (H.264 via VideoToolbox → h264-ws protocol over WebSocket)
- [x] App Group IPC (shared UserDefaults between main app and broadcast extension)
- [x] GPS location verification (LocationMonitor: CLLocationManager polling + haversine drift + auto-recovery)
- [x] DNS filter extension (NEPacketTunnelProvider sinkhole for Apple Wi-Fi/cell positioning domains)
- [x] iOS app split into two deliverables:
  - `services/ios-app/` for video/input control + ReplayKit broadcast
  - `services/ios-location-spoofer/` for location spoofing + DNS location guard
- [x] 265 tests passing (location service tests now live in external `ChromaCatch-Go` repo)
- [ ] On-device testing: verify EA session activates dongle GPS forwarding (RP status `>`)
- [ ] End-to-end: external location service POST /location → iOS app WS → BLE NMEA → iPhone location change
- [ ] End-to-end: ReplayKit broadcast → H.264 frames on backend dashboard (same stream as CLI)

### Phase 2A: Universal Source / Transport / Input [COMPLETE]
- [x] Commander ABC + CommandResult model (`commander/base.py`)
- [x] ESP32Commander adapter wrapping existing ESP32Client (with mode discovery via GET /mode)
- [x] Commander factory (`commander/factory.py`) — mode-based creation
- [x] GameCommandMessage type in shared protocol (supports mouse/keyboard/gamepad/touch)
- [x] CommandForwarder generalized from ESP32Forwarder — routes via Commander interface
- [x] WebRTC transport (`webrtc_transport.py`) — GStreamer whipclientsink to MediaMTX WHIP
- [x] Transport factory updated for webrtc + webrtc-failover + rtp-fec modes
- [x] SysBotbaseCommander — sys-botbase TCP for modded Switch (text commands)
- [x] Luma3DSCommander — Luma3DS input redirect for modded 3DS (UDP binary packets)
- [x] VirtualGamepadCommander — uinput (Linux) / ViGEm (Windows) for emulators
- [x] DSUCommander — Cemuhook/DSU protocol for emulators (Cemu, Dolphin, Citra)
- [x] RTP+FEC transport (`rtp_fec_transport.py`) — custom UDP + Reed-Solomon FEC via zfec, lowest latency (~3-5ms LAN)
- [x] RTP+FEC receiver (`rtp_fec_receiver.py`) — backend asyncio UDP + FEC recovery + AU reassembly → H264Decoder
- [x] Shared RTP+FEC protocol (`shared/rtp_fec_protocol.py`) — packet format, FEC parameters, header builders
- [x] SysDVR frame source (`sysdvr_source.py`) — RTSP capture from modded Switch via OpenCV
- [x] NTR frame source (`ntr_source.py`) — UDP JPEG listener from modded 3DS with multi-packet reassembly
- [x] Source factory updated for sysdvr + ntr modes
- [x] ESP32 firmware v2 — e-ink display menu + buttons, BLE Mouse+KB/Gamepad modes, WiFi HTTP + USB Serial input, GET/POST /mode API
- [x] ESP32Client updated with get_mode() and set_mode() for mode discovery/configuration
- [x] iOS BLE HID commander (`BLEHIDCommander.swift`) — CBPeripheralManager with expanded 128-bit UUID, mouse/kb/gamepad profiles
- [x] iOS BLE HID stability hardening — boot input characteristics (2A22/2A33), robust read/write handling, thread-safe peripheral queue, background peripheral mode
- [x] iOS BLE HID UX controls — disconnect/discoverable reset, host scan/connect list, combo↔gamepad mode switch, local touch trackpad/keyboard + gamepad pane (backend commands prioritized)
- [x] 491 tests passing (226 new tests across all sprints)

### Phase 2: Computer Vision
- [ ] Screen state detection (battle, overworld, menu, etc.)
- [ ] Pokemon encounter detection
- [ ] Shiny detection (color comparison techniques)
- [ ] UI element recognition (buttons, prompts)

### Phase 3: Shiny Hunt Automation
- [ ] Hunt loop state machine
- [ ] Action sequences (encounter → check → flee/catch)
- [ ] Error recovery (crash detection, reconnection)
- [ ] Logging and statistics

## Development Commands

```bash
# Install
poetry install

# Run all tests (543 tests)
poetry run pytest

# Run by suite
poetry run pytest services/backend/tests/              # Backend + shared + integration
poetry run pytest services/airplay-client/tests/        # Client component tests
poetry run pytest services/control-sdk/tests/           # Control SDK tests
poetry run pytest services/backend/tests/integration/   # End-to-end round-trip only

# Start backend (cloud/remote)
./scripts/start_backend.sh
# Dashboard: http://localhost:8000/dashboard

# Start client (local, near iPhone + ESP32)
./scripts/start_client.sh

# Start both (dev mode)
./scripts/start.sh

# Install client as CLI tool (on MacBook)
pip install ./services/airplay-client
chromacatch-client connect --backend-url ws://<host>:8000/ws/client
chromacatch-client run --backend-url ws://<host>:8000/ws/client

# Test HID mouse commands
python scripts/test_mouse.py --backend-url http://<host>:8000

# Install MediaMTX (for SRT transport)
./scripts/install_mediamtx.sh

# ESP32 Firmware (requires PlatformIO)
cd services/esp32 && pio run              # Build
cd services/esp32 && pio run -t upload    # Flash
```

## Configuration

**Client** (`.env` with `CC_CLIENT_` prefix):
```bash
CC_CLIENT_BACKEND_WS_URL=wss://your-backend.run.app/ws/client
CC_CLIENT_BACKEND_CONTROL_WS_URL=wss://your-backend.run.app/ws/control
CC_CLIENT_CLIENT_ID=macbook-station-1
CC_CLIENT_API_KEY=your-secret-key
CC_CLIENT_ESP32_HOST=192.168.1.100
CC_CLIENT_ESP32_PORT=80
CC_CLIENT_AIRPLAY_UDP_PORT=5000
CC_CLIENT_AIRPLAY_AUDIO_UDP_PORT=5002
CC_CLIENT_AIRPLAY_NAME=ChromaCatch
CC_CLIENT_CLEANUP_STALE_AIRPLAY_PROCESSES=true
CC_CLIENT_SINGLE_INSTANCE_LOCK_PATH=/tmp    # lock dir; prevents duplicate client_id processes
CC_CLIENT_CAPTURE_SOURCE=airplay            # airplay | capture | screen
CC_CLIENT_CAPTURE_DEVICE=0                  # device index/path for capture source
CC_CLIENT_CAPTURE_FPS=30
CC_CLIENT_SCREEN_MONITOR=1                  # used by screen source
CC_CLIENT_SCREEN_REGION=                    # optional x,y,width,height
CC_CLIENT_AIRPLAY_RECONNECT_TIMEOUT_S=8.0  # restart capture if stream stalls post-connect
CC_CLIENT_JPEG_QUALITY=65
CC_CLIENT_MAX_DIMENSION=720
CC_CLIENT_FRAME_INTERVAL_MS=33
CC_CLIENT_AUDIO_ENABLED=true
CC_CLIENT_AUDIO_SOURCE=auto                 # auto | airplay | system | none
CC_CLIENT_AUDIO_SAMPLE_RATE=44100
CC_CLIENT_AUDIO_CHANNELS=2
CC_CLIENT_AUDIO_CHUNK_MS=100
CC_CLIENT_AUDIO_INPUT_BACKEND=auto          # auto | avfoundation | pulse | dshow
CC_CLIENT_AUDIO_INPUT_DEVICE=               # backend-specific input selector
# Commander (input target)
CC_CLIENT_COMMANDER_MODE=esp32              # esp32 | sysbotbase | luma3ds | virtual-gamepad | dsu
CC_CLIENT_COMMANDER_HOST=192.168.1.100      # Target host (ESP32, Switch, 3DS)
CC_CLIENT_COMMANDER_PORT=0                  # Target port (0 = use default per commander)
# Transport settings
CC_CLIENT_TRANSPORT_MODE=websocket          # rtp-fec | webrtc | srt | *-failover | h264-ws | websocket
CC_CLIENT_RTP_FEC_DEST_HOST=               # Backend host for UDP delivery (auto-derived if empty)
CC_CLIENT_RTP_FEC_DEST_PORT=7000           # Backend UDP port for RTP+FEC receiver
CC_CLIENT_SRT_BACKEND_URL=                  # srt://host:8890 (auto-derived from WS URL if empty)
CC_CLIENT_SRT_LATENCY_MS=50                 # SRT latency buffer
CC_CLIENT_SRT_PASSPHRASE=                   # optional SRT encryption
CC_CLIENT_SRT_STREAM_ID=                    # auto-derived from client_id if empty
CC_CLIENT_SRT_OPUS_BITRATE=128000           # Opus audio bitrate (SRT/WebRTC mode)
CC_CLIENT_WEBRTC_WHIP_URL=                  # Auto-derived from backend URL if empty
CC_CLIENT_WEBRTC_STUN_SERVER=stun://stun.l.google.com:19302
CC_CLIENT_WEBRTC_TURN_SERVER=               # Optional TURN for symmetric NATs
CC_CLIENT_WEBRTC_TURN_USERNAME=
CC_CLIENT_WEBRTC_TURN_PASSWORD=
```

> Location service has been extracted to the external `ChromaCatch-Go` repo.

**Backend** (`.env` with `CC_BACKEND_` prefix):
```bash
CC_BACKEND_API_KEY=your-secret-key
CC_BACKEND_HOST=0.0.0.0
CC_BACKEND_PORT=8000
CC_BACKEND_MAX_FRAME_BYTES=500000
# MediaMTX settings (for SRT transport)
CC_BACKEND_MEDIAMTX_ENABLED=false           # Enable MediaMTX subprocess management
CC_BACKEND_MEDIAMTX_BINARY=mediamtx         # Path or name of MediaMTX binary
CC_BACKEND_MEDIAMTX_CONFIG=                 # Path to mediamtx.yml (auto-detected if empty)
CC_BACKEND_MEDIAMTX_SRT_PORT=8890
CC_BACKEND_MEDIAMTX_RTSP_PORT=8554
CC_BACKEND_MEDIAMTX_WEBRTC_PORT=8889
CC_BACKEND_RTSP_CONSUMER_ENABLED=false      # Enable RTSP frame consumer for CV pipeline
CC_BACKEND_RTSP_BASE_URL=rtsp://127.0.0.1:8554
# RTP+FEC receiver (lowest-latency UDP transport)
CC_BACKEND_RTP_FEC_ENABLED=false            # Enable RTP+FEC receiver
CC_BACKEND_RTP_FEC_BIND_HOST=0.0.0.0
CC_BACKEND_RTP_FEC_BIND_PORT=7000
CC_BACKEND_RTP_FEC_CLIENT_ID=rtp-fec       # client_id for session manager
```

## Backend API

| Method | Endpoint | Description |
|--------|----------|-------------|
| WS | `/ws/client` | Frame/status WebSocket (frames up, command fallback) |
| WS | `/ws/control` | Low-latency command/ack WebSocket |
| GET | `/health` | Health check |
| GET | `/status` | Connected clients count |
| POST | `/command` | Legacy command endpoint (backward compatible) |
| POST | `/hid-mode` | Legacy HID mode switch endpoint (backward compatible) |
| GET | `/clients/{id}/status` | Legacy client status endpoint |
| GET | `/clients/{id}/frame` | Legacy latest-frame endpoint |
| GET | `/stream/{id}` | Legacy MJPEG stream endpoint |
| GET | `/dashboard` | Browser dashboard (WebRTC + MJPEG fallback) |

## Client API (`/api/client/v1`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| WS | `/api/client/v1/ws/client` | Frame/status channel for clients |
| WS | `/api/client/v1/ws/control` | Low-latency command + ack channel |
| GET | `/api/client/v1/clients` | List connected clients |
| POST | `/api/client/v1/commands` | Send command to one/all clients |
| POST | `/api/client/v1/hid-mode` | Switch client HID profile |

## Automation API (`/api/automation/v1`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/automation/v1/clients` | List connected clients for automation bots |
| GET | `/api/automation/v1/clients/{id}/frame` | Get latest frame (JPEG bytes) |
| POST | `/api/automation/v1/cv/template-match` | Template matching against latest client frame |
| POST | `/api/automation/v1/commands` | Send automation command |
| WS | `/api/automation/v1/ws/commands` | Low-latency automation command channel |

> GPS/location APIs are now hosted in the external `ChromaCatch-Go` repo.
