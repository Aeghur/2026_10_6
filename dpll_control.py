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
TYPE_DPLL_RESULT = 0x91
BAUD = 1_000_000

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


def receive_frame(link: serial.Serial, sequence: int, timeout: float = 3.0):
    decoder = FrameDecoder(max_payload=64)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        block = link.read(link.in_waiting or 1)
        for frame in decoder.feed(block):
            if frame.sequence == sequence:
                return frame
    raise TimeoutError("状态查询超时，请检查两段UART接线、波特率和共地")


def set_phase(link: serial.Serial, sequence: int, phase_deg: float) -> int:
    if not 0.0 <= phase_deg < 360.0:
        raise ValueError("相位必须位于0～359.99°")
    phase_cdeg = int(round(phase_deg * 100.0))
    sequence = (sequence + 1) & 0xFF
    link.write(encode_frame(
        TYPE_SET_PHASE, sequence, struct.pack("<H", phase_cdeg)
    ))
    return sequence


def query_status(link: serial.Serial, sequence: int) -> tuple[int, str]:
    sequence = (sequence + 1) & 0xFF
    link.reset_input_buffer()
    link.write(encode_frame(TYPE_DPLL_STATUS, sequence))
    frame = receive_frame(link, sequence)
    if frame.frame_type == TYPE_ERROR:
        code = struct.unpack("<H", frame.payload)[0]
        raise RuntimeError(f"MSPM0返回错误码 {code}")
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
    text = (
        f"f={frequency:10.3f} Hz  lock={locked}  signal={signal}  "
        f"phase_error={error_deg:+8.3f}°  DAC={dac_code:5d}  "
        f"clip={clip}  OTR={otr}"
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
    args = parser.parse_args()

    try:
        port = choose_port(args.port)
        with serial.Serial(port, BAUD, timeout=0.05) as link:
            link.reset_input_buffer()
            sequence = set_phase(link, 0, args.phase)
            print(f"已连接 {port} @ {BAUD:,} baud，目标滞后 {args.phase:.2f}°")
            print("按Ctrl+C停止；修改相位可重新启动并传入 --phase 90")
            while True:
                sequence, status = query_status(link, sequence)
                print("\r" + status, end="", flush=True)
                time.sleep(max(args.interval, 0.05))
    except KeyboardInterrupt:
        print("\n已停止")
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
