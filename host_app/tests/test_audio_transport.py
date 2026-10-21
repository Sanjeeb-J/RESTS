"""Audio packet and low-bitrate codec tests."""

from __future__ import annotations

import math

import pytest

from rests_host.audio_transport import (
    AudioTransportError,
    FRAME_SAMPLES,
    decode_adpcm_block,
    decode_frame,
    encode_adpcm_block,
    encode_frame,
    roundtrip_audio,
)


def test_audio_frame_checks_sequence_payload_and_crc():
    payload = b"rests audio block"
    frame = encode_frame(42, payload)
    assert decode_frame(frame) == (42, payload)
    damaged = bytearray(frame)
    damaged[-3] ^= 0x40
    with pytest.raises(AudioTransportError, match="checksum"):
        decode_frame(bytes(damaged))


def test_adpcm_block_roundtrip_is_bounded_and_decodable():
    samples = [0.5 * math.sin(2 * math.pi * 440 * i / 16_000) for i in range(FRAME_SAMPLES)]
    encoded = encode_adpcm_block(samples)
    decoded = decode_adpcm_block(encoded)
    assert len(encoded) <= 512
    assert len(decoded) == len(samples)
    mse = sum((a - b) ** 2 for a, b in zip(samples, decoded)) / len(samples)
    assert mse < 0.002


def test_roundtrip_sends_sequential_frames_and_reconstructs_audio():
    samples = [0.25 * math.sin(2 * math.pi * 220 * i / 16_000) for i in range(2500)]
    frames = []

    def echo(frame, sequence):
        actual_sequence, _payload = decode_frame(frame)
        assert actual_sequence == sequence
        frames.append(frame)
        return frame

    returned = roundtrip_audio(samples, echo)
    assert len(frames) == 3
    assert len(returned) == len(samples)
    assert returned != samples  # ADPCM is intentionally lossy compression.


def test_roundtrip_rejects_wrong_echo():
    with pytest.raises(AudioTransportError, match="mismatched"):
        roundtrip_audio([0.1] * 64, lambda _frame, sequence: encode_frame(sequence + 1, b"x"))
