"""Command a free-running sine or sawtooth through host -> MSPM0 -> FPGA."""
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
        description="控制FPGA自由运行DDS输出目标正弦波或锯齿波"
    )
    parser.add_argument(
        "--port", required=True,
        help="MSPM0串口，例如Windows的COM18或Radxa的/dev/ttyS4"
    )
    parser.add_argument("--frequency", type=float,
                        help="目标输出频率；快降脉冲固定100 Hz，其他波形250～210000 Hz")
    parser.add_argument(
        "--arm-double", action="store_true",
        help="将当前自由运行正弦在下降过零处原子切换为精确2倍频"
    )
    parser.add_argument("--phase", type=float, default=0.0,
                        help="目标滞后相位，单位度，范围0～359.99")
    parser.add_argument("--amplitude", type=int, choices=(2, 4, 6, 8),
                        default=8, help="输出幅度档位，默认8")
    parser.add_argument("--decay-us", type=int, default=1000,
                        help="快降脉冲下降时间，1～9999 us，默认1000")
    parser.add_argument(
        "--waveform", choices=("sine", "sawtooth", "decay_pulse"), default="sine",
        help="输出波形，默认sine"
    )
    parser.add_argument("--direction", choices=("rising", "falling"),
                        default="rising", help="锯齿方向：缓升快降或缓降快升")
    args = parser.parse_args()
    if args.arm_double:
        try:
            with MSPM0WaveLink(args.port, timeout=3.0) as link:
                ack = link.arm_double_at_falling_zero()
        except MSPM0TimeoutError as error:
            print(f"倍频确认超时：{error}")
            return 1
        except MSPM0CommandError as error:
            print(str(error))
            return 2
        except (MSPM0LinkError, OSError) as error:
            print(f"串口通信失败：{error}")
            return 3
        print(
            "FPGA已在下降过零处提交并更新DAC："
            f"sample={ack.applied_sample_counter}，"
            f"FTW 0x{ack.old_phase_step:08X} → "
            f"0x{ack.new_phase_step:08X}，"
            f"commit_phase=0x{ack.commit_phase:08X}"
        )
        return 0
    if args.frequency is None:
        parser.error("普通波形命令必须提供--frequency；确定相位倍频请用--arm-double")
    if args.direction == "falling" and args.waveform != "sawtooth":
        parser.error("--direction falling仅适用于--waveform sawtooth")

    if args.waveform == "decay_pulse":
        if args.frequency != 100.0:
            parser.error("decay_pulse的--frequency必须为100 Hz")
        if not 1 <= args.decay_us <= 9999:
            parser.error("decay_pulse的--decay-us必须位于1～9999")
    elif not 250.0 <= args.frequency <= 210_000.0:
        parser.error("--frequency必须位于250～210000 Hz")
    if not 0.0 <= args.phase < 360.0:
        parser.error("--phase必须位于0～359.99度")

    try:
        with MSPM0WaveLink(args.port) as link:
            ack = link.set_waveform(
                args.waveform, args.frequency, args.phase, args.amplitude,
                direction=args.direction, decay_microseconds=args.decay_us
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
    decay_text = (f"，下降 {ack.decay_microseconds} us"
                  if args.waveform == "decay_pulse" else "")
    print(
        f"MSPM0/FPGA已确认 {args.waveform}，"
        f"{ack.frequency_millihz / 1000:.3f} Hz，"
        f"滞后 {ack.phase_centidegrees / 100:.2f}°，"
        f"幅度 {args.amplitude}/8，方向 {args.direction}{decay_text}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
