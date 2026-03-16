"""Unified RTP+FEC transport — consumes CaptureProvider, supports audio.

Sends H.264/H.265 video AUs with Reed-Solomon FEC over UDP.
Audio (Opus) is interleaved on the same UDP socket with PT=97 (no FEC).
"""

from __future__ import annotations

import asyncio
import logging
import time
from random import randint

from airplay_client.capture_provider.base import CaptureProvider
from airplay_client.config import client_settings as settings
from airplay_client.transport.base import MediaTransport
from shared.rtp_fec_protocol import (
    FEC_DATA_SHARDS,
    FEC_PARITY_SHARDS,
    FEC_TOTAL_SHARDS,
    FLAG_KEYFRAME,
    FLAG_LAST_BLOCK,
    PAYLOAD_SIZE,
    RTP_AUDIO_CLOCK_RATE,
    RTP_CLOCK_RATE,
    build_audio_rtp_packet,
    build_cc_header,
    build_rtp_header,
)

logger = logging.getLogger(__name__)


class UnifiedRTPFECTransport(MediaTransport):
    """RTP+FEC transport consuming CaptureProvider for video + audio."""

    def __init__(self, dest_host: str | None = None, dest_port: int | None = None):
        self._dest_host = dest_host or settings.rtp_fec_dest_host or "localhost"
        self._dest_port = dest_port or settings.rtp_fec_dest_port or 7000
        self._running = False
        self._connected = False
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._video_task: asyncio.Task | None = None
        self._audio_task: asyncio.Task | None = None
        self._provider: CaptureProvider | None = None
        # Video RTP state
        self._video_seq = 0
        self._frame_id = 0
        self._ssrc = randint(0, 0xFFFFFFFF)
        self._video_timestamp = 0
        # Audio RTP state
        self._audio_seq = 0
        self._audio_ssrc = randint(0, 0xFFFFFFFF)
        self._audio_timestamp = 0
        # Counters
        self._frames_sent = 0
        self._audio_frames_sent = 0
        self._bytes_sent = 0
        self._fec_encoder = None

    def _get_fec_encoder(self):
        if self._fec_encoder is None:
            import zfec
            self._fec_encoder = zfec.Encoder(FEC_DATA_SHARDS, FEC_TOTAL_SHARDS)
        return self._fec_encoder

    def _packetize_au(self, au_data: bytes, is_keyframe: bool) -> list[bytes]:
        """Split encoded AU into RTP+FEC packets."""
        encoder = self._get_fec_encoder()
        self._frame_id = (self._frame_id + 1) & 0xFFFF

        chunks: list[bytes] = []
        for i in range(0, len(au_data), PAYLOAD_SIZE):
            chunks.append(au_data[i : i + PAYLOAD_SIZE])
        if not chunks:
            return []

        all_packets: list[bytes] = []
        num_blocks = (len(chunks) + FEC_DATA_SHARDS - 1) // FEC_DATA_SHARDS

        for block_idx in range(num_blocks):
            start = block_idx * FEC_DATA_SHARDS
            block_chunks = chunks[start : start + FEC_DATA_SHARDS]
            actual_data_count = len(block_chunks)
            is_last_block = block_idx == num_blocks - 1
            last_orig_len = len(block_chunks[-1]) if block_chunks else 0

            padded = [c.ljust(PAYLOAD_SIZE, b"\x00") for c in block_chunks]
            while len(padded) < FEC_DATA_SHARDS:
                padded.append(b"\x00" * PAYLOAD_SIZE)

            all_shards = encoder.encode(padded)

            for shard_idx in range(FEC_TOTAL_SHARDS):
                flags = 0
                if is_keyframe:
                    flags |= FLAG_KEYFRAME
                if is_last_block:
                    flags |= FLAG_LAST_BLOCK

                orig_len = 0
                if shard_idx == actual_data_count - 1:
                    orig_len = last_orig_len & 0xFF

                marker = (
                    shard_idx < FEC_DATA_SHARDS
                    and shard_idx == actual_data_count - 1
                    and is_last_block
                )

                rtp_hdr = build_rtp_header(
                    seq=self._video_seq, timestamp=self._video_timestamp, ssrc=self._ssrc, marker=marker
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
                all_packets.append(rtp_hdr + cc_hdr + all_shards[shard_idx])
                self._video_seq = (self._video_seq + 1) & 0xFFFF

        self._video_timestamp = (self._video_timestamp + RTP_CLOCK_RATE // 30) & 0xFFFFFFFF
        return all_packets

    async def _video_send_loop(self) -> None:
        """Pull video AUs from CaptureProvider and send as RTP+FEC."""
        while self._running and self._provider:
            try:
                au = await self._provider.get_au(0.5)
                if au is None:
                    continue

                packets = self._packetize_au(au.data, au.is_keyframe)
                for pkt in packets:
                    if self._udp_transport and not self._udp_transport.is_closing():
                        self._udp_transport.sendto(pkt, (self._dest_host, self._dest_port))
                        self._bytes_sent += len(pkt)

                self._frames_sent += 1
                if not self._connected and self._frames_sent > 0:
                    self._connected = True
                    logger.info("RTP+FEC connected → %s:%d", self._dest_host, self._dest_port)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("RTP+FEC video send error")
                await asyncio.sleep(0.1)

    async def _audio_send_loop(self) -> None:
        """Pull audio frames from CaptureProvider and send as RTP (no FEC)."""
        while self._running and self._provider:
            try:
                audio = await self._provider.get_audio(0.1)
                if audio is None:
                    continue

                pkt = build_audio_rtp_packet(
                    opus_data=audio.data,
                    seq=self._audio_seq,
                    timestamp=self._audio_timestamp,
                    ssrc=self._audio_ssrc,
                )
                if self._udp_transport and not self._udp_transport.is_closing():
                    self._udp_transport.sendto(pkt, (self._dest_host, self._dest_port))
                    self._bytes_sent += len(pkt)

                self._audio_seq = (self._audio_seq + 1) & 0xFFFF
                self._audio_timestamp = (
                    self._audio_timestamp + RTP_AUDIO_CLOCK_RATE * audio.duration_ms // 1000
                ) & 0xFFFFFFFF
                self._audio_frames_sent += 1

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("RTP+FEC audio send error")
                await asyncio.sleep(0.1)

    async def start(self) -> None:
        """Legacy start — no-op, use start_with_provider()."""
        raise RuntimeError("UnifiedRTPFECTransport requires start_with_provider()")

    async def start_with_provider(self, provider: CaptureProvider) -> None:
        """Start transport, pulling AUs from the given CaptureProvider."""
        self._provider = provider
        self._running = True

        loop = asyncio.get_event_loop()
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=(self._dest_host, self._dest_port),
        )
        logger.info(
            "Unified RTP+FEC started → %s:%d (FEC %d+%d, audio=%s)",
            self._dest_host, self._dest_port,
            FEC_DATA_SHARDS, FEC_PARITY_SHARDS,
            provider.has_audio,
        )

        self._video_task = asyncio.create_task(self._video_send_loop())
        if provider.has_audio:
            self._audio_task = asyncio.create_task(self._audio_send_loop())

    async def stop(self) -> None:
        self._running = False
        for task in [self._video_task, self._audio_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._video_task = None
        self._audio_task = None
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        self._connected = False
        logger.info("Unified RTP+FEC stopped (%d video, %d audio frames)", self._frames_sent, self._audio_frames_sent)

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
    def audio_frames_sent(self) -> int:
        return self._audio_frames_sent

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent
