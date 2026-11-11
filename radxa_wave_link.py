"""Long-lived SSH bridge to the existing Radxa UART controller; no server install."""
from __future__ import annotations

import base64
import json
import queue
import shlex
import subprocess
import threading
import time

from msp_wave_link import encode_waveform_payload, MSPM0LinkError
from protocol import DoubleAck, TargetAck

MARKER = "WAVE_JSON:"
REMOTE_WORKER = r'''
import sys, os, json, shutil, struct, subprocess, time

def reply(value):
    print("WAVE_JSON:" + json.dumps(value), flush=True)

link = None
try:
    os.chdir(sys.argv[1])
    sys.path.insert(0, os.getcwd())
    from msp_wave_link import MSPM0WaveLink, encode_waveform_payload
    from protocol import FrameDecoder, TYPE_PC_SET_WAVEFORM, encode_frame
    if not shutil.which("fuser"):
        raise RuntimeError("缺少 fuser，无法检查串口占用，请安装 psmisc")
    check = subprocess.run(["fuser", sys.argv[2]], capture_output=True)
    if check.returncode == 0:
        raise RuntimeError("串口已被其他程序占用，请先退出视觉自动控制或串口程序；PID: " + check.stdout.decode().strip())
    if check.returncode != 1:
        raise RuntimeError("无法检查串口占用: " + check.stderr.decode(errors="replace"))
    link = MSPM0WaveLink(sys.argv[2], timeout=1.0)
    link.open()
    link._serial.exclusive = True
    reply({"ready": True})
    for line in sys.stdin:
        request = json.loads(line)
        if request.get("close"):
            break
        try:
            if request.get("operation") == "arm_double":
                with link._lock:
                    link._sequence = (link._sequence % 255) + 1
                    sequence = link._sequence
                    link._serial.reset_input_buffer()
                    link._serial.write(encode_frame(0x15, sequence))
                    link._serial.flush()
                    decoder = FrameDecoder(max_payload=64)
                    deadline = time.monotonic() + 3.0
                    result = None
                    while time.monotonic() < deadline and result is None:
                        block = link._serial.read(link._serial.in_waiting or 1)
                        for frame in decoder.feed(block):
                            if frame.sequence != sequence:
                                continue
                            if frame.frame_type == 0xE0:
                                code = struct.unpack("<H", frame.payload)[0]
                                raise RuntimeError("MSPM0错误%d" % code)
                            if frame.frame_type == 0x94 and len(frame.payload) == 17:
                                values = struct.unpack("<BIIII", frame.payload)
                                result = dict(zip(("status", "applied_sample_counter",
                                    "old_phase_step", "new_phase_step",
                                    "commit_phase"), values))
                    if result is None:
                        raise RuntimeError("等待下降过零倍频及DAC生效确认超时")
                reply({"double_ack": result})
                continue
            wait_for_confirmation = request.pop("wait_for_confirmation", True)
            if wait_for_confirmation:
                ack = link.set_waveform(**request)
                reply({"ack": {"frequency_millihz": ack.frequency_millihz,
                               "phase_centidegrees": ack.phase_centidegrees,
                               "amplitude_code": ack.amplitude_code,
                               "mode": ack.mode,
                               "decay_microseconds": ack.decay_microseconds}})
            else:
                payload = encode_waveform_payload(**request)
                with link._lock:
                    link._sequence = (link._sequence % 255) + 1
                    link._serial.reset_input_buffer()
                    link._serial.write(encode_frame(
                        TYPE_PC_SET_WAVEFORM, link._sequence, payload))
                    link._serial.flush()
                reply({"sent": True})
        except Exception as error:
            reply({"error": str(error)})
            break
except Exception as error:
    reply({"error": str(error)})
finally:
    if link is not None:
        link.close()
'''


