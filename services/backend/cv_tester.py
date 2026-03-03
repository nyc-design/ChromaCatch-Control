"""Visual CV tool tester — runs locate/confirm/extract tools on live frames with overlay.

Provides a dashboard at /test/cv with:
- Reference image upload or server-path input
- Tool selector (all 25 tools)
- Tool-specific params editor
- Live MJPEG stream with bounding box overlays for locate tools
- Real-time score/match/details display
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# Add cv-toolkit to import path for the running server
_services = Path(__file__).resolve().parent.parent
if str(_services / "cv-toolkit") not in sys.path:
    sys.path.insert(0, str(_services / "cv-toolkit"))

from cv_toolkit import run_tool  # noqa: E402
from cv_toolkit.models import ToolInput  # noqa: E402
from cv_toolkit.registry import TOOL_REGISTRY  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/test", tags=["cv-test"])

# ── In-memory test config (one active test at a time) ──

_test_config: dict = {
    "tool": None,
    "reference": None,       # np.ndarray or None
    "reference_name": None,  # filename for display
    "threshold": 0.5,
    "params": {},
    "region": None,
}


def _load_reference_from_path(path: str) -> np.ndarray:
    """Load a reference image from a server-side file path."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Cannot load image: {path}")
    return img


def _draw_overlay(
    frame: np.ndarray,
    result: dict,
    tool_name: str,
    elapsed_ms: float,
) -> np.ndarray:
    """Draw bounding boxes, scores, and info bar on a frame copy."""
    annotated = frame.copy()
    h, w = annotated.shape[:2]

    matches = result.get("details", {}).get("matches", [])
    score = result.get("score", 0.0)
    is_match = result.get("match", False)
    threshold = result.get("threshold", 0.5)

    # Draw bounding boxes for locate tools
    for i, m in enumerate(matches):
        bbox = m.get("bbox", {})
        conf = m.get("confidence", 0.0)
        x1 = int(bbox.get("x", 0) * w)
        y1 = int(bbox.get("y", 0) * h)
        x2 = int((bbox.get("x", 0) + bbox.get("w", 0)) * w)
        y2 = int((bbox.get("y", 0) + bbox.get("h", 0)) * h)

        green = int(min(1.0, conf) * 255)
        red = int(min(1.0, 1.0 - conf) * 255)
        color = (0, green, red)

        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        label = f"#{i + 1} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(annotated, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # Info bar at top
    match_color = (0, 200, 100) if is_match else (0, 80, 200)
    cv2.rectangle(annotated, (0, 0), (w, 32), (30, 30, 30), -1)
    info = f"{tool_name} | score={score:.3f} | thresh={threshold} | {len(matches)} matches | {elapsed_ms:.0f}ms"
    cv2.putText(annotated, info, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, match_color, 1)

    return annotated


# ── REST Endpoints ──


@router.post("/cv/config")
async def set_test_config(
    tool: str = Query(..., description="Tool name (e.g. locate_template)"),
    threshold: float = Query(0.5, ge=0.0, le=1.0),
    params: str = Query("{}", description="JSON object of tool-specific params"),
    reference_path: str | None = Query(None, description="Server-side path to reference image"),
    region: str | None = Query(None, description="JSON region {x, y, w, h} normalized 0-1"),
):
    """Configure the active CV test (tool, params, reference)."""
    if tool not in TOOL_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}. Available: {sorted(TOOL_REGISTRY.keys())}")

    parsed_params = json.loads(params)
    parsed_region = json.loads(region) if region else None

    _test_config["tool"] = tool
    _test_config["threshold"] = threshold
    _test_config["params"] = parsed_params
    _test_config["region"] = parsed_region

    if reference_path:
        try:
            _test_config["reference"] = _load_reference_from_path(reference_path)
            _test_config["reference_name"] = Path(reference_path).name
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {"status": "configured", "tool": tool, "threshold": threshold, "has_reference": _test_config["reference"] is not None}


@router.post("/cv/reference")
async def upload_reference(file: UploadFile = File(...)):
    """Upload a reference image for the current test."""
    data = await file.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise HTTPException(status_code=400, detail="Cannot decode image")
    _test_config["reference"] = img
    _test_config["reference_name"] = file.filename
    return {"status": "uploaded", "filename": file.filename, "shape": list(img.shape)}


@router.get("/cv/config")
async def get_test_config():
    """Get the current test configuration."""
    ref = _test_config["reference"]
    return {
        "tool": _test_config["tool"],
        "threshold": _test_config["threshold"],
        "params": _test_config["params"],
        "region": _test_config["region"],
        "has_reference": ref is not None,
        "reference_name": _test_config["reference_name"],
        "reference_shape": list(ref.shape) if ref is not None else None,
        "available_tools": sorted(TOOL_REGISTRY.keys()),
    }


