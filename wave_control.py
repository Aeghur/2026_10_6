"""Command one free-running waveform through Radxa -> MSPM0 -> FPGA."""
from __future__ import annotations

import argparse
from msp_wave_link import (
    MSPM0CommandError,
    MSPM0LinkError,
    MSPM0TimeoutError,
    MSPM0WaveLink,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="控制FPGA自由运行DDS输出正弦波或锯齿波"
    )
    parser.add_argument(
        "--port", required=True,
        help="MSPM0串口，例如Windows的COM18或Radxa的/dev/ttyS4"
    )
    parser.add_argument("--frequency", type=float, required=True,
                        help="目标输出频率，单位Hz，范围900～110000")
    parser.add_argument("--phase", type=float, default=0.0,
                        help="目标滞后相位，单位度，范围0～359.99")
    parser.add_argument("--amplitude", type=int, choices=(2, 4, 6, 8),
                        default=8, help="输出幅度档位，默认8")
    parser.add_argument("--waveform", choices=("sine", "sawtooth"),
                        default="sine", help="输出波形，默认sine")
    args = parser.parse_args()

    if not 900.0 <= args.frequency <= 110_000.0:
        parser.error("--frequency必须位于900～110000 Hz")
    if not 0.0 <= args.phase < 360.0:
        parser.error("--phase必须位于0～359.99度")

    try:
        with MSPM0WaveLink(args.port) as link:
            ack = link.set_waveform(
                args.waveform, args.frequency, args.phase, args.amplitude
            )
    except MSPM0TimeoutError as error:
        print(f"控制确认超时：{error}")
        return 1
    except MSPM0CommandError as error:
        print(str(error))
        return 2
    except (MSPM0LinkError, OSError) as error:
        print(f"串口通信失败：{error}")
        return 3
    print(
        f"MSPM0/FPGA已确认 {ack.frequency_millihz / 1000:.3f} Hz，"
        f"滞后 {ack.phase_centidegrees / 100:.2f}°，"
        f"幅度 {args.amplitude}/8，波形 {args.waveform}，mode=0x{ack.mode:02X}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
