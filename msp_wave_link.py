"""Reusable Radxa/PC UART client for the MSPM0 waveform controller."""
from __future__ import annotations

import struct
import threading
import time
from typing import Any

import serial

from protocol import (
    FrameDecoder,
    DoubleAck,
    TYPE_ERROR,
    TYPE_PC_ARM_DOUBLE,
    TYPE_PC_DOUBLE_ACK,
    TYPE_PC_SET_TARGET,
    TYPE_PC_SET_WAVEFORM,
    TYPE_PC_TARGET_ACK,
    TargetAck,
    decode_error,
    decode_double_ack,
    decode_target_ack,
    encode_frame,
)

BAUD = 1_000_000
AMPLITUDE_CODES = {8: 0, 2: 1, 4: 2, 6: 3}
WAVEFORM_CODES = {"sine": 0, "sawtooth": 1, "decay_pulse": 3}
ERROR_TEXT = {
    1: "FPGA确认超时",
    2: "FPGA确认帧格式或序号错误",
    3: "FPGA确认帧CRC错误",
    4: "目标频率超出250～210000 Hz范围",
    5: "倍频事务忙",
    6: "当前输出状态不允许确定相位倍频",
    7: "等待下降过零或DAC生效超时",
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
    frequency_hz: float, phase_degrees: float, amplitude: int,
    *, minimum_hz: float = 250.0
) -> bytes:
    """Validate engineering units and build the SET_TARGET payload."""
    if not minimum_hz <= frequency_hz <= 210_000.0:
        raise ValueError(f"frequency_hz必须位于{minimum_hz:g}～210000 Hz")
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
    amplitude: int, *, direction: str = "rising",
    decay_microseconds: int = 1000
) -> bytes:
    """Build SET_WAVEFORM: waveform:u8 followed by legacy target fields."""
    try:
        waveform_code = WAVEFORM_CODES[waveform]
    except KeyError as error:
        raise ValueError("waveform必须是sine、sawtooth或decay_pulse") from error
    if direction not in ("rising", "falling"):
        raise ValueError("direction必须是rising或falling")
    if direction == "falling":
        if waveform != "sawtooth":
            raise ValueError("falling方向仅适用于sawtooth")
        waveform_code = 2
    if waveform == "decay_pulse" and frequency_hz != 100.0:
        raise ValueError("decay_pulse波形频率固定为100 Hz")
    if waveform == "decay_pulse" and not 1 <= decay_microseconds <= 9999:
        raise ValueError("decay_microseconds必须位于1～9999 us")
    payload = bytes((waveform_code,)) + encode_target_payload(
        frequency_hz, phase_degrees, amplitude,
        minimum_hz=100.0 if waveform == "decay_pulse" else 250.0
    )
    if waveform == "decay_pulse":
        payload += struct.pack("<H", decay_microseconds)
    return payload


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
        phase_degrees: float = 0.0, amplitude: int = 8,
        *, direction: str = "rising", decay_microseconds: int = 1000
    ) -> TargetAck:
        """Set a sine, sawtooth or 100 Hz decay pulse and await FPGA ACK."""
        payload = encode_waveform_payload(
            waveform, frequency_hz, phase_degrees, amplitude,
            direction=direction, decay_microseconds=decay_microseconds
        )
        expected_waveform_bits = {
            "sine": 0x00,
            "sawtooth": 0x60 if direction == "falling" else 0x20,
            "decay_pulse": 0x40,
        }[waveform]
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
                        if ack.mode & 0x60 != expected_waveform_bits:
                            raise MSPM0LinkError(
                                "MSPM0确认的波形类型与请求不一致"
                            )
                        if (waveform == "decay_pulse" and
                                ack.decay_microseconds != decay_microseconds):
                            raise MSPM0LinkError(
                                "MSPM0确认的下降时间与请求不一致"
                            )
                        return ack
            raise MSPM0TimeoutError(
                f"等待MSPM0/FPGA端到端确认超时（{self.port}）"
            )

    def send_waveform_unconfirmed(
        self, waveform: str, frequency_hz: float,
        phase_degrees: float = 0.0, amplitude: int = 8,
        *, direction: str = "rising", decay_microseconds: int = 1000
    ) -> None:
        """Write one valid waveform command without waiting for an FPGA ACK.

        This is intended for slow, operator-observed calibration.  A successful
        return only means the complete command was written to the host UART; it
        must never be presented as an FPGA confirmation.
        """
        payload = encode_waveform_payload(
            waveform, frequency_hz, phase_degrees, amplitude,
            direction=direction, decay_microseconds=decay_microseconds
        )
        with self._lock:
            if self._serial is None:
                self.open()
            self._sequence = (self._sequence % 255) + 1
            self._serial.reset_input_buffer()
            self._serial.write(encode_frame(
                TYPE_PC_SET_WAVEFORM, self._sequence, payload
            ))
            self._serial.flush()

    def arm_double_at_falling_zero(self) -> DoubleAck:
        """Atomically change a free-running sine from 1x to 2x.

        The FPGA ACK is delayed until the first tagged 2x sample has reached
        the DAC interface.  Unlike ordinary waveform writes, this operation
        is never fire-and-forget.
        """
        with self._lock:
            if self._serial is None:
                self.open()
            self._sequence = (self._sequence % 255) + 1
            sequence = self._sequence
            decoder = FrameDecoder(max_payload=64)
            self._serial.reset_input_buffer()
            self._serial.write(encode_frame(
                TYPE_PC_ARM_DOUBLE, sequence
            ))
            self._serial.flush()
            deadline = time.monotonic() + max(self.timeout, 2.0)
            while time.monotonic() < deadline:
                block = self._serial.read(self._serial.in_waiting or 1)
                for frame in decoder.feed(block):
                    if frame.sequence != sequence:
                        continue
                    if frame.frame_type == TYPE_ERROR:
                        raise MSPM0CommandError(decode_error(frame.payload))
                    if frame.frame_type == TYPE_PC_DOUBLE_ACK:
                        ack = decode_double_ack(frame.payload)
                        if ack.status != 0:
                            raise MSPM0LinkError(
                                f"FPGA倍频ACK状态异常：{ack.status}"
                            )
                        if ack.new_phase_step != ((
                                ack.old_phase_step << 1) & 0xFFFFFFFF):
                            raise MSPM0LinkError("FPGA倍频ACK的FTW不是精确2倍")
                        return ack
            raise MSPM0TimeoutError(
                f"等待下降过零倍频及DAC生效确认超时（{self.port}）"
            )

    def set_sawtooth(
        self, frequency_hz: float, phase_degrees: float = 0.0,
        amplitude: int = 8, *, direction: str = "rising"
    ) -> TargetAck:
        return self.set_waveform(
            "sawtooth", frequency_hz, phase_degrees, amplitude, direction=direction
        )

    def set_decay_pulse(
        self, decay_microseconds: int = 1000,
        phase_degrees: float = 0.0, amplitude: int = 8
    ) -> TargetAck:
        """Output the fixed 100 Hz pulse: 9 ms zero, then 1 ms decay."""
        return self.set_waveform(
            "decay_pulse", 100.0, phase_degrees, amplitude,
            decay_microseconds=decay_microseconds
        )
