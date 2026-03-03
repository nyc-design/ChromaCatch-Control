"""Tests for RTP+FEC transport (sender, receiver, protocol)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.rtp_fec_protocol import (
    CC_HEADER_SIZE,
    FEC_DATA_SHARDS,
    FEC_TOTAL_SHARDS,
    FLAG_KEYFRAME,
    FLAG_LAST_BLOCK,
    HEADER_SIZE,
    PAYLOAD_SIZE,
    RTP_HEADER_SIZE,
    build_cc_header,
    build_rtp_header,
    parse_cc_header,
    parse_rtp_header,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_encoder(data_shards: int = FEC_DATA_SHARDS, total_shards: int = FEC_TOTAL_SHARDS):
    """Return a mock zfec.Encoder whose encode() produces PAYLOAD_SIZE-padded shards."""
    encoder = MagicMock()
    def fake_encode(padded):
        # Return total_shards shards, each exactly PAYLOAD_SIZE bytes
        return [p.ljust(PAYLOAD_SIZE, b"\x00")[:PAYLOAD_SIZE] for p in padded] + [b"\x00" * PAYLOAD_SIZE] * (total_shards - data_shards)
    encoder.encode = MagicMock(side_effect=fake_encode)
    return encoder


def _make_transport_with_mock_encoder(**kwargs):
    """Create RTPFECTransport with a pre-injected fake FEC encoder."""
    from airplay_client.transport.rtp_fec_transport import RTPFECTransport
    t = RTPFECTransport(**kwargs)
    t._fec_encoder = _make_fake_encoder()
    return t


# ---------------------------------------------------------------------------
# TestRTPProtocol — wire format unit tests
# ---------------------------------------------------------------------------

class TestRTPProtocol:
    """Tests for RTP+FEC packet format."""

    def test_rtp_header_roundtrip(self):
        """Build and parse RTP header — all fields survive."""
        hdr = build_rtp_header(seq=1234, timestamp=90000, ssrc=0xDEADBEEF, marker=True)
        assert len(hdr) == RTP_HEADER_SIZE
        parsed = parse_rtp_header(hdr)
        assert parsed["version"] == 2
        assert parsed["seq"] == 1234
        assert parsed["timestamp"] == 90000
        assert parsed["ssrc"] == 0xDEADBEEF
        assert parsed["marker"] is True

    def test_rtp_header_no_marker(self):
        """Marker bit is clear when marker=False."""
        hdr = build_rtp_header(seq=0, timestamp=0, ssrc=0, marker=False)
        parsed = parse_rtp_header(hdr)
        assert parsed["marker"] is False

    def test_rtp_header_seq_wraps(self):
        """Sequence number at max u16 survives pack/unpack."""
        hdr = build_rtp_header(seq=0xFFFF, timestamp=0, ssrc=0)
        parsed = parse_rtp_header(hdr)
        assert parsed["seq"] == 0xFFFF

    def test_rtp_header_payload_type_default(self):
        """Default payload type is 96 (dynamic video PT)."""
        hdr = build_rtp_header(seq=0, timestamp=0, ssrc=0)
        parsed = parse_rtp_header(hdr)
        assert parsed["pt"] == 96

    def test_rtp_header_custom_payload_type(self):
        """Custom payload type is stored correctly."""
        hdr = build_rtp_header(seq=0, timestamp=0, ssrc=0, pt=100)
        parsed = parse_rtp_header(hdr)
        assert parsed["pt"] == 100

    def test_cc_header_roundtrip(self):
        """Build and parse ChromaCatch FEC header — all fields survive."""
        hdr = build_cc_header(
            frame_id=42,
            block_id=1,
            shard_index=5,
            data_shards=10,
            total_shards=13,
            flags=FLAG_KEYFRAME | FLAG_LAST_BLOCK,
            orig_len=200,
        )
        assert len(hdr) == CC_HEADER_SIZE
        parsed = parse_cc_header(hdr)
        assert parsed["frame_id"] == 42
        assert parsed["block_id"] == 1
        assert parsed["shard_index"] == 5
        assert parsed["data_shards"] == 10
        assert parsed["total_shards"] == 13
        assert parsed["is_keyframe"] is True
        assert parsed["is_last_block"] is True
        assert parsed["orig_len"] == 200

    def test_cc_header_flags_clear(self):
        """Zero flags means neither keyframe nor last-block."""
        hdr = build_cc_header(0, 0, 0, 10, 13, 0, 0)
        parsed = parse_cc_header(hdr)
        assert parsed["is_keyframe"] is False
        assert parsed["is_last_block"] is False
        assert parsed["flags"] == 0

    def test_cc_header_keyframe_only(self):
        """FLAG_KEYFRAME bit sets is_keyframe without setting is_last_block."""
        hdr = build_cc_header(0, 0, 0, 10, 13, FLAG_KEYFRAME, 0)
        parsed = parse_cc_header(hdr)
        assert parsed["is_keyframe"] is True
        assert parsed["is_last_block"] is False

    def test_cc_header_last_block_only(self):
        """FLAG_LAST_BLOCK bit sets is_last_block without setting is_keyframe."""
        hdr = build_cc_header(0, 0, 0, 10, 13, FLAG_LAST_BLOCK, 0)
        parsed = parse_cc_header(hdr)
        assert parsed["is_keyframe"] is False
        assert parsed["is_last_block"] is True

    def test_cc_header_frame_id_wraps(self):
        """frame_id at max u16 and all other u8 fields at 0xFF survive."""
        hdr = build_cc_header(
            frame_id=0xFFFF, block_id=0xFF, shard_index=0xFF,
            data_shards=10, total_shards=13, flags=0xFF, orig_len=0xFF,
        )
        parsed = parse_cc_header(hdr)
        assert parsed["frame_id"] == 0xFFFF
        assert parsed["block_id"] == 0xFF
        assert parsed["shard_index"] == 0xFF
        assert parsed["orig_len"] == 0xFF

    def test_header_size_constant(self):
        """HEADER_SIZE equals RTP_HEADER_SIZE + CC_HEADER_SIZE."""
        assert HEADER_SIZE == RTP_HEADER_SIZE + CC_HEADER_SIZE

    def test_rtp_header_size(self):
        """RTP header is exactly 12 bytes (RFC 3550)."""
        assert RTP_HEADER_SIZE == 12

    def test_cc_header_size(self):
        """CC header is exactly 8 bytes."""
        assert CC_HEADER_SIZE == 8

    def test_payload_fits_mtu(self):
        """Total packet size stays within the 1500-byte Ethernet MTU."""
        ip_udp_overhead = 28  # 20 IP + 8 UDP
        assert HEADER_SIZE + PAYLOAD_SIZE <= 1500 - ip_udp_overhead


# ---------------------------------------------------------------------------
# TestRTPFECTransport — sender unit tests
# ---------------------------------------------------------------------------

class TestRTPFECTransport:
    """Tests for the RTP+FEC sender transport."""

    def test_transport_name(self):
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        assert t.transport_name == "rtp-fec"

    def test_not_connected_initially(self):
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        assert t.is_connected is False

    def test_frames_sent_initially_zero(self):
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        assert t.frames_sent == 0

    def test_bytes_sent_initially_zero(self):
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        assert t.bytes_sent == 0

    def test_dest_host_explicit(self):
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="10.0.0.5", dest_port=9000)
        assert t._dest_host == "10.0.0.5"
        assert t._dest_port == 9000

    def test_packetize_small_au_produces_one_block(self):
        """A small AU (< PAYLOAD_SIZE) yields exactly FEC_TOTAL_SHARDS packets."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00\x00\x00\x01\x65" + b"\x00" * 100  # small H.264 IDR slice
        packets = t._packetize_au(au, is_keyframe=True)
        assert len(packets) == FEC_TOTAL_SHARDS

    def test_packetize_packet_size(self):
        """Every packet is exactly HEADER_SIZE + PAYLOAD_SIZE bytes."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=False)
        for pkt in packets:
            assert len(pkt) == HEADER_SIZE + PAYLOAD_SIZE

    def test_packetize_sets_keyframe_flag(self):
        """Packets from a keyframe AU carry FLAG_KEYFRAME in their CC header."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=True)
        cc = parse_cc_header(packets[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc["is_keyframe"] is True

    def test_packetize_non_keyframe_no_flag(self):
        """Packets from a non-keyframe AU do NOT carry FLAG_KEYFRAME."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=False)
        cc = parse_cc_header(packets[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc["is_keyframe"] is False

    def test_packetize_single_block_sets_last_block_flag(self):
        """The only block for a small AU must carry FLAG_LAST_BLOCK."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=False)
        cc = parse_cc_header(packets[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc["is_last_block"] is True

    def test_packetize_large_au_multiple_blocks(self):
        """An AU spanning 11 payload chunks produces 2 FEC blocks (26 packets)."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        # 11 chunks: block 0 holds 10, block 1 holds 1
        au = b"\x00" * (PAYLOAD_SIZE * 11)
        packets = t._packetize_au(au, is_keyframe=False)
        assert len(packets) == FEC_TOTAL_SHARDS * 2

    def test_packetize_large_au_last_block_flag(self):
        """Only the second block carries FLAG_LAST_BLOCK; the first block does not."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * (PAYLOAD_SIZE * 11)
        packets = t._packetize_au(au, is_keyframe=False)
        # First FEC_TOTAL_SHARDS packets belong to block 0
        cc_block0 = parse_cc_header(packets[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        cc_block1 = parse_cc_header(packets[FEC_TOTAL_SHARDS][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc_block0["is_last_block"] is False
        assert cc_block1["is_last_block"] is True

    def test_packetize_block_ids(self):
        """Block IDs are sequential starting at 0."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * (PAYLOAD_SIZE * 11)
        packets = t._packetize_au(au, is_keyframe=False)
        cc_first = parse_cc_header(packets[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        cc_second = parse_cc_header(packets[FEC_TOTAL_SHARDS][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc_first["block_id"] == 0
        assert cc_second["block_id"] == 1

    def test_packetize_shard_indices(self):
        """Shard indices within a block run from 0 to FEC_TOTAL_SHARDS-1."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=False)
        indices = [
            parse_cc_header(p[RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])["shard_index"]
            for p in packets
        ]
        assert indices == list(range(FEC_TOTAL_SHARDS))

    def test_packetize_increments_frame_id(self):
        """Consecutive calls to _packetize_au use successive frame IDs."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        p1 = t._packetize_au(au, is_keyframe=False)
        p2 = t._packetize_au(au, is_keyframe=False)
        cc1 = parse_cc_header(p1[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        cc2 = parse_cc_header(p2[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc2["frame_id"] == (cc1["frame_id"] + 1) & 0xFFFF

    def test_packetize_same_frame_id_across_blocks(self):
        """All packets within one AU share the same frame_id."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * (PAYLOAD_SIZE * 11)
        packets = t._packetize_au(au, is_keyframe=False)
        frame_ids = {
            parse_cc_header(p[RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])["frame_id"]
            for p in packets
        }
        assert len(frame_ids) == 1

    def test_packetize_rtp_seq_increments(self):
        """RTP sequence number increments across consecutive packets."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=False)
        seqs = [parse_rtp_header(p)["seq"] for p in packets]
        for i in range(1, len(seqs)):
            assert seqs[i] == (seqs[i - 1] + 1) & 0xFFFF

    def test_packetize_empty_au_returns_empty(self):
        """Empty AU bytes produce no packets."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        packets = t._packetize_au(b"", is_keyframe=False)
        assert packets == []

    def test_packetize_total_shards_field(self):
        """total_shards field in CC header equals FEC_TOTAL_SHARDS."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        packets = t._packetize_au(b"\x00" * 100, is_keyframe=False)
        cc = parse_cc_header(packets[0][RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
        assert cc["total_shards"] == FEC_TOTAL_SHARDS

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """stop() on an unstarted transport is a no-op."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        await t.stop()
        assert t.is_connected is False

    @pytest.mark.asyncio
    async def test_stop_twice_is_safe(self):
        """Calling stop() twice does not raise."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        await t.stop()
        await t.stop()

    @pytest.mark.asyncio
    async def test_start_sets_running_and_stop_clears(self):
        """start() creates a UDP endpoint; stop() tears it down."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(dest_host="127.0.0.1", dest_port=7000)
        await t.start()
        assert t._running is True
        assert t._transport is not None
        await t.stop()
        assert t._running is False
        assert t._transport is None

    @pytest.mark.asyncio
    async def test_send_loop_exits_when_not_running(self):
        """_send_loop returns immediately when _running is False."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        h264_capture = MagicMock()
        t = RTPFECTransport(h264_capture=h264_capture, dest_host="127.0.0.1", dest_port=7000)
        t._running = False
        # Should return promptly without calling get_au
        await t._send_loop()
        h264_capture.get_au.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_loop_exits_without_h264_capture(self):
        """_send_loop returns early and logs an error when h264_capture is None."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        t = RTPFECTransport(h264_capture=None, dest_host="127.0.0.1", dest_port=7000)
        t._running = True
        await t._send_loop()  # Should return without raising

    @pytest.mark.asyncio
    async def test_send_loop_skips_none_au(self):
        """_send_loop does not increment frames_sent when get_au returns None."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        call_count = 0

        def get_au_side_effect(_timeout):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                # Signal the loop to stop by clearing _running on the transport
                return None
            return None

        h264_capture = MagicMock()
        h264_capture.get_au = MagicMock(side_effect=get_au_side_effect)
        t = RTPFECTransport(h264_capture=h264_capture, dest_host="127.0.0.1", dest_port=7000)
        t._running = True
        mock_udp_transport = MagicMock()
        mock_udp_transport.is_closing = MagicMock(return_value=False)
        t._transport = mock_udp_transport

        # Run the loop briefly then stop it
        async def stop_soon():
            await asyncio.sleep(0.05)
            t._running = False

        await asyncio.gather(t._send_loop(), stop_soon())
        assert t.frames_sent == 0

    @pytest.mark.asyncio
    async def test_send_loop_sends_packets_and_increments_counters(self):
        """_send_loop calls sendto for each packet and increments frames_sent."""
        from airplay_client.transport.rtp_fec_transport import RTPFECTransport
        au = b"\x00" * 100
        delivered = []

        def get_au_side_effect(_timeout):
            if not delivered:
                delivered.append(True)
                return (au, False, 0.0)
            return None  # Return None after the first AU

        h264_capture = MagicMock()
        h264_capture.get_au = MagicMock(side_effect=get_au_side_effect)
        t = _make_transport_with_mock_encoder(h264_capture=h264_capture, dest_host="127.0.0.1", dest_port=7000)
        t._running = True
        mock_udp_transport = MagicMock()
        mock_udp_transport.is_closing = MagicMock(return_value=False)
        t._transport = mock_udp_transport

        async def stop_soon():
            await asyncio.sleep(0.1)
            t._running = False

        await asyncio.gather(t._send_loop(), stop_soon())
        assert t.frames_sent == 1
        assert t.bytes_sent > 0
        assert mock_udp_transport.sendto.call_count == FEC_TOTAL_SHARDS


# ---------------------------------------------------------------------------
# TestRTPFECTransportFactory — factory integration
# ---------------------------------------------------------------------------

class TestRTPFECTransportFactory:
    """Tests for factory integration with rtp-fec mode."""

    @patch("airplay_client.transport.factory.client_settings")
    def test_factory_creates_rtp_fec(self, mock_settings):
        """create_media_transport returns an RTPFECTransport for mode 'rtp-fec'."""
        mock_settings.transport_mode = "rtp-fec"
        mock_settings.rtp_fec_dest_host = "127.0.0.1"
        mock_settings.rtp_fec_dest_port = 7000
        mock_settings.audio_enabled = False
        from airplay_client.transport.factory import create_media_transport
        h264_capture = MagicMock()
        t = create_media_transport(frame_source=MagicMock(), audio_source=None, h264_capture=h264_capture)
        assert t.transport_name == "rtp-fec"

    @patch("airplay_client.transport.factory.client_settings")
    def test_factory_rtp_fec_requires_h264_capture(self, mock_settings):
        """create_media_transport raises ValueError when h264_capture is None for rtp-fec."""
        mock_settings.transport_mode = "rtp-fec"
        from airplay_client.transport.factory import create_media_transport
        with pytest.raises(ValueError, match="h264_capture"):
            create_media_transport(frame_source=MagicMock(), audio_source=None, h264_capture=None)

    @patch("airplay_client.transport.factory.client_settings")
    def test_factory_rtp_fec_error_message_mentions_mode(self, mock_settings):
        """ValueError for unknown mode mentions rtp-fec as a valid option."""
        mock_settings.transport_mode = "unknown-mode"
        from airplay_client.transport.factory import create_media_transport
        with pytest.raises(ValueError, match="rtp-fec"):
            create_media_transport(frame_source=MagicMock(), audio_source=None)


# ---------------------------------------------------------------------------
# TestFECBlock — receiver dataclass
# ---------------------------------------------------------------------------

class TestFECBlock:
    """Tests for the FECBlock dataclass used by the receiver."""

    def test_complete_when_enough_shards(self):
        """Block is complete when shard count reaches data_shards."""
        from backend.rtp_fec_receiver import FECBlock
        block = FECBlock(frame_id=1, block_id=0, data_shards=10, total_shards=13, flags=0, orig_len=0)
        for i in range(10):
            block.shards[i] = b"\x00" * PAYLOAD_SIZE
        assert block.complete is True

    def test_not_complete_with_fewer_shards(self):
        """Block is not complete with fewer than data_shards received."""
        from backend.rtp_fec_receiver import FECBlock
        block = FECBlock(frame_id=1, block_id=0, data_shards=10, total_shards=13, flags=0, orig_len=0)
        for i in range(9):
            block.shards[i] = b"\x00" * PAYLOAD_SIZE
        assert block.complete is False

    def test_complete_with_parity_substituting_data(self):
        """Block is complete when parity shards compensate for missing data shards."""
        from backend.rtp_fec_receiver import FECBlock
        block = FECBlock(frame_id=1, block_id=0, data_shards=3, total_shards=5, flags=0, orig_len=0)
        # Two data shards + one parity shard = 3 >= data_shards=3
        block.shards[0] = b"\x00"
        block.shards[1] = b"\x00"
        block.shards[3] = b"\x00"  # parity shard at index 3
        assert block.complete is True
        assert block.all_data_received is False

    def test_all_data_received_true(self):
        """all_data_received is True when every data shard index is present."""
        from backend.rtp_fec_receiver import FECBlock
        block = FECBlock(frame_id=1, block_id=0, data_shards=3, total_shards=5, flags=0, orig_len=0)
        block.shards[0] = b"\x00"
        block.shards[1] = b"\x00"
        block.shards[2] = b"\x00"
        assert block.all_data_received is True

    def test_all_data_received_false_when_gap(self):
        """all_data_received is False when a data shard index is missing."""
        from backend.rtp_fec_receiver import FECBlock
        block = FECBlock(frame_id=1, block_id=0, data_shards=3, total_shards=5, flags=0, orig_len=0)
        block.shards[0] = b"\x00"
        block.shards[2] = b"\x00"  # shard index 1 is absent
        assert block.all_data_received is False

    def test_default_shards_empty(self):
        """FECBlock initialises with an empty shards dict."""
        from backend.rtp_fec_receiver import FECBlock
        block = FECBlock(frame_id=5, block_id=2, data_shards=10, total_shards=13, flags=0, orig_len=0)
        assert block.shards == {}
        assert block.complete is False
        assert block.all_data_received is False


# ---------------------------------------------------------------------------
# TestFrameAssembly — receiver dataclass
# ---------------------------------------------------------------------------

class TestFrameAssembly:
    """Tests for the FrameAssembly dataclass."""

    def test_default_values(self):
        """FrameAssembly initialises with correct defaults."""
        from backend.rtp_fec_receiver import FrameAssembly
        fa = FrameAssembly(frame_id=42)
        assert fa.frame_id == 42
        assert fa.blocks == {}
        assert fa.is_keyframe is False
        assert fa.is_complete is False

    def test_blocks_dict_is_per_instance(self):
        """Each FrameAssembly gets its own blocks dict (no shared mutable default)."""
        from backend.rtp_fec_receiver import FrameAssembly
        fa1 = FrameAssembly(frame_id=1)
        fa2 = FrameAssembly(frame_id=2)
        fa1.blocks[0] = b"data"
        assert 0 not in fa2.blocks

    def test_is_keyframe_settable(self):
        from backend.rtp_fec_receiver import FrameAssembly
        fa = FrameAssembly(frame_id=1)
        fa.is_keyframe = True
        assert fa.is_keyframe is True

    def test_is_complete_settable(self):
        from backend.rtp_fec_receiver import FrameAssembly
        fa = FrameAssembly(frame_id=1)
        fa.is_complete = True
        assert fa.is_complete is True


# ---------------------------------------------------------------------------
# TestRTPFECProtocol — datagram receiver protocol
# ---------------------------------------------------------------------------

class TestRTPFECProtocol:
    """Tests for RTPFECProtocol — the asyncio DatagramProtocol receiver."""

    def _make_protocol(self):
        from backend.rtp_fec_receiver import RTPFECProtocol
        return RTPFECProtocol(MagicMock())

    def test_initial_counters(self):
        """All protocol counters start at zero."""
        p = self._make_protocol()
        assert p.packets_received == 0
        assert p.frames_decoded == 0
        assert p.fec_recoveries == 0

    def test_short_packet_ignored(self):
        """Packets shorter than HEADER_SIZE are silently dropped."""
        p = self._make_protocol()
        p.datagram_received(b"\x00" * (HEADER_SIZE - 1), ("127.0.0.1", 7000))
        assert p.packets_received == 0

    def test_packet_exactly_header_size_counted(self):
        """A packet of exactly HEADER_SIZE bytes is counted (empty payload)."""
        p = self._make_protocol()
        rtp = build_rtp_header(seq=1, timestamp=0, ssrc=0)
        cc = build_cc_header(frame_id=1, block_id=0, shard_index=0, data_shards=1, total_shards=1, flags=0, orig_len=0)
        pkt = rtp + cc  # exactly HEADER_SIZE, no payload
        p.datagram_received(pkt, ("127.0.0.1", 7000))
        assert p.packets_received == 1

    def test_pending_blocks_populated(self):
        """Receiving a valid packet creates a pending FEC block."""
        p = self._make_protocol()
        rtp = build_rtp_header(seq=1, timestamp=0, ssrc=0)
        cc = build_cc_header(frame_id=10, block_id=0, shard_index=0, data_shards=10, total_shards=13, flags=0, orig_len=0)
        payload = b"\xAB" * PAYLOAD_SIZE
        p.datagram_received(rtp + cc + payload, ("127.0.0.1", 7000))
        assert (10, 0) in p._pending_blocks

    def test_multiple_packets_same_block(self):
        """Multiple packets for the same block accumulate in the pending block."""
        p = self._make_protocol()
        for shard_idx in range(5):
            rtp = build_rtp_header(seq=shard_idx, timestamp=0, ssrc=0)
            cc = build_cc_header(frame_id=20, block_id=0, shard_index=shard_idx, data_shards=10, total_shards=13, flags=0, orig_len=0)
            p.datagram_received(rtp + cc + b"\x00" * PAYLOAD_SIZE, ("127.0.0.1", 7000))
        assert len(p._pending_blocks[(20, 0)].shards) == 5

    def test_cleanup_stale_removes_old_blocks(self):
        """_cleanup_stale evicts blocks whose received_at is older than the cutoff."""
        import time
        p = self._make_protocol()
        rtp = build_rtp_header(seq=1, timestamp=0, ssrc=0)
        cc = build_cc_header(frame_id=99, block_id=0, shard_index=0, data_shards=10, total_shards=13, flags=0, orig_len=0)
        p.datagram_received(rtp + cc + b"\x00" * PAYLOAD_SIZE, ("127.0.0.1", 7000))
        assert (99, 0) in p._pending_blocks
        # Back-date the block so it appears stale
        p._pending_blocks[(99, 0)].received_at = time.monotonic() - 1.0
        p._cleanup_stale()
        assert (99, 0) not in p._pending_blocks

    def test_cleanup_stale_keeps_fresh_blocks(self):
        """_cleanup_stale does not evict recently received blocks."""
        p = self._make_protocol()
        rtp = build_rtp_header(seq=1, timestamp=0, ssrc=0)
        cc = build_cc_header(frame_id=88, block_id=0, shard_index=0, data_shards=10, total_shards=13, flags=0, orig_len=0)
        p.datagram_received(rtp + cc + b"\x00" * PAYLOAD_SIZE, ("127.0.0.1", 7000))
        p._cleanup_stale()
        assert (88, 0) in p._pending_blocks

    def test_different_blocks_different_keys(self):
        """Packets for different (frame_id, block_id) pairs are stored separately."""
        p = self._make_protocol()
        for frame_id, block_id in [(1, 0), (1, 1), (2, 0)]:
            rtp = build_rtp_header(seq=0, timestamp=0, ssrc=0)
            cc = build_cc_header(frame_id=frame_id, block_id=block_id, shard_index=0, data_shards=10, total_shards=13, flags=0, orig_len=0)
            p.datagram_received(rtp + cc + b"\x00" * PAYLOAD_SIZE, ("127.0.0.1", 7000))
        assert (1, 0) in p._pending_blocks
        assert (1, 1) in p._pending_blocks
        assert (2, 0) in p._pending_blocks


# ---------------------------------------------------------------------------
# TestRTPFECReceiver — lifecycle manager
# ---------------------------------------------------------------------------

class TestRTPFECReceiver:
    """Tests for the RTPFECReceiver lifecycle manager."""

    def test_receiver_instantiation_defaults(self):
        """RTPFECReceiver stores host, port, and client_id correctly."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        r = RTPFECReceiver(MagicMock(), bind_host="127.0.0.1", bind_port=7000, client_id="test-client")
        assert r._bind_host == "127.0.0.1"
        assert r._bind_port == 7000
        assert r._client_id == "test-client"

    def test_receiver_default_bind_host(self):
        """Default bind_host is '0.0.0.0'."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        r = RTPFECReceiver(MagicMock())
        assert r._bind_host == "0.0.0.0"

    def test_receiver_default_bind_port(self):
        """Default bind_port is 7000."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        r = RTPFECReceiver(MagicMock())
        assert r._bind_port == 7000

    def test_protocol_is_none_before_start(self):
        """protocol property is None before start() is called."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        r = RTPFECReceiver(MagicMock())
        assert r.protocol is None

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """stop() on an unstarted receiver is a safe no-op."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        r = RTPFECReceiver(MagicMock())
        await r.stop()

    @pytest.mark.asyncio
    async def test_stop_twice_is_safe(self):
        """Calling stop() twice does not raise."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        r = RTPFECReceiver(MagicMock())
        await r.stop()
        await r.stop()

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        """start() binds a UDP endpoint; stop() closes it cleanly."""
        from backend.rtp_fec_receiver import RTPFECReceiver
        # Use port 0 to let the OS choose a free port
        r = RTPFECReceiver(MagicMock(), bind_host="127.0.0.1", bind_port=0)
        await r.start()
        assert r.protocol is not None
        assert r._transport is not None
        await r.stop()


# ---------------------------------------------------------------------------
# TestRTPFECEndToEnd — sender packets parsed correctly by receiver primitives
# ---------------------------------------------------------------------------

class TestRTPFECEndToEnd:
    """Integration-style tests: packetize on the sender, parse on the receiver."""

    def test_packet_headers_are_parseable_by_receiver(self):
        """Packets produced by _packetize_au can be parsed by the receiver's header parsers."""
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00\x00\x00\x01\x41" + b"\xAB" * 500
        packets = t._packetize_au(au, is_keyframe=False)
        for pkt in packets:
            rtp = parse_rtp_header(pkt[:RTP_HEADER_SIZE])
            cc = parse_cc_header(pkt[RTP_HEADER_SIZE : RTP_HEADER_SIZE + CC_HEADER_SIZE])
            assert rtp["version"] == 2
            assert cc["total_shards"] == FEC_TOTAL_SHARDS
            assert cc["data_shards"] <= FEC_DATA_SHARDS

    def test_receiver_protocol_accepts_sender_packets(self):
        """RTPFECProtocol.datagram_received handles packets from _packetize_au without error."""
        from backend.rtp_fec_receiver import RTPFECProtocol
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * 100
        packets = t._packetize_au(au, is_keyframe=False)
        p = RTPFECProtocol(MagicMock())
        for pkt in packets:
            p.datagram_received(pkt, ("127.0.0.1", 7000))
        assert p.packets_received == FEC_TOTAL_SHARDS

    def test_receiver_counts_correct_for_multi_block_frame(self):
        """Receiver counts all packets from a multi-block frame correctly."""
        from backend.rtp_fec_receiver import RTPFECProtocol
        t = _make_transport_with_mock_encoder(dest_host="127.0.0.1", dest_port=7000)
        au = b"\x00" * (PAYLOAD_SIZE * 11)  # 2 FEC blocks = 26 packets
        packets = t._packetize_au(au, is_keyframe=True)
        p = RTPFECProtocol(MagicMock())
        for pkt in packets:
            p.datagram_received(pkt, ("127.0.0.1", 7000))
        assert p.packets_received == FEC_TOTAL_SHARDS * 2
