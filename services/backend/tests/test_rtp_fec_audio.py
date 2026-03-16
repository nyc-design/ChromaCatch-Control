"""Tests for RTP+FEC audio packet support."""

from shared.rtp_fec_protocol import (
    RTP_HEADER_SIZE,
    RTP_PAYLOAD_TYPE_AUDIO,
    RTP_PAYLOAD_TYPE_VIDEO,
    build_audio_rtp_packet,
    build_rtp_header,
    is_audio_packet,
    parse_audio_rtp_packet,
    parse_rtp_header,
)


class TestAudioRTPPackets:
    def test_build_audio_packet(self):
        opus_data = b"\x01\x02\x03\x04" * 20
        pkt = build_audio_rtp_packet(opus_data, seq=42, timestamp=96000, ssrc=12345)
        assert len(pkt) == RTP_HEADER_SIZE + len(opus_data)
        rtp = parse_rtp_header(pkt[:RTP_HEADER_SIZE])
        assert rtp["pt"] == RTP_PAYLOAD_TYPE_AUDIO
        assert rtp["seq"] == 42
        assert rtp["timestamp"] == 96000
        assert rtp["ssrc"] == 12345

    def test_parse_audio_packet(self):
        opus_data = b"opus_encoded_frame_data"
        pkt = build_audio_rtp_packet(opus_data, seq=1, timestamp=0, ssrc=0)
        rtp, payload = parse_audio_rtp_packet(pkt)
        assert rtp["pt"] == RTP_PAYLOAD_TYPE_AUDIO
        assert payload == opus_data

    def test_is_audio_packet_true(self):
        rtp = {"pt": RTP_PAYLOAD_TYPE_AUDIO}
        assert is_audio_packet(rtp) is True

    def test_is_audio_packet_false_video(self):
        rtp = {"pt": RTP_PAYLOAD_TYPE_VIDEO}
        assert is_audio_packet(rtp) is False

    def test_is_audio_packet_false_missing(self):
        assert is_audio_packet({}) is False

    def test_video_pt_unchanged(self):
        """Verify backward compat: video packets still use PT=96."""
        hdr = build_rtp_header(seq=0, timestamp=0, ssrc=0)
        rtp = parse_rtp_header(hdr)
        assert rtp["pt"] == RTP_PAYLOAD_TYPE_VIDEO

    def test_audio_video_distinguishable(self):
        """Audio and video packets on same socket must be distinguishable."""
        video_hdr = build_rtp_header(seq=0, timestamp=0, ssrc=0, pt=RTP_PAYLOAD_TYPE_VIDEO)
        audio_pkt = build_audio_rtp_packet(b"opus", seq=0, timestamp=0, ssrc=0)
        v_rtp = parse_rtp_header(video_hdr)
        a_rtp = parse_rtp_header(audio_pkt[:RTP_HEADER_SIZE])
        assert not is_audio_packet(v_rtp)
        assert is_audio_packet(a_rtp)

    def test_empty_opus_payload(self):
        pkt = build_audio_rtp_packet(b"", seq=0, timestamp=0, ssrc=0)
        assert len(pkt) == RTP_HEADER_SIZE
        _, payload = parse_audio_rtp_packet(pkt)
        assert payload == b""

    def test_large_opus_payload(self):
        data = b"\xff" * 4000
        pkt = build_audio_rtp_packet(data, seq=65535, timestamp=2**32 - 1, ssrc=2**32 - 1)
        rtp, payload = parse_audio_rtp_packet(pkt)
        assert payload == data
        assert rtp["seq"] == 65535