@router.get("/cv/tools")
async def list_tools():
    """List all available CV tools with their categories."""
    locate_tools = [t for t in TOOL_REGISTRY if t.startswith("locate_")]
    confirm_tools = [t for t in TOOL_REGISTRY if not t.startswith(("locate_", "read_"))]
    extract_tools = [t for t in TOOL_REGISTRY if t.startswith("read_")]
    return {
        "locate": sorted(locate_tools),
        "confirm": sorted(confirm_tools),
        "extract": sorted(extract_tools),
        "total": len(TOOL_REGISTRY),
    }


@router.get("/cv/stream/{client_id}")
async def cv_stream(client_id: str):
    """MJPEG stream with CV tool overlay on live frames."""
    # Import here to avoid circular import with main.py
    from backend.main import session_manager

    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")

    if not _test_config["tool"]:
        raise HTTPException(status_code=400, detail="No tool configured. POST /test/cv/config first.")

    return StreamingResponse(
        _annotated_mjpeg_generator(client_id, session_manager),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


async def _annotated_mjpeg_generator(client_id: str, session_manager):
    """Yield annotated JPEG frames as MJPEG stream."""
    last_sequence = -1
    gone_count = 0

    while True:
        session = session_manager.get_session(client_id)
        if session is None:
            gone_count += 1
            if gone_count > 300:
                return
            await asyncio.sleep(0.1)
            continue
        gone_count = 0

        frame = session_manager.get_latest_frame(client_id)
        seq = session.latest_frame_sequence
        if frame is not None and seq != last_sequence:
            last_sequence = seq

            tool_name = _test_config["tool"]
            if not tool_name:
                await asyncio.sleep(0.05)
                continue

            try:
                tool_input = ToolInput(
                    tool=tool_name,
                    threshold=_test_config["threshold"],
                    params=_test_config["params"],
                    region=_test_config["region"],
                )
                t0 = time.monotonic()
                result = run_tool(frame, tool_input, reference=_test_config["reference"])
                elapsed_ms = (time.monotonic() - t0) * 1000

                annotated = _draw_overlay(frame, result.model_dump(), tool_name, elapsed_ms)
            except Exception as e:
                annotated = frame.copy()
                h_f, w_f = annotated.shape[:2]
                cv2.rectangle(annotated, (0, 0), (w_f, 32), (0, 0, 80), -1)
                cv2.putText(annotated, f"ERROR: {e}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1)

            _, jpeg_bytes = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
            jpeg = jpeg_bytes.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )
        else:
            await asyncio.sleep(0.01)


# ── Static test: run tool on a single uploaded image ──


@router.post("/cv/run")
async def run_single(
    file: UploadFile = File(...),
    tool: str | None = Query(None),
    threshold: float | None = Query(None),
    params: str | None = Query(None),
):
    """Run the configured tool on a single uploaded image and return annotated JPEG."""
    data = await file.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="Cannot decode image")

    tool_name = tool or _test_config["tool"]
    if not tool_name:
        raise HTTPException(status_code=400, detail="No tool specified")

    t_threshold = threshold if threshold is not None else _test_config["threshold"]
    t_params = json.loads(params) if params else _test_config["params"]

    tool_input = ToolInput(tool=tool_name, threshold=t_threshold, params=t_params, region=_test_config["region"])
    t0 = time.monotonic()
    result = run_tool(frame, tool_input, reference=_test_config["reference"])
    elapsed_ms = (time.monotonic() - t0) * 1000

    annotated = _draw_overlay(frame, result.model_dump(), tool_name, elapsed_ms)
    _, jpeg_bytes = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])

    return StreamingResponse(
        iter([jpeg_bytes.tobytes()]),
        media_type="image/jpeg",
        headers={"X-CV-Result": json.dumps(result.model_dump())},
    )


# ── Dashboard HTML ──

