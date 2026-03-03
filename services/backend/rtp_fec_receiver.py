"""RTP+FEC receiver — receives UDP packets, recovers lost data via FEC, outputs H.264 AUs.

Runs as an asyncio DatagramProtocol. Collects packets per FEC block, applies
Reed-Solomon recovery (zfec), reassembles H.264 Access Units, and feeds them
into the existing H264Decoder + SessionManager pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from backend.h264_decoder import H264Decoder
from backend.session_manager import SessionManager
from shared.rtp_fec_protocol import (
    CC_HEADER_SIZE,
    FEC_DATA_SHARDS,
    HEADER_SIZE,
    PAYLOAD_SIZE,
    RTP_HEADER_SIZE,
    parse_cc_header,
    parse_rtp_header,
)

logger = logging.getLogger(__name__)

# How long to wait for a complete FEC block before giving up (ms)
_BLOCK_TIMEOUT_MS = 50


@dataclass
class FECBlock:
    """Collects shards for a single FEC block."""

    frame_id: int
    block_id: int
    data_shards: int
    total_shards: int
    flags: int
    orig_len: int  # original length of the last data shard
    shards: dict[int, bytes] = field(default_factory=dict)  # shard_index → payload
    received_at: float = field(default_factory=time.monotonic)

    @property
    def complete(self) -> bool:
        """Have we received enough shards to recover all data?"""
        return len(self.shards) >= self.data_shards

    @property
    def all_data_received(self) -> bool:
        """Have we received all data shards (no FEC needed)?"""
        return all(i in self.shards for i in range(self.data_shards))


@dataclass
class FrameAssembly:
    """Collects FEC blocks for a single frame."""

    frame_id: int
    blocks: dict[int, bytes] = field(default_factory=dict)  # block_id → recovered data
    is_keyframe: bool = False
    is_complete: bool = False
    received_at: float = field(default_factory=time.monotonic)


class RTPFECProtocol(asyncio.DatagramProtocol):
    """Asyncio protocol that receives RTP+FEC packets and outputs H.264 AUs."""

    def __init__(
        self,
        session_manager: SessionManager,
        client_id: str = "rtp-fec",
    ) -> None:
        self._session_manager = session_manager
        self._client_id = client_id
        self._decoder = H264Decoder()
        self._fec_decoder = None  # Lazy init
        self._pending_blocks: dict[tuple[int, int], FECBlock] = {}  # (frame_id, block_id)
        self._pending_frames: dict[int, FrameAssembly] = {}
        self._packets_received = 0
        self._frames_decoded = 0
        self._fec_recoveries = 0
        self._transport: asyncio.DatagramTransport | None = None

    def _get_fec_decoder(self, k: int, m: int):
        """Lazy-init zfec decoder with specific parameters."""
        import zfec
        # Cache for the common case
        if self._fec_decoder is None or self._fec_decoder_params != (k, m):
            self._fec_decoder = zfec.Decoder(k, m)
            self._fec_decoder_params = (k, m)
        return self._fec_decoder

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport
        logger.info("RTP+FEC receiver listening")

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if len(data) < HEADER_SIZE:
            return

        self._packets_received += 1

        # Parse headers
        rtp = parse_rtp_header(data[:RTP_HEADER_SIZE])
        cc = parse_cc_header(data[RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        payload = data[HEADER_SIZE:]

        frame_id = cc["frame_id"]
        block_id = cc["block_id"]
        key = (frame_id, block_id)

        # Get or create FEC block
        if key not in self._pending_blocks:
            self._pending_blocks[key] = FECBlock(
                frame_id=frame_id,
                block_id=block_id,
                data_shards=cc["data_shards"],
                total_shards=cc["total_shards"],
                flags=cc["flags"],
                orig_len=cc["orig_len"],
            )
        block = self._pending_blocks[key]

        # Store shard (pad to PAYLOAD_SIZE if needed)
        shard = payload.ljust(PAYLOAD_SIZE, b"\x00") if len(payload) < PAYLOAD_SIZE else payload[:PAYLOAD_SIZE]
        block.shards[cc["shard_index"]] = shard

        # Update orig_len from the shard that carries it
        if cc["orig_len"] > 0:
            block.orig_len = cc["orig_len"]

        # Update flags
        block.flags |= cc["flags"]

        # Try to complete the block
        if block.complete:
            self._complete_block(block)

    def _complete_block(self, block: FECBlock) -> None:
        """Recover data from a complete FEC block and add to frame assembly."""
        key = (block.frame_id, block.block_id)

        if block.all_data_received:
            # Fast path: all data shards present, no FEC needed
            data_parts = [block.shards[i] for i in range(block.data_shards)]
        else:
            # FEC recovery needed
            try:
                decoder = self._get_fec_decoder(block.data_shards, block.total_shards)
                available = []
                indices = []
                for idx in sorted(block.shards.keys()):
                    available.append(block.shards[idx])
                    indices.append(idx)
                    if len(available) >= block.data_shards:
                        break

                data_parts = list(decoder.decode(available, indices))
                self._fec_recoveries += 1
                lost = block.data_shards - sum(1 for i in range(block.data_shards) if i in block.shards)
                logger.debug("FEC recovered %d lost shards for frame %d block %d", lost, block.frame_id, block.block_id)
            except Exception:
                logger.warning("FEC recovery failed for frame %d block %d", block.frame_id, block.block_id)
                self._pending_blocks.pop(key, None)
                return

        # Trim the last data shard to its original length
        if block.orig_len > 0 and data_parts:
            data_parts[-1] = data_parts[-1][: block.orig_len]

        block_data = b"".join(data_parts)

        # Add to frame assembly
        frame_id = block.frame_id
        if frame_id not in self._pending_frames:
            self._pending_frames[frame_id] = FrameAssembly(frame_id=frame_id)
        frame = self._pending_frames[frame_id]
        frame.blocks[block.block_id] = block_data
        if block.flags & 0x01:  # FLAG_KEYFRAME
            frame.is_keyframe = True
        if block.flags & 0x02:  # FLAG_LAST_BLOCK
            frame.is_complete = True

        # Clean up block
        self._pending_blocks.pop(key, None)

        # Try to deliver the frame
        if frame.is_complete and all(i in frame.blocks for i in range(max(frame.blocks.keys()) + 1)):
            self._deliver_frame(frame)

    def _deliver_frame(self, frame: FrameAssembly) -> None:
        """Decode H.264 AU and push to SessionManager."""
        au_data = b"".join(frame.blocks[i] for i in sorted(frame.blocks.keys()))
        self._pending_frames.pop(frame.frame_id, None)

        try:
            bgr = self._decoder.decode(au_data)
            if bgr is not None:
                import cv2
                _, jpeg_bytes = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 65])
                # Fire-and-forget update (we're in a sync callback, schedule coroutine)
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self._update_session(bgr, bytes(jpeg_bytes))
                    )
                )
                self._frames_decoded += 1
        except Exception:
            logger.exception("H.264 decode failed for frame %d", frame.frame_id)

    async def _update_session(self, bgr, jpeg_bytes: bytes) -> None:
        """Push decoded frame to SessionManager."""
        self._session_manager.update_frame(self._client_id, bgr, jpeg_bytes)

    def _cleanup_stale(self) -> None:
        """Remove stale pending blocks/frames (called periodically)."""
        now = time.monotonic()
        cutoff = now - (_BLOCK_TIMEOUT_MS / 1000.0)

        stale_blocks = [k for k, b in self._pending_blocks.items() if b.received_at < cutoff]
        for k in stale_blocks:
            del self._pending_blocks[k]

        stale_frames = [fid for fid, f in self._pending_frames.items() if f.received_at < cutoff]
        for fid in stale_frames:
            del self._pending_frames[fid]

    @property
    def packets_received(self) -> int:
        return self._packets_received

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded

    @property
    def fec_recoveries(self) -> int:
        return self._fec_recoveries


class RTPFECReceiver:
    """Manages the RTP+FEC receiver lifecycle."""

    def __init__(
        self,
        session_manager: SessionManager,
        bind_host: str = "0.0.0.0",
        bind_port: int = 7000,
        client_id: str = "rtp-fec",
    ) -> None:
        self._session_manager = session_manager
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._client_id = client_id
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: RTPFECProtocol | None = None
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start listening for RTP+FEC packets."""
        loop = asyncio.get_event_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: RTPFECProtocol(self._session_manager, self._client_id),
            local_addr=(self._bind_host, self._bind_port),
        )
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("RTP+FEC receiver started on %s:%d", self._bind_host, self._bind_port)

    async def _cleanup_loop(self) -> None:
        """Periodically clean up stale blocks/frames."""
        while True:
            try:
                await asyncio.sleep(0.1)
                if self._protocol:
                    self._protocol._cleanup_stale()
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        """Stop the receiver."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        if self._transport:
            self._transport.close()
        logger.info("RTP+FEC receiver stopped")

    @property
    def protocol(self) -> RTPFECProtocol | None:
        return self._protocol
