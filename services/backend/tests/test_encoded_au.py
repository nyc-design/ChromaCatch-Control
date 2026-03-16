"""Tests for canonical encoded media types."""

import time

import pytest

from shared.encoded_au import EncodedAccessUnit, EncodedAudioFrame


class TestEncodedAccessUnit:
    def test_basic_construction(self):
        au = EncodedAccessUnit(data=b"\x00\x00\x00\x01\x67test")
        assert au.data == b"\x00\x00\x00\x01\x67test"
        assert au.is_keyframe is False
        assert au.codec == "h264"
        assert au.sequence == 0
        assert au.width == 0
        assert au.height == 0

    def test_keyframe(self):
        au = EncodedAccessUnit(data=b"idr_data", is_keyframe=True)
        assert au.is_keyframe is True

    def test_h265_codec(self):
        au = EncodedAccessUnit(data=b"hevc_data", codec="h265")
        assert au.codec == "h265"

    def test_byte_length(self):
        data = b"x" * 1024
        au = EncodedAccessUnit(data=data)
        assert au.byte_length == 1024

    def test_capture_timestamp_auto(self):
        before = time.time()
        au = EncodedAccessUnit(data=b"x")
        after = time.time()
        assert before <= au.capture_timestamp <= after

    def test_capture_timestamp_explicit(self):
        ts = 1700000000.0
        au = EncodedAccessUnit(data=b"x", capture_timestamp=ts)
        assert au.capture_timestamp == ts

    def test_immutable(self):
        au = EncodedAccessUnit(data=b"x")
        with pytest.raises(AttributeError):
            au.data = b"y"  # type: ignore

    def test_repr(self):
        au = EncodedAccessUnit(data=b"x" * 100, codec="h265", is_keyframe=True, sequence=42, width=1920, height=1080)
        r = repr(au)
        assert "h265" in r
        assert "keyframe=True" in r
        assert "seq=42" in r
        assert "100B" in r
        assert "1920x1080" in r

    def test_empty_data(self):
        au = EncodedAccessUnit(data=b"")
        assert au.byte_length == 0

    def test_dimensions(self):
        au = EncodedAccessUnit(data=b"x", width=1280, height=720)
        assert au.width == 1280
        assert au.height == 720


class TestEncodedAudioFrame:
    def test_basic_construction(self):
        af = EncodedAudioFrame(data=b"opus_data")
        assert af.data == b"opus_data"
        assert af.sample_rate == 48000
        assert af.channels == 2
        assert af.duration_ms == 20
        assert af.sequence == 0

    def test_byte_length(self):
        af = EncodedAudioFrame(data=b"x" * 256)
        assert af.byte_length == 256

    def test_custom_params(self):
        af = EncodedAudioFrame(
            data=b"mono",
            sample_rate=16000,
            channels=1,
            duration_ms=10,
            sequence=5,
        )
        assert af.sample_rate == 16000
        assert af.channels == 1
        assert af.duration_ms == 10
        assert af.sequence == 5

    def test_immutable(self):
        af = EncodedAudioFrame(data=b"x")
        with pytest.raises(AttributeError):
            af.data = b"y"  # type: ignore

    def test_repr(self):
        af = EncodedAudioFrame(data=b"x" * 50, sequence=10, sample_rate=48000, channels=2, duration_ms=20)
        r = repr(af)
        assert "seq=10" in r
        assert "50B" in r
        assert "48000" in r

    def test_capture_timestamp_auto(self):
        before = time.time()
        af = EncodedAudioFrame(data=b"x")
        after = time.time()
        assert before <= af.capture_timestamp <= after