CV_TESTER_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>CV Tool Tester — ChromaCatch-Go</title>
    <meta charset="utf-8">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0d1117; color: #c9d1d9; }
        .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px;
                  display: flex; align-items: center; gap: 16px; }
        .header h1 { font-size: 18px; color: #58a6ff; }
        .header a { color: #8b949e; text-decoration: none; font-size: 14px; }
        .header a:hover { color: #58a6ff; }
        .main { display: grid; grid-template-columns: 340px 1fr; height: calc(100vh - 49px); }
        .sidebar { background: #161b22; border-right: 1px solid #30363d; padding: 16px;
                   overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
        .section { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
        .section h3 { font-size: 13px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px;
                      margin-bottom: 8px; }
        label { display: block; font-size: 13px; color: #8b949e; margin-bottom: 4px; }
        select, input[type=text], input[type=number], textarea {
            width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
            border-radius: 4px; padding: 6px 8px; font-size: 13px; font-family: inherit; }
        select:focus, input:focus, textarea:focus { outline: none; border-color: #58a6ff; }
        textarea { font-family: 'SF Mono', 'Fira Code', monospace; resize: vertical; min-height: 80px; }
        input[type=range] { width: 100%; accent-color: #58a6ff; }
        .btn { background: #238636; color: #fff; border: none; border-radius: 6px; padding: 8px 16px;
               font-size: 13px; cursor: pointer; width: 100%; font-weight: 600; }
        .btn:hover { background: #2ea043; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-secondary { background: #30363d; }
        .btn-secondary:hover { background: #484f58; }
        .stream-area { display: flex; flex-direction: column; overflow: hidden; }
        .stream-container { flex: 1; display: flex; align-items: center; justify-content: center;
                           background: #010409; position: relative; min-height: 0; }
        .stream-container img { max-width: 100%; max-height: 100%; object-fit: contain; }
        .placeholder { color: #484f58; font-size: 14px; text-align: center; padding: 40px; }
        .result-bar { background: #161b22; border-top: 1px solid #30363d; padding: 10px 16px;
                     font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px;
                     max-height: 120px; overflow-y: auto; }
        .ref-preview { max-width: 100%; max-height: 100px; border-radius: 4px; margin-top: 8px;
                      border: 1px solid #30363d; }
        .client-selector { display: flex; gap: 8px; align-items: center; }
        .client-selector select { flex: 1; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950;
                     display: inline-block; }
        .status-dot.offline { background: #f85149; }
        .file-drop { border: 2px dashed #30363d; border-radius: 6px; padding: 16px;
                    text-align: center; cursor: pointer; transition: border-color 0.2s; }
        .file-drop:hover, .file-drop.dragover { border-color: #58a6ff; }
        .file-drop input { display: none; }
        .row { display: flex; gap: 8px; }
        .row > * { flex: 1; }
        .tool-category { color: #484f58; font-size: 11px; padding: 4px 8px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>CV Tool Tester</h1>
        <a href="/dashboard">&larr; Dashboard</a>
    </div>
    <div class="main">
        <div class="sidebar">
            <!-- Client Selector -->
            <div class="section">
                <h3>Client</h3>
                <div class="client-selector">
                    <select id="clientSelect"><option value="">Loading...</option></select>
                    <span id="clientDot" class="status-dot offline"></span>
                </div>
            </div>

            <!-- Tool Selector -->
            <div class="section">
                <h3>Tool</h3>
                <select id="toolSelect"></select>
            </div>

            <!-- Reference Image -->
            <div class="section">
                <h3>Reference Image</h3>
                <div class="row" style="margin-bottom: 8px;">
                    <div class="file-drop" id="fileDrop" onclick="document.getElementById('fileInput').click()">
                        Drop image or click to upload
                        <input type="file" id="fileInput" accept="image/*">
                    </div>
                </div>
                <label>Or server path:</label>
                <input type="text" id="refPath" placeholder="/path/to/reference.png">
                <img id="refPreview" class="ref-preview" style="display:none">
                <div id="refInfo" style="font-size:12px; color:#8b949e; margin-top:4px;"></div>
            </div>

            <!-- Threshold -->
            <div class="section">
                <h3>Threshold</h3>
                <div class="row">
                    <input type="range" id="threshold" min="0" max="1" step="0.01" value="0.5">
                    <input type="number" id="thresholdNum" min="0" max="1" step="0.01" value="0.5" style="width:60px; flex:none">
                </div>
            </div>

            <!-- Params -->
            <div class="section">
                <h3>Params (JSON)</h3>
                <textarea id="paramsInput">{}</textarea>
            </div>

            <!-- Region -->
            <div class="section">
                <h3>Region (optional)</h3>
                <textarea id="regionInput" style="min-height:40px" placeholder='{"x": 0, "y": 0, "w": 1, "h": 1}'></textarea>
            </div>

            <!-- Actions -->
            <button class="btn" id="startBtn" onclick="startStream()">Start Live Test</button>
            <button class="btn btn-secondary" id="stopBtn" onclick="stopStream()" style="display:none">Stop</button>
            <button class="btn btn-secondary" onclick="testSingleFrame()">Test Single Frame</button>
        </div>
        <div class="stream-area">
            <div class="stream-container" id="streamContainer">
                <div class="placeholder" id="streamPlaceholder">
                    Select a client and tool, then click "Start Live Test"
                </div>
                <img id="streamImg" style="display:none" alt="CV test stream">
            </div>
            <div class="result-bar" id="resultBar">Ready</div>
        </div>
    </div>

    <script>
        const toolSelect = document.getElementById('toolSelect');
        const clientSelect = document.getElementById('clientSelect');
        const clientDot = document.getElementById('clientDot');
        const threshold = document.getElementById('threshold');
        const thresholdNum = document.getElementById('thresholdNum');
        const paramsInput = document.getElementById('paramsInput');
        const regionInput = document.getElementById('regionInput');
        const streamImg = document.getElementById('streamImg');
        const streamPlaceholder = document.getElementById('streamPlaceholder');
        const resultBar = document.getElementById('resultBar');
        const startBtn = document.getElementById('startBtn');
        const stopBtn = document.getElementById('stopBtn');
        const refPath = document.getElementById('refPath');
        const refPreview = document.getElementById('refPreview');
        const refInfo = document.getElementById('refInfo');
        const fileInput = document.getElementById('fileInput');
        const fileDrop = document.getElementById('fileDrop');

        let streaming = false;

        // Sync threshold slider and number input
        threshold.oninput = () => { thresholdNum.value = threshold.value; };
        thresholdNum.oninput = () => { threshold.value = thresholdNum.value; };

        // Tool defaults
        const TOOL_DEFAULTS = {
            locate_color: { hsv_lower: [0, 100, 100], hsv_upper: [10, 255, 255] },
            locate_template: { color_filters: [[[0, 100, 100], [10, 255, 255]]], rmsd_threshold: 80.0 },
            locate_contour: { canny_low: 50, canny_high: 150, min_hu_similarity: 0.3 },
            locate_text: { target: "text_to_find" },
            locate_feature: { detector: "orb", max_features: 500, min_matches: 10 },
            locate_multi_scale: { scale_range: [0.5, 2.0], scale_steps: 20 },
            locate_sub_object: { sub_reference_region: { x: 0, y: 0, w: 0.5, h: 0.5 }, sub_color_filters: [[[0, 100, 100], [10, 255, 255]]] },
            exact_match: { brightness_clamp: 0.15, use_stddev_weight: true },
            ssim_compare: { window_size: 11 },
            read_text: {},
            read_number: {},
            read_bar: { bar_hsv_lower: [0, 100, 100], bar_hsv_upper: [10, 255, 255] },
        };

        // Populate tool dropdown
        async function loadTools() {
            const resp = await fetch('/test/cv/tools');
            const data = await resp.json();
            toolSelect.innerHTML = '';
            for (const [cat, tools] of [['locate', data.locate], ['confirm', data.confirm], ['extract', data.extract]]) {
                const group = document.createElement('optgroup');
                group.label = cat.charAt(0).toUpperCase() + cat.slice(1) + ' (' + tools.length + ')';
                for (const t of tools) {
                    const opt = document.createElement('option');
                    opt.value = t;
                    opt.textContent = t;
                    group.appendChild(opt);
                }
                toolSelect.appendChild(group);
            }
            // Set default params for first tool
            toolSelect.onchange = () => {
                const defaults = TOOL_DEFAULTS[toolSelect.value];
                if (defaults) paramsInput.value = JSON.stringify(defaults, null, 2);
            };
            toolSelect.dispatchEvent(new Event('change'));
        }

        // Populate client dropdown
        async function loadClients() {
            try {
                const resp = await fetch('/status');
                const data = await resp.json();
                const current = clientSelect.value;
                clientSelect.innerHTML = '';
                if (data.connected_clients.length === 0) {
                    clientSelect.innerHTML = '<option value="">No clients connected</option>';
                    clientDot.className = 'status-dot offline';
                    return;
                }
                for (const c of data.connected_clients) {
                    const opt = document.createElement('option');
                    opt.value = c;
                    opt.textContent = c;
                    clientSelect.appendChild(opt);
                }
                if (current && data.connected_clients.includes(current)) {
                    clientSelect.value = current;
                }
                clientDot.className = 'status-dot';
            } catch(e) {
                clientSelect.innerHTML = '<option value="">Error loading</option>';
                clientDot.className = 'status-dot offline';
            }
        }

        // File upload
        fileInput.onchange = async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            await uploadReference(file);
        };

        fileDrop.ondragover = (e) => { e.preventDefault(); fileDrop.classList.add('dragover'); };
        fileDrop.ondragleave = () => { fileDrop.classList.remove('dragover'); };
        fileDrop.ondrop = async (e) => {
            e.preventDefault();
            fileDrop.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) await uploadReference(file);
        };

        async function uploadReference(file) {
            const fd = new FormData();
            fd.append('file', file);
            const resp = await fetch('/test/cv/reference', { method: 'POST', body: fd });
            const data = await resp.json();
            if (resp.ok) {
                refPreview.src = URL.createObjectURL(file);
                refPreview.style.display = 'block';
                refInfo.textContent = data.filename + ' (' + data.shape.join('x') + ')';
            } else {
                refInfo.textContent = 'Error: ' + data.detail;
            }
        }

        // Apply config and start/stop stream
        async function applyConfig() {
            const tool = toolSelect.value;
            const th = parseFloat(threshold.value);
            const params = paramsInput.value.trim() || '{}';
            const region = regionInput.value.trim() || '';
            const refPathVal = refPath.value.trim();

            let url = '/test/cv/config?tool=' + encodeURIComponent(tool)
                + '&threshold=' + th
                + '&params=' + encodeURIComponent(params);
            if (region) url += '&region=' + encodeURIComponent(region);
            if (refPathVal) url += '&reference_path=' + encodeURIComponent(refPathVal);

            const resp = await fetch(url, { method: 'POST' });
            const data = await resp.json();
            if (!resp.ok) {
                resultBar.textContent = 'Config error: ' + data.detail;
                return false;
            }
            resultBar.textContent = 'Configured: ' + tool + ' (threshold=' + th + ')';
            return true;
        }

        async function startStream() {
            const clientId = clientSelect.value;
            if (!clientId) { resultBar.textContent = 'No client selected'; return; }

            const ok = await applyConfig();
            if (!ok) return;

            streaming = true;
            streamImg.src = '/test/cv/stream/' + clientId + '?t=' + Date.now();
            streamImg.style.display = 'block';
            streamPlaceholder.style.display = 'none';
            startBtn.style.display = 'none';
            stopBtn.style.display = 'block';
            resultBar.textContent = 'Streaming with ' + toolSelect.value + '...';

            streamImg.onerror = () => {
                if (streaming) {
                    resultBar.textContent = 'Stream disconnected, reconnecting...';
                    setTimeout(() => {
                        if (streaming) streamImg.src = '/test/cv/stream/' + clientId + '?t=' + Date.now();
                    }, 2000);
                }
            };
        }

        function stopStream() {
            streaming = false;
            streamImg.src = '';
            streamImg.style.display = 'none';
            streamPlaceholder.style.display = 'block';
            startBtn.style.display = 'block';
            stopBtn.style.display = 'none';
            resultBar.textContent = 'Stopped';
        }

        async function testSingleFrame() {
            const clientId = clientSelect.value;
            if (!clientId) { resultBar.textContent = 'No client selected'; return; }

            const ok = await applyConfig();
            if (!ok) return;

            // Fetch latest frame and run tool on it
            resultBar.textContent = 'Running...';
            try {
                const frameResp = await fetch('/clients/' + clientId + '/frame');
                if (!frameResp.ok) { resultBar.textContent = 'No frame available'; return; }
                const blob = await frameResp.blob();
                const fd = new FormData();
                fd.append('file', blob, 'frame.jpg');

                const tool = toolSelect.value;
                const th = parseFloat(threshold.value);
                const params = paramsInput.value.trim() || '{}';
                let url = '/test/cv/run?tool=' + encodeURIComponent(tool)
                    + '&threshold=' + th
                    + '&params=' + encodeURIComponent(params);

                const resp = await fetch(url, { method: 'POST', body: fd });
                if (!resp.ok) {
                    const err = await resp.json();
                    resultBar.textContent = 'Error: ' + err.detail;
                    return;
                }

                const resultHeader = resp.headers.get('X-CV-Result');
                if (resultHeader) {
                    const result = JSON.parse(resultHeader);
                    resultBar.textContent = JSON.stringify(result, null, 2);
                }

                const imgBlob = await resp.blob();
                streamImg.src = URL.createObjectURL(imgBlob);
                streamImg.style.display = 'block';
                streamPlaceholder.style.display = 'none';
            } catch(e) {
                resultBar.textContent = 'Error: ' + e.message;
            }
        }

        // Init
        loadTools();
        loadClients();
        setInterval(loadClients, 5000);
    </script>
</body>
</html>"""


@router.get("/cv", response_class=HTMLResponse)
async def cv_test_page():
    """Visual CV tool tester dashboard."""
    return HTMLResponse(content=CV_TESTER_HTML)
