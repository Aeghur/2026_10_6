"""Command one free-running sine through PC -> MSPM0 -> FPGA."""
from __future__ import annotations

import argparse
import struct
import time

import serial

from protocol import (
    FrameDecoder,
    TYPE_ERROR,
    TYPE_PC_SET_TARGET,
    TYPE_PC_TARGET_ACK,
    decode_error,
    decode_target_ack,
    encode_frame,
)

BAUD = 1_000_000
AMPLITUDE_CODES = {8: 0, 2: 1, 4: 2, 6: 3}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="控制FPGA自由运行DDS输出目标正弦波"
    )
    parser.add_argument("--port", required=True, help="MSPM0串口，例如COM18")
    parser.add_argument("--frequency", type=float, required=True,
                        help="目标输出频率，单位Hz，范围900～110000")
    parser.add_argument("--phase", type=float, default=0.0,
                        help="目标滞后相位，单位度，范围0～359.99")
    parser.add_argument("--amplitude", type=int, choices=(2, 4, 6, 8),
                        default=8, help="输出幅度档位，默认8")
    args = parser.parse_args()

    if not 900.0 <= args.frequency <= 110_000.0:
        parser.error("--frequency必须位于900～110000 Hz")
    if not 0.0 <= args.phase < 360.0:
        parser.error("--phase必须位于0～359.99度")

    frequency_millihz = int(round(args.frequency * 1000.0))
    phase_centidegrees = int(round(args.phase * 100.0))
    payload = struct.pack(
        "<IHB", frequency_millihz, phase_centidegrees,
        AMPLITUDE_CODES[args.amplitude]
    )
    sequence = 1
    decoder = FrameDecoder()

    with serial.Serial(args.port, BAUD, timeout=0.1) as link:
        link.reset_input_buffer()
        link.write(encode_frame(TYPE_PC_SET_TARGET, sequence, payload))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            block = link.read(link.in_waiting or 1)
            for frame in decoder.feed(block):
                if frame.sequence != sequence:
                    continue
                if frame.frame_type == TYPE_ERROR:
                    print(f"MSPM0返回错误：{decode_error(frame.payload)}")
                    return 2
                if frame.frame_type == TYPE_PC_TARGET_ACK:
                    ack = decode_target_ack(frame.payload)
                    free_run = bool(ack.mode & 0x10)
                    print(
                        f"MSPM0已转发 {ack.frequency_millihz / 1000:.3f} Hz，"
                        f"滞后 {ack.phase_centidegrees / 100:.2f}°，"
                        f"幅度 {args.amplitude}/8；"
                        f"自由运行={'是' if free_run else '否'}"
                    )
                    return 0 if free_run else 3
        print("控制确认超时：请检查PC↔MSPM0串口和MSPM0固件")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
