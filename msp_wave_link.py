"""Reusable Radxa/PC UART client for the MSPM0 waveform controller."""
from __future__ import annotations

import struct
import threading
import time
from typing import Any

import serial

from protocol import (
    FrameDecoder,
    TYPE_ERROR,
    TYPE_PC_SET_TARGET,
    TYPE_PC_SET_WAVEFORM,
    TYPE_PC_TARGET_ACK,
    TargetAck,
    decode_error,
    decode_target_ack,
    encode_frame,
)

BAUD = 1_000_000
AMPLITUDE_CODES = {8: 0, 2: 1, 4: 2, 6: 3}
WAVEFORM_CODES = {"sine": 0, "sawtooth": 1}
ERROR_TEXT = {
    1: "FPGA确认超时",
    2: "FPGA确认帧格式或序号错误",
    3: "FPGA确认帧CRC错误",
    4: "目标频率超出范围",
}


class MSPM0LinkError(RuntimeError):
    """Base class for host-to-MSPM0 communication failures."""


class MSPM0TimeoutError(MSPM0LinkError):
    """Raised when a complete end-to-end acknowledgement is not received."""


class MSPM0CommandError(MSPM0LinkError):
    """Raised when the MSPM0 reports that a command failed."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"MSPM0错误{code}: {ERROR_TEXT.get(code, '未知错误')}")


def encode_target_payload(
    frequency_hz: float, phase_degrees: float, amplitude: int
) -> bytes:
    """Validate engineering units and build the SET_TARGET payload."""
    if not 900.0 <= frequency_hz <= 110_000.0:
        raise ValueError("frequency_hz必须位于900～110000 Hz")
    if not 0.0 <= phase_degrees < 360.0:
        raise ValueError("phase_degrees必须位于0～359.99度")
    if amplitude not in AMPLITUDE_CODES:
        raise ValueError("amplitude必须是2、4、6或8")
    frequency_millihz = int(round(frequency_hz * 1000.0))
    phase_centidegrees = int(round(phase_degrees * 100.0))
    if phase_centidegrees >= 36_000:
        raise ValueError("相位舍入后必须小于360度")
    return struct.pack(
        "<IHB", frequency_millihz, phase_centidegrees,
        AMPLITUDE_CODES[amplitude]
    )


def encode_waveform_payload(
    waveform: str, frequency_hz: float, phase_degrees: float,
    amplitude: int
) -> bytes:
    """Build SET_WAVEFORM: waveform:u8 followed by legacy target fields."""
    try:
        waveform_code = WAVEFORM_CODES[waveform]
    except KeyError as error:
        raise ValueError("waveform必须是sine或sawtooth") from error
    return bytes((waveform_code,)) + encode_target_payload(
        frequency_hz, phase_degrees, amplitude
    )


class MSPM0WaveLink:
    """Own one serial port and issue synchronized waveform commands.

    One instance should be shared by the image-processing/control process.
    PySerial configures Linux termios automatically; no separate ``stty``
    command is required.
    """

    def __init__(
        self, port: str = "/dev/ttyS4", *, timeout: float = 1.0,
        serial_port: Any | None = None
    ) -> None:
        self.port = port
        self.timeout = timeout
        self._serial = serial_port
        self._owns_serial = serial_port is None
        self._sequence = 0
        self._lock = threading.Lock()

    def open(self) -> "MSPM0WaveLink":
        if self._serial is None:
            self._serial = serial.Serial(
                self.port, BAUD, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=0.05, write_timeout=self.timeout,
            )
        self._serial.reset_input_buffer()
        return self

    def close(self) -> None:
        if self._serial is not None and self._owns_serial:
            self._serial.close()
            self._serial = None

    def __enter__(self) -> "MSPM0WaveLink":
        return self.open()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def set_target(
        self, frequency_hz: float, phase_degrees: float = 0.0,
        amplitude: int = 8
    ) -> TargetAck:
        """Apply one waveform and return only after FPGA acknowledgement."""
        payload = encode_target_payload(
            frequency_hz, phase_degrees, amplitude
        )
        with self._lock:
            if self._serial is None:
                self.open()
            self._sequence = (self._sequence % 255) + 1
            sequence = self._sequence
            decoder = FrameDecoder()
            self._serial.write(
                encode_frame(TYPE_PC_SET_TARGET, sequence, payload)
            )
            self._serial.flush()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                block = self._serial.read(self._serial.in_waiting or 1)
                for frame in decoder.feed(block):
                    if frame.sequence != sequence:
                        continue
                    if frame.frame_type == TYPE_ERROR:
                        raise MSPM0CommandError(decode_error(frame.payload))
                    if frame.frame_type == TYPE_PC_TARGET_ACK:
                        ack = decode_target_ack(frame.payload)
                        if not (ack.mode & 0x10):
                            raise MSPM0LinkError(
                                "MSPM0确认帧未标记为自由运行模式"
                            )
                        return ack
            raise MSPM0TimeoutError(
                f"等待MSPM0/FPGA端到端确认超时（{self.port}）"
            )

    def set_waveform(
        self, waveform: str, frequency_hz: float,
        phase_degrees: float = 0.0, amplitude: int = 8
    ) -> TargetAck:
        """Set a sine or sawtooth and wait for the FPGA acknowledgement."""
        payload = encode_waveform_payload(
            waveform, frequency_hz, phase_degrees, amplitude
        )
        expected_saw = waveform == "sawtooth"
        with self._lock:
            if self._serial is None:
                self.open()
            self._sequence = (self._sequence % 255) + 1
            sequence = self._sequence
            decoder = FrameDecoder()
            self._serial.write(
                encode_frame(TYPE_PC_SET_WAVEFORM, sequence, payload)
            )
            self._serial.flush()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                block = self._serial.read(self._serial.in_waiting or 1)
                for frame in decoder.feed(block):
                    if frame.sequence != sequence:
                        continue
                    if frame.frame_type == TYPE_ERROR:
                        raise MSPM0CommandError(decode_error(frame.payload))
                    if frame.frame_type == TYPE_PC_TARGET_ACK:
                        ack = decode_target_ack(frame.payload)
                        if not (ack.mode & 0x10):
                            raise MSPM0LinkError(
                                "MSPM0确认帧未标记为自由运行模式"
                            )
                        if bool(ack.mode & 0x20) != expected_saw:
                            raise MSPM0LinkError(
                                "MSPM0确认的波形类型与请求不一致"
                            )
                        return ack
            raise MSPM0TimeoutError(
                f"等待MSPM0/FPGA端到端确认超时（{self.port}）"
            )

    def set_sawtooth(
        self, frequency_hz: float, phase_degrees: float = 0.0,
        amplitude: int = 8
    ) -> TargetAck:
        return self.set_waveform(
            "sawtooth", frequency_hz, phase_degrees, amplitude
        )