class RadxaWaveLink:
    def __init__(self, host="192.168.137.2", username="radxa", *,
                 directory="/home/radxa/vision_sorter",
                 python="/home/radxa/vision_sorter/.venv/bin/python",
                 serial_port="/dev/ttyS4", timeout=8.0,
                 wait_for_confirmation=True):
        self.port = f"{username}@{host} → {serial_port}"
        self.host, self.username = host, username
        self.directory, self.python, self.serial_port = directory, python, serial_port
        self.timeout = timeout
        self.wait_for_confirmation = wait_for_confirmation
        self.process = None
        self.messages = queue.Queue()

    def open(self):
        if not self.host.strip() or self.host.startswith("-") or not self.username.strip() or self.username.startswith("-"):
            raise ValueError("请填写有效的主机地址和用户名")
        code = base64.b64encode(REMOTE_WORKER.encode()).decode("ascii")
        command = shlex.join([self.python, "-u", "-c",
                              f"import base64;exec(base64.b64decode('{code}'))",
                              self.directory, self.serial_port])
        self.process = subprocess.Popen(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
             "-o", "ServerAliveCountMax=2", "-l", self.username, self.host, command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process = self.process
        def read_messages():
            try:
                for line in process.stdout:
                    self.messages.put(line.rstrip())
            finally:
                self.messages.put(None)
        threading.Thread(target=read_messages, daemon=True).start()
        try:
            if self._receive().get("ready") is not True:
                raise MSPM0LinkError("Radxa 未返回就绪确认")
        except Exception:
            self.close()
            raise
        return self

    def _receive(self):
        deadline = time.monotonic() + self.timeout
        diagnostics = []
        while True:
            try:
                line = self.messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty:
                raise MSPM0LinkError("等待 Radxa 网络响应超时；输出状态未确认") from None
            if line is None:
                raise MSPM0LinkError("SSH 连接结束：" + " / ".join(diagnostics[-5:]))
            if line.startswith(MARKER):
                result = json.loads(line[len(MARKER):])
                if "error" in result:
                    raise MSPM0LinkError("Radxa: " + result["error"])
                return result
            diagnostics.append(line)
            if time.monotonic() >= deadline:
                raise MSPM0LinkError("Radxa 响应超时：" + " / ".join(diagnostics[-5:]))

    def set_waveform(self, waveform, frequency_hz, phase_degrees=0.0,
                     amplitude=8, *, direction="rising",
                     decay_microseconds=1000):
        encode_waveform_payload(
            waveform, frequency_hz, phase_degrees, amplitude,
            direction=direction, decay_microseconds=decay_microseconds)
        if self.process is None or self.process.poll() is not None:
            raise MSPM0LinkError("尚未连接 Radxa")
        self.process.stdin.write(json.dumps(dict(
            waveform=waveform, frequency_hz=frequency_hz,
            phase_degrees=phase_degrees, amplitude=amplitude,
            direction=direction,
            decay_microseconds=decay_microseconds,
            wait_for_confirmation=self.wait_for_confirmation)) + "\n")
        self.process.stdin.flush()
        response = self._receive()
        if not self.wait_for_confirmation:
            if response.get("sent") is not True:
                raise MSPM0LinkError("Radxa 未确认串口命令已写入")
            waveform_bits = {
                "sine": 0x00,
                "sawtooth": 0x60 if direction == "falling" else 0x20,
                "decay_pulse": 0x40,
            }[waveform]
            amplitude_code = {8: 0, 2: 1, 4: 2, 6: 3}[amplitude]
            return TargetAck(
                frequency_millihz=int(round(frequency_hz * 1000)),
                phase_centidegrees=int(round(phase_degrees * 100)),
                amplitude_code=amplitude_code,
                mode=0x11 | (amplitude_code << 2) | waveform_bits,
                decay_microseconds=(decay_microseconds
                                    if waveform == "decay_pulse" else 0),
            )
        try:
            ack = TargetAck(**response["ack"])
        except (KeyError, TypeError) as error:
            raise MSPM0LinkError("Radxa 返回的确认格式无效") from error
        expected_waveform_bits = {
            "sine": 0x00,
            "sawtooth": 0x60 if direction == "falling" else 0x20,
            "decay_pulse": 0x40,
        }[waveform]
        if not ack.mode & 0x10 or ack.mode & 0x60 != expected_waveform_bits:
            raise MSPM0LinkError("Radxa 确认的输出模式、波形或方向不匹配")
        if (waveform == "decay_pulse" and
                ack.decay_microseconds != decay_microseconds):
            raise MSPM0LinkError("Radxa确认的下降时间与请求不匹配")
        return ack

    def arm_double_at_falling_zero(self):
        """Wait for the FPGA's phase-deterministic 1x-to-2x DAC-applied ACK."""
        if self.process is None or self.process.poll() is not None:
            raise MSPM0LinkError("尚未连接 Radxa")
        self.process.stdin.write(json.dumps({"operation": "arm_double"}) + "\n")
        self.process.stdin.flush()
        response = self._receive()
        try:
            ack = DoubleAck(**response["double_ack"])
        except (KeyError, TypeError) as error:
            raise MSPM0LinkError("Radxa 返回的倍频确认格式无效") from error
        if ack.status != 0:
            raise MSPM0LinkError(f"FPGA倍频ACK状态异常：{ack.status}")
        if ack.new_phase_step != ((ack.old_phase_step << 1) & 0xFFFFFFFF):
            raise MSPM0LinkError("FPGA倍频ACK的FTW不是精确2倍")
        return ack

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    process.stdin.write('{"close":true}\n')
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
                process.stdin.close()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=2)
        finally:
            if process.stdout:
                process.stdout.close()
