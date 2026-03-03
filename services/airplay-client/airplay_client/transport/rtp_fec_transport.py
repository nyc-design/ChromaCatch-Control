"""RTP+FEC media transport — lowest latency H.264 delivery over UDP.

Reads H.264 Access Units from GStreamer pipe (via H264Capture), packetizes
into RTP packets with Reed-Solomon FEC parity, and sends via UDP.

No GStreamer subprocess for transport — Python handles packetization directly.
This eliminates SRT's jitter buffer and TCP's head-of-line blocking.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from random import randint

from airplay_client.capture.h264_capture import H264Capture
from airplay_client.config import client_settings as settings
from airplay_client.transport.base import MediaTransport
from shared.rtp_fec_protocol import (
    CC_HEADER_SIZE,
    FEC_DATA_SHARDS,
    FEC_PARITY_SHARDS,
    FEC_TOTAL_SHARDS,
    FLAG_KEYFRAME,
    FLAG_LAST_BLOCK,
    PAYLOAD_SIZE,
    RTP_CLOCK_RATE,
    RTP_HEADER_SIZE,
    build_cc_header,
    build_rtp_header,
)

logger = logging.getLogger(__name__)


class RTPFECTransport(MediaTransport):
    """Sends H.264 AUs over UDP with Reed-Solomon FEC."""

    def __init__(
        self,
        h264_capture: H264Capture | None = None,
        dest_host: str | None = None,
        dest_port: int | None = None,
        audio_enabled: bool | None = None,
    ):
        self._h264_capture = h264_capture
        self._dest_host = dest_host or settings.rtp_fec_dest_host or "localhost"
        self._dest_port = dest_port or settings.rtp_fec_dest_port or 7000
        self._audio_enabled = audio_enabled if audio_enabled is not None else settings.audio_enabled
        self._running = False
        self._connected = False
        self._transport: asyncio.DatagramTransport | None = None
        self._send_task: asyncio.Task | None = None
        self._seq = 0
        self._frame_id = 0
        self._ssrc = randint(0, 0xFFFFFFFF)
        self._timestamp = 0
        self._frames_sent = 0
        self._bytes_sent = 0
        self._fec_encoder = None  # Lazy init

    def _get_encoder(self):
        """Lazy-init zfec encoder."""
        if self._fec_encoder is None:
            import zfec
            self._fec_encoder = zfec.Encoder(FEC_DATA_SHARDS, FEC_TOTAL_SHARDS)
        return self._fec_encoder

    def _packetize_au(self, au_bytes: bytes, is_keyframe: bool) -> list[bytes]:
        """Split H.264 AU into RTP+FEC packets."""
        encoder = self._get_encoder()
        self._frame_id = (self._frame_id + 1) & 0xFFFF

        # Split AU into payload-sized chunks
        chunks: list[bytes] = []
        for i in range(0, len(au_bytes), PAYLOAD_SIZE):
            chunks.append(au_bytes[i : i + PAYLOAD_SIZE])
        if not chunks:
            return []

        # Process in FEC blocks (up to FEC_DATA_SHARDS chunks per block)
        all_packets: list[bytes] = []
        num_blocks = (len(chunks) + FEC_DATA_SHARDS - 1) // FEC_DATA_SHARDS

        for block_idx in range(num_blocks):
            start = block_idx * FEC_DATA_SHARDS
            block_chunks = chunks[start : start + FEC_DATA_SHARDS]
            actual_data_count = len(block_chunks)
            is_last_block = (block_idx == num_blocks - 1)

            # Record original length of last chunk before padding
            last_orig_len = len(block_chunks[-1]) if block_chunks else 0

            # Pad all chunks to PAYLOAD_SIZE
            padded = [c.ljust(PAYLOAD_SIZE, b"\x00") for c in block_chunks]

            # Pad block to exactly FEC_DATA_SHARDS if undersized
            while len(padded) < FEC_DATA_SHARDS:
                padded.append(b"\x00" * PAYLOAD_SIZE)

            # Generate FEC: returns DATA + PARITY shards
            all_shards = encoder.encode(padded)

            # Build packets for each shard (data + parity)
            for shard_idx in range(FEC_TOTAL_SHARDS):
                is_data = shard_idx < FEC_DATA_SHARDS
                # Skip data shards beyond actual data (padding shards)
                # Still send them — receiver needs them for FEC math

                flags = 0
                if is_keyframe:
                    flags |= FLAG_KEYFRAME
                if is_last_block:
                    flags |= FLAG_LAST_BLOCK

                # orig_len only meaningful for last data shard
                orig_len = 0
                if shard_idx == actual_data_count - 1:
                    orig_len = last_orig_len & 0xFF

                marker = is_data and shard_idx == actual_data_count - 1 and is_last_block

                rtp_hdr = build_rtp_header(
                    seq=self._seq,
                    timestamp=self._timestamp,
                    ssrc=self._ssrc,
                    marker=marker,
                )
                cc_hdr = build_cc_header(
                    frame_id=self._frame_id,
                    block_id=block_idx,
                    shard_index=shard_idx,
                    data_shards=actual_data_count,
                    total_shards=FEC_TOTAL_SHARDS,
                    flags=flags,
                    orig_len=orig_len,
                )
                packet = rtp_hdr + cc_hdr + all_shards[shard_idx]
                all_packets.append(packet)
                self._seq = (self._seq + 1) & 0xFFFF

        # Advance RTP timestamp by one frame (~33ms at 30fps)
        self._timestamp = (self._timestamp + RTP_CLOCK_RATE // 30) & 0xFFFFFFFF
        return all_packets

    async def _send_loop(self) -> None:
        """Read AUs from H264Capture and send as RTP+FEC packets."""
        if self._h264_capture is None:
            logger.error("RTP+FEC transport requires h264_capture")
            return

        while self._running:
            try:
                result = await asyncio.to_thread(self._h264_capture.get_au, 0.5)
                if result is None:
                    continue

                au_bytes, is_keyframe, ts = result
                packets = self._packetize_au(au_bytes, is_keyframe)

                for pkt in packets:
                    if self._transport and not self._transport.is_closing():
                        self._transport.sendto(pkt, (self._dest_host, self._dest_port))
                        self._bytes_sent += len(pkt)

                self._frames_sent += 1

                if not self._connected and self._frames_sent > 0:
                    self._connected = True
                    logger.info(
                        "RTP+FEC transport connected → %s:%d",
                        self._dest_host,
                        self._dest_port,
                    )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("RTP+FEC send error")
                await asyncio.sleep(0.1)

    async def start(self) -> None:
        """Start the RTP+FEC transport."""
        self._running = True

        # Create UDP socket
        loop = asyncio.get_event_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=(self._dest_host, self._dest_port),
        )
        logger.info(
            "RTP+FEC transport started → %s:%d (FEC %d+%d, payload %dB)",
            self._dest_host,
            self._dest_port,
            FEC_DATA_SHARDS,
            FEC_PARITY_SHARDS,
            PAYLOAD_SIZE,
        )

        self._send_task = asyncio.create_task(self._send_loop())

    async def stop(self) -> None:
        """Stop the RTP+FEC transport."""
        self._running = False
        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
            self._send_task = None
        if self._transport:
            self._transport.close()
            self._transport = None
        self._connected = False
        logger.info("RTP+FEC transport stopped (%d frames sent)", self._frames_sent)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def transport_name(self) -> str:
        return "rtp-fec"

    @property
    def frames_sent(self) -> int:
        return self._frames_sent

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent
