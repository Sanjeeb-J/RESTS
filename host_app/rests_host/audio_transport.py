"""Compact audio packets for the RESTS STM32/ Renode UART relay."""

from __future__ import annotations

import struct
from typing import Callable, Sequence

MAGIC = b"\xA5\x5A"
FRAME_TYPE_AUDIO = 1
MAX_PAYLOAD = 512
FRAME_SAMPLES = 1008  # 63 ms at 16 kHz; IMA ADPCM block is 509 bytes.
FRAME_HEADER = struct.Struct("<2sBHH")
BLOCK_HEADER = struct.Struct("<HhB")
CRC_STRUCT = struct.Struct("<H")

_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
    13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)
_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)


class AudioTransportError(RuntimeError):
    """An audio block could not safely cross the UART relay."""


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_frame(sequence: int, payload: bytes) -> bytes:
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("sequence must fit in 16 bits")
    if not payload or len(payload) > MAX_PAYLOAD:
        raise ValueError(f"audio payload must contain 1..{MAX_PAYLOAD} bytes")
    header = FRAME_HEADER.pack(MAGIC, FRAME_TYPE_AUDIO, sequence, len(payload))
    body = header[2:] + payload
    return header + payload + CRC_STRUCT.pack(crc16_ccitt(body))


def decode_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < FRAME_HEADER.size + CRC_STRUCT.size:
        raise AudioTransportError("audio frame is truncated")
    magic, kind, sequence, length = FRAME_HEADER.unpack_from(frame)
    if magic != MAGIC or kind != FRAME_TYPE_AUDIO:
        raise AudioTransportError("invalid audio frame header")
    if not 0 < length <= MAX_PAYLOAD or len(frame) != FRAME_HEADER.size + length + 2:
        raise AudioTransportError("invalid audio frame length")
    payload = frame[FRAME_HEADER.size:-2]
    actual_crc = CRC_STRUCT.unpack_from(frame, len(frame) - 2)[0]
    if actual_crc != crc16_ccitt(frame[2:-2]):
        raise AudioTransportError("audio frame checksum mismatch")
    return sequence, payload


def encode_adpcm_block(samples: Sequence[float]) -> bytes:
    """Encode one independent mono IMA ADPCM block with its sample count."""
    if not samples or len(samples) > FRAME_SAMPLES:
        raise ValueError(f"block must contain 1..{FRAME_SAMPLES} samples")
    pcm = [max(-32768, min(32767, int(float(s) * 32767.0))) for s in samples]
    predictor = pcm[0]
    initial_index = 0
    index = initial_index
    nibbles: list[int] = []
    for sample in pcm[1:]:
        step = _STEP_TABLE[index]
        diff = sample - predictor
        code = 8 if diff < 0 else 0
        if diff < 0:
            diff = -diff
        delta = step >> 3
        if diff >= step:
            code |= 4
            diff -= step
            delta += step
        if diff >= (step >> 1):
            code |= 2
            diff -= step >> 1
            delta += step >> 1
        if diff >= (step >> 2):
            code |= 1
            delta += step >> 2
        predictor = max(-32768, min(32767, predictor - delta if code & 8 else predictor + delta))
        index = max(0, min(88, index + _INDEX_TABLE[code & 7]))
        nibbles.append(code)

    packed = bytearray((len(nibbles) + 1) // 2)
    for i, code in enumerate(nibbles):
        packed[i // 2] |= code << (4 if i & 1 else 0)
    result = BLOCK_HEADER.pack(len(pcm), pcm[0], initial_index) + packed
    if len(result) > MAX_PAYLOAD:
        raise ValueError("encoded audio block exceeds packet capacity")
    return result


def decode_adpcm_block(payload: bytes) -> list[float]:
    if len(payload) < BLOCK_HEADER.size:
        raise AudioTransportError("ADPCM block header is truncated")
    count, predictor, index = BLOCK_HEADER.unpack_from(payload)
    if not 1 <= count <= FRAME_SAMPLES or index > 88:
        raise AudioTransportError("invalid ADPCM sample count or step index")
    expected = BLOCK_HEADER.size + (count - 1 + 1) // 2
    if len(payload) != expected:
        raise AudioTransportError("ADPCM block length does not match sample count")
    decoded = [predictor]
    for i in range(count - 1):
        packed = payload[BLOCK_HEADER.size + i // 2]
        code = (packed >> 4) & 0x0F if i & 1 else packed & 0x0F
        step = _STEP_TABLE[index]
        delta = step >> 3
        if code & 4:
            delta += step
        if code & 2:
            delta += step >> 1
        if code & 1:
            delta += step >> 2
        predictor = max(-32768, min(32767, predictor - delta if code & 8 else predictor + delta))
        index = max(0, min(88, index + _INDEX_TABLE[code & 7]))
        decoded.append(predictor)
    return [value / 32768.0 for value in decoded]


def roundtrip_audio(
    samples: Sequence[float],
    exchange: Callable[[bytes, int], bytes],
) -> list[float]:
    """Compress, relay, validate, and decode audio in ordered UART frames."""
    returned: list[float] = []
    for sequence, offset in enumerate(range(0, len(samples), FRAME_SAMPLES)):
        block = encode_adpcm_block(samples[offset : offset + FRAME_SAMPLES])
        sent = encode_frame(sequence & 0xFFFF, block)
        echoed = exchange(sent, sequence & 0xFFFF)
        echoed_sequence, echoed_block = decode_frame(echoed)
        if echoed_sequence != (sequence & 0xFFFF) or echoed_block != block:
            raise AudioTransportError("STM32 returned a mismatched audio packet")
        returned.extend(decode_adpcm_block(echoed_block))
    return returned
