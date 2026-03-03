"""RTP + FEC protocol constants and packet structures.

Defines the wire format for the custom RTP+FEC transport — the lowest-latency
option in the transport stack. Uses Reed-Solomon erasure coding (zfec) over UDP.

Packet layout:
  [RTP Header (12B)] [ChromaCatch Header (8B)] [Payload (up to PAYLOAD_SIZE)]

ChromaCatch Header:
  - frame_id     (u16): monotonically increasing frame counter
  - block_id     (u8):  FEC block index within this frame (0-3)
  - shard_index  (u8):  index within the FEC block (0..total_shards-1)
  - data_shards  (u8):  number of data shards in this FEC block
  - total_shards (u8):  data + parity shards
  - flags        (u8):  bit 0 = keyframe, bit 1 = last block in frame
  - orig_len     (u8):  original payload length of the last shard (for unpadding)
"""

from __future__ import annotations

import struct

# --- FEC Parameters ---
FEC_DATA_SHARDS = 10    # data packets per FEC block
FEC_PARITY_SHARDS = 3   # parity packets per block (30% overhead)
FEC_TOTAL_SHARDS = FEC_DATA_SHARDS + FEC_PARITY_SHARDS

# --- Packet sizes ---
RTP_HEADER_SIZE = 12
CC_HEADER_SIZE = 8       # ChromaCatch custom header
HEADER_SIZE = RTP_HEADER_SIZE + CC_HEADER_SIZE
MAX_MTU = 1500
IP_UDP_OVERHEAD = 28     # 20 IP + 8 UDP
PAYLOAD_SIZE = MAX_MTU - IP_UDP_OVERHEAD - HEADER_SIZE  # ~1452 bytes

# --- RTP ---
RTP_VERSION = 2
RTP_PAYLOAD_TYPE = 96    # dynamic PT for video
RTP_CLOCK_RATE = 90000   # standard video clock

# --- Default ports ---
DEFAULT_RTP_FEC_PORT = 7000

# --- Flags ---
FLAG_KEYFRAME = 0x01
FLAG_LAST_BLOCK = 0x02


def build_rtp_header(
    seq: int,
    timestamp: int,
    ssrc: int,
    marker: bool = False,
    pt: int = RTP_PAYLOAD_TYPE,
) -> bytes:
    """Build a 12-byte RTP header (RFC 3550)."""
    byte0 = (RTP_VERSION << 6)  # V=2, P=0, X=0, CC=0
    byte1 = pt | (0x80 if marker else 0x00)
    return struct.pack("!BBHII", byte0, byte1, seq & 0xFFFF, timestamp & 0xFFFFFFFF, ssrc)


def parse_rtp_header(data: bytes) -> dict:
    """Parse a 12-byte RTP header."""
    byte0, byte1, seq, timestamp, ssrc = struct.unpack("!BBHII", data[:RTP_HEADER_SIZE])
    return {
        "version": (byte0 >> 6) & 3,
        "pt": byte1 & 0x7F,
        "marker": bool(byte1 & 0x80),
        "seq": seq,
        "timestamp": timestamp,
        "ssrc": ssrc,
    }


def build_cc_header(
    frame_id: int,
    block_id: int,
    shard_index: int,
    data_shards: int,
    total_shards: int,
    flags: int,
    orig_len: int,
) -> bytes:
    """Build 8-byte ChromaCatch FEC header."""
    return struct.pack(
        "!HBBBBBB",
        frame_id & 0xFFFF,
        block_id & 0xFF,
        shard_index & 0xFF,
        data_shards & 0xFF,
        total_shards & 0xFF,
        flags & 0xFF,
        orig_len & 0xFF,
    )


def parse_cc_header(data: bytes) -> dict:
    """Parse 8-byte ChromaCatch FEC header."""
    frame_id, block_id, shard_index, data_shards, total_shards, flags, orig_len = struct.unpack(
        "!HBBBBBB", data[:CC_HEADER_SIZE]
    )
    return {
        "frame_id": frame_id,
        "block_id": block_id,
        "shard_index": shard_index,
        "data_shards": data_shards,
        "total_shards": total_shards,
        "flags": flags,
        "orig_len": orig_len,
        "is_keyframe": bool(flags & FLAG_KEYFRAME),
        "is_last_block": bool(flags & FLAG_LAST_BLOCK),
    }
