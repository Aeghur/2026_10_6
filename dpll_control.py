"""Live control and diagnostics for the standalone FPGA DPLL/DDS project."""
from __future__ import annotations

import argparse
import struct
import sys
import time

import serial
from serial.tools import list_ports

from protocol import FrameDecoder, TYPE_ERROR, encode_frame

TYPE_SET_PHASE = 0x11
TYPE_DPLL_STATUS = 0x12
TYPE_LOOP_PROFILE = 0x13
TYPE_DIAGNOSTICS = 0x14
TYPE_DPLL_RESULT = 0x91
TYPE_DIAG_RESULT = 0x92
BAUD = 1_000_000
DEFAULT_RESPONSE_TIMEOUT = 10.0
COMMAND_SETTLE_SECONDS = 0.05
SAMPLE_RATE = 25_000_000
LOOP_PROFILES = {"legacy": 0, "balanced": 1, "fast": 2}
PROFILE_SETTINGS = {
    0: ("61Hz", 1024),
    1: ("243Hz", 1024),
    2: ("243Hz", 256),
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def choose_port(requested: str | None) -> str:
    if requested:
        return requested
    ports = list(list_ports.comports())
    if len(ports) == 1:
        return ports[0].device
    if not ports:
        raise RuntimeError("未找到串口，请连接USB-TTL后重试")
    names = ", ".join(item.device for item in ports)
    raise RuntimeError(f"检测到多个串口（{names}），请用 --port COMx 指定")


def receive_frame(
    link: serial.Serial, sequence: int,
    timeout: float = DEFAULT_RESPONSE_TIMEOUT,
):
    decoder = FrameDecoder(max_payload=64)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        block = link.read(link.in_waiting or 1)
        for frame in decoder.feed(block):
            if frame.sequence == sequence:
                return frame
    raise TimeoutError(
        f"{timeout:g}秒内未收到MSPM0回应；请先关闭占用串口的旧界面，"
        "再确认MSPM0已烧录最新HEX"
    )


def describe_fpga_error(code: int) -> str:
    names = {
        1: "FPGA返回超时",
        2: "FPGA返回CRC错误",
        3: "FPGA返回帧格式错误",
    }
    return names.get(code, f"未知错误{code}")


def set_phase(link: serial.Serial, sequence: int, phase_deg: float) -> int:
    if not 0.0 <= phase_deg < 360.0:
        raise ValueError("相位必须位于0～359.99°")
    phase_cdeg = int(round(phase_deg * 100.0))
    sequence = (sequence + 1) & 0xFF
    link.write(encode_frame(
        TYPE_SET_PHASE, sequence, struct.pack("<H", phase_cdeg)
    ))
    return sequence


def set_loop_profile(link: serial.Serial, sequence: int, profile: str) -> int:
    sequence = (sequence + 1) & 0xFF
    link.write(encode_frame(
        TYPE_LOOP_PROFILE, sequence, bytes((LOOP_PROFILES[profile],))
    ))
    return sequence


def query_status(link: serial.Serial, sequence: int) -> tuple[int, str]:
    sequence = (sequence + 1) & 0xFF
    link.reset_input_buffer()
    link.write(encode_frame(TYPE_DPLL_STATUS, sequence))
    frame = receive_frame(link, sequence)
    if frame.frame_type == TYPE_ERROR:
        code = struct.unpack("<H", frame.payload)[0]
        raise RuntimeError(
            f"MSPM0已响应，但MSPM0→FPGA状态查询失败："
            f"{describe_fpga_error(code)}（错误码{code}）"
        )
    if frame.frame_type != TYPE_DPLL_RESULT or len(frame.payload) != 16:
        raise RuntimeError(
            f"收到非DPLL状态帧 type=0x{frame.frame_type:02X}, "
            f"length={len(frame.payload)}"
        )
    increment, error_q23, flags, dac_code, sample_rate = struct.unpack(
        "<IiHHI", frame.payload
    )
    frequency = increment * sample_rate / 2**32
    error_deg = error_q23 * 180.0 / 2**23
    locked = "是" if flags & 0x0001 else "否"
    signal = "有" if flags & 0x0002 else "无"
    clip = "是" if flags & 0x0004 else "否"
    otr = "是" if flags & 0x0008 else "否"
    profile = (flags >> 4) & 0x03
    profile_name = next(
        (name for name, value in LOOP_PROFILES.items() if value == profile),
        f"unknown({profile})"
    )
    lpf_bandwidth, loop_decimation = PROFILE_SETTINGS.get(profile, ("?", 0))
    text = (
        f"f={frequency:10.3f} Hz  lock={locked}  signal={signal}  "
        f"phase_error={error_deg:+8.3f}°  DAC={dac_code:5d}  "
        f"profile={profile_name} LPF={lpf_bandwidth} PI/{loop_decimation}  "
        f"clip={clip}  OTR={otr}"
    )
    return sequence, text


def query_diagnostics(link: serial.Serial, sequence: int) -> tuple[int, str]:
    sequence = (sequence + 1) & 0xFF
    link.reset_input_buffer()
    link.write(encode_frame(TYPE_DIAGNOSTICS, sequence))
    frame = receive_frame(link, sequence)
    if frame.frame_type == TYPE_ERROR:
        code = struct.unpack("<H", frame.payload)[0]
        raise RuntimeError(
            f"MSPM0已响应，但MSPM0→FPGA诊断查询失败："
            f"{describe_fpga_error(code)}（错误码{code}）"
        )
    if frame.frame_type != TYPE_DIAG_RESULT or len(frame.payload) != 8:
        raise RuntimeError(
            f"收到非诊断帧 type=0x{frame.frame_type:02X}, "
            f"length={len(frame.payload)}"
        )
    i_div256, q_div256, magnitude_div256, integrator_div32 = struct.unpack(
        "<hhhh", frame.payload
    )
    i_value = i_div256 * 256
    q_value = q_div256 * 256
    magnitude = magnitude_div256 * 256
    integrator = integrator_div32 * 32
    text = (
        f" I={i_value:+8d} Q={q_value:+8d} "
        f"mag={magnitude:7d} int={integrator:+8d}"
    )
    return sequence, text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="设置DPLL输出滞后角并实时查看FPGA锁定状态"
    )
    parser.add_argument("--port", help="MSPM0的串口，例如COM6")
    parser.add_argument("--phase", type=float, default=0.0,
                        help="输出滞后角，默认0°")
    parser.add_argument("--interval", type=float, default=0.25,
                        help="状态刷新间隔秒数，默认0.25")
    parser.add_argument(
        "--loop-profile", choices=LOOP_PROFILES, default="balanced",
        help="环路配置：legacy最稳但最慢，balanced默认，fast用于跳频测试"
    )
    parser.add_argument(
        "--basic", action="store_true",
        help="使用旧16字节状态查询，不显示I/Q与积分器"
    )
    args = parser.parse_args()

    try:
        port = choose_port(args.port)
        with serial.Serial(port, BAUD, timeout=0.05) as link:
            link.reset_input_buffer()
            sequence = set_phase(link, 0, args.phase)
            link.flush()
            time.sleep(COMMAND_SETTLE_SECONDS)
            sequence = set_loop_profile(link, sequence, args.loop_profile)
            link.flush()
            time.sleep(COMMAND_SETTLE_SECONDS)
            print(f"已连接 {port} @ {BAUD:,} baud，目标滞后 {args.phase:.2f}°")
            print(
                f"环路={args.loop_profile}；按Ctrl+C停止；"
                "修改相位可重新启动并传入 --phase 90"
            )
            while True:
                if args.basic:
                    sequence, status = query_status(link, sequence)
                else:
                    sequence, status = query_status(link, sequence)
                    sequence, diagnostics = query_diagnostics(link, sequence)
                    status += diagnostics
                # Keep every sample on its own line.  The extended diagnostic
                # line has variable width; carriage-return updates leave stale
                # text (for example "I=f=") in Windows cmd and also destroy
                # the history needed when comparing loop profiles.
                print(status, flush=True)
                time.sleep(max(args.interval, 0.05))
    except KeyboardInterrupt:
        print("已停止")
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
