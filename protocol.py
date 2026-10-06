"""Binary framing used by the PC-to-MSPM0 measurement link."""
from __future__ import annotations

from dataclasses import dataclass
import struct

MAGIC = b"\xA5\x5A"
TYPE_PC_CAPTURE = 0x10
TYPE_PC_SET_PHASE = 0x11
TYPE_PC_SET_MODE = 0x12
TYPE_PC_RESULT = 0x90
TYPE_ERROR = 0xE0
MAX_PAYLOAD = 20_000


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    crc = initial
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (
                crc << 1
            ) & 0xFFFF
    return crc


def encode_frame(frame_type: int, sequence: int, payload: bytes = b"") -> bytes:
    if len(payload) > 0xFFFF:
        raise ValueError("payload is too large")
    body = struct.pack("<BBH", frame_type, sequence, len(payload)) + payload
    return MAGIC + body + struct.pack("<H", crc16_ccitt(body))


@dataclass(frozen=True)
class Frame:
    frame_type: int
    sequence: int
    payload: bytes


class FrameDecoder:
    """Incremental decoder with sync recovery after noise or a bad CRC."""

    def __init__(self, max_payload: int = MAX_PAYLOAD) -> None:
        self.buffer = bytearray()
        self.max_payload = max_payload
        self.crc_errors = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames: list[Frame] = []
        while True:
            sync = self.buffer.find(MAGIC)
            if sync < 0:
                if self.buffer[-1:] != MAGIC[:1]:
                    self.buffer.clear()
                else:
                    del self.buffer[:-1]
                break
            if sync:
                del self.buffer[:sync]
            if len(self.buffer) < 6:
                break
            frame_type, sequence, payload_length = struct.unpack_from(
                "<BBH", self.buffer, 2
            )
            if payload_length > self.max_payload:
                del self.buffer[0]
                continue
            total_length = 8 + payload_length
            if len(self.buffer) < total_length:
                break
            body = bytes(self.buffer[2 : 6 + payload_length])
            received_crc = struct.unpack_from(
                "<H", self.buffer, 6 + payload_length
            )[0]
            if crc16_ccitt(body) != received_crc:
                self.crc_errors += 1
                del self.buffer[0]
                continue
            payload = bytes(self.buffer[6 : 6 + payload_length])
            frames.append(Frame(frame_type, sequence, payload))
            del self.buffer[:total_length]
        return frames


@dataclass(frozen=True)
class Peak:
    frequency_hz: float
    magnitude: int


@dataclass(frozen=True)
class CaptureResult:
    sample_rate: int
    flags: int
    peaks: tuple[Peak, Peak, Peak]
    samples: tuple[int, ...]
    spectrum: tuple[int, ...]


def decode_result(payload: bytes) -> CaptureResult:
    if len(payload) < 28:
        raise ValueError("RESULT payload is shorter than its metadata")
    sample_rate, sample_count, spectrum_count, flags = struct.unpack_from(
        "<IHHH", payload
    )
    offset = 10
    peaks: list[Peak] = []
    for _ in range(3):
        frequency_millihz, magnitude = struct.unpack_from("<IH", payload, offset)
        peaks.append(Peak(frequency_millihz / 1000.0, magnitude))
        offset += 6
    expected = offset + 2 * sample_count + 2 * spectrum_count
    if len(payload) != expected:
        raise ValueError(
            f"RESULT payload length mismatch: expected {expected}, got {len(payload)}"
        )
    sample_fmt = f"<{sample_count}H"
    samples = struct.unpack_from(sample_fmt, payload, offset)
    offset += 2 * sample_count
    spectrum_fmt = f"<{spectrum_count}H"
    spectrum = struct.unpack_from(spectrum_fmt, payload, offset)
    return CaptureResult(
        sample_rate, flags, tuple(peaks), samples, spectrum  # type: ignore[arg-type]
    )


def decode_error(payload: bytes) -> int:
    if len(payload) != 2:
        raise ValueError("ERROR payload must contain one u16 error code")
    return struct.unpack("<H", payload)[0]
