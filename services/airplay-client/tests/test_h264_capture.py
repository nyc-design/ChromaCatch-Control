"""Tests for H.264 capture and NAL type detection."""

from airplay_client.capture.h264_capture import _has_nal_type

# H.264 NAL start code
SC = b"\x00\x00\x00\x01"


def _make_au(nal_types: list[int]) -> bytes:
    """Build a fake H.264 Access Unit with given NAL types.

    Each NAL is just: start_code + header_byte + 2 padding bytes.
    """
    data = b""
    for nt in nal_types:
        # nal_ref_idc=3 for IDR/SPS/PPS, 0 for AUD
        idc = 3 if nt in (5, 7, 8) else 0
        header = (idc << 5) | nt
        data += SC + bytes([header, 0x00, 0x00])
    return data


class TestHasNalType:
    def test_finds_idr(self):
        au = _make_au([9, 7, 8, 5])
        assert _has_nal_type(au, 5) is True

    def test_no_idr_in_p_frame(self):
        au = _make_au([9, 1])
        assert _has_nal_type(au, 5) is False

    def test_empty_data(self):
        assert _has_nal_type(b"", 5) is False

    def test_no_start_code(self):
        assert _has_nal_type(b"\x01\x02\x03", 5) is False

    def test_finds_sps(self):
        au = _make_au([7, 8, 5])
        assert _has_nal_type(au, 7) is True
