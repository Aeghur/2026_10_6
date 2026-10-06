"""MSPM0 FFT capture and spectrum display."""
from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import serial
from serial.tools import list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from protocol import (
    CaptureResult,
    FrameDecoder,
    TYPE_ERROR,
    TYPE_PC_CAPTURE,
    TYPE_PC_SET_PHASE,
    TYPE_PC_SET_MODE,
    TYPE_PC_RESULT,
    decode_error,
    decode_result,
    encode_frame,
)

BAUD = 1_000_000


class ScopeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("AD9226 / MSPM0 FFT 测量")
        root.geometry("1150x760")
        self.serial: serial.Serial | None = None
        self.stop_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.latest: CaptureResult | None = None
        self.sequence = 0

        bar = ttk.Frame(root, padding=8)
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="串口").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=16)
        self.port_box.pack(side=tk.LEFT, padx=5)
        ttk.Button(bar, text="刷新", command=self.refresh_ports).pack(side=tk.LEFT)
        self.connect_btn = ttk.Button(
            bar, text="连接", command=self.toggle_connection
        )
        self.connect_btn.pack(side=tk.LEFT, padx=8)
        self.capture_btn = ttk.Button(
            bar, text="单次采集", command=self.capture, state=tk.DISABLED
        )
        self.capture_btn.pack(side=tk.LEFT, padx=8)
        ttk.Label(bar, text="目标滞后").pack(side=tk.LEFT, padx=(12, 3))
        self.phase_var = tk.DoubleVar(value=90.0)
        ttk.Spinbox(
            bar, from_=0.0, to=359.99, increment=1.0,
            textvariable=self.phase_var, width=7
        ).pack(side=tk.LEFT)
        ttk.Label(bar, text="°").pack(side=tk.LEFT)
        ttk.Label(bar, text="输出模式").pack(side=tk.LEFT, padx=(12, 3))
        self.mode_var = tk.StringVar(value="流水线延迟")
        self.mode_box = ttk.Combobox(
            bar, textvariable=self.mode_var, width=12, state="readonly",
            values=("流水线延迟", "过零锁相DDS")
        )
        self.mode_box.pack(side=tk.LEFT)
        ttk.Label(bar, text="频谱单位").pack(side=tk.LEFT, padx=(18, 3))
        self.db_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar, text="相对 dB", variable=self.db_var, command=self.redraw
        ).pack(side=tk.LEFT)
        ttk.Button(bar, text="保存 CSV", command=self.save_csv).pack(side=tk.RIGHT)

        self.figure = Figure(figsize=(10, 6), dpi=100)
        self.time_axis = self.figure.add_subplot(211)
        self.freq_axis = self.figure.add_subplot(212)
        self.figure.tight_layout(pad=2.2)
        self.canvas = FigureCanvasTkAgg(self.figure, master=root)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8)

        self.status = tk.StringVar(value="未连接")
        ttk.Label(root, textvariable=self.status, padding=8).pack(fill=tk.X)
        self.refresh_ports()
        self.root.after(50, self.process_events)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def refresh_ports(self) -> None:
        ports = [item.device for item in list_ports.comports()]
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def toggle_connection(self) -> None:
        if self.serial and self.serial.is_open:
            self.disconnect()
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("串口", "请选择 USB-TTL 串口")
            return
        try:
            self.serial = serial.Serial(port, BAUD, timeout=0.1)
            self.serial.reset_input_buffer()
        except serial.SerialException as exc:
            messagebox.showerror("串口打开失败", str(exc))
            return
        self.stop_event.clear()
        threading.Thread(target=self.reader, daemon=True).start()
        self.connect_btn.config(text="断开")
        self.capture_btn.config(state=tk.NORMAL)
        self.status.set(f"已连接 {port}，{BAUD:,} baud")

    def disconnect(self) -> None:
        self.stop_event.set()
        if self.serial:
            try:
                self.serial.close()
            except serial.SerialException:
                pass
        self.serial = None
        self.connect_btn.config(text="连接")
        self.capture_btn.config(state=tk.DISABLED)
        self.status.set("未连接")

    def capture(self) -> None:
        if not self.serial or not self.serial.is_open:
            return
        self.sequence = (self.sequence + 1) & 0xFF
        try:
            phase = float(self.phase_var.get())
        except (ValueError, tk.TclError):
            self.status.set("目标相位必须是0～359.99°的数字")
            return
        if not 0.0 <= phase < 360.0:
            self.status.set("目标相位必须位于0～359.99°")
            return
        phase_cdeg = int(round(phase * 100.0))
        mode = 1 if self.mode_var.get() == "过零锁相DDS" else 0
        try:
            self.serial.write(encode_frame(
                TYPE_PC_SET_MODE, self.sequence, bytes((mode,))
            ))
            self.serial.write(encode_frame(
                TYPE_PC_SET_PHASE, self.sequence,
                phase_cdeg.to_bytes(2, "little")
            ))
            self.serial.write(encode_frame(TYPE_PC_CAPTURE, self.sequence))
        except serial.SerialException as exc:
            self.status.set(f"发送失败：{exc}")
            return
        self.capture_btn.config(state=tk.DISABLED)
        self.status.set("正在等待 FPGA 采集并由 MSPM0 计算 FFT…")
        self.root.after(2500, lambda sequence=self.sequence: self.capture_timeout(sequence))

    def capture_timeout(self, sequence: int) -> None:
        if sequence == self.sequence and str(self.capture_btn["state"]) == "disabled":
            self.capture_btn.config(state=tk.NORMAL)
            self.status.set("采集超时：请检查 FPGA 程序、两段 UART 接线和共地")

    def reader(self) -> None:
        decoder = FrameDecoder()
        try:
            while not self.stop_event.is_set() and self.serial:
                block = self.serial.read(self.serial.in_waiting or 1)
                for frame in decoder.feed(block):
                    if (frame.frame_type == TYPE_ERROR and frame.sequence == self.sequence):
                        try:
                            code = decode_error(frame.payload)
                        except ValueError as exc:
                            self.events.put(("error", str(exc)))
                        else:
                            names = {1: "FPGA响应超时", 2: "FPGA帧格式错误", 3: "FPGA帧CRC错误"}
                            self.events.put(("error", names.get(code, f"MSPM0错误码 {code}")))
                        continue
                    if frame.frame_type != TYPE_PC_RESULT:
                        continue
                    if frame.sequence != self.sequence:
                        continue
                    try:
                        result = decode_result(frame.payload)
                    except ValueError as exc:
                        self.events.put(("error", str(exc)))
                    else:
                        self.events.put(("result", result))
                if decoder.crc_errors:
                    self.events.put(("crc", decoder.crc_errors))
                    decoder.crc_errors = 0
        except serial.SerialException as exc:
            self.events.put(("error", f"串口错误：{exc}"))

    def process_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "result":
                    self.latest = value  # type: ignore[assignment]
                    self.capture_btn.config(state=tk.NORMAL)
                    self.redraw()
                elif kind == "crc":
                    self.status.set(f"收到错误 CRC 帧，累计新增 {value} 帧")
                    self.capture_btn.config(state=tk.NORMAL)
                else:
                    self.status.set(str(value))
                    self.capture_btn.config(state=tk.NORMAL)
        except queue.Empty:
            pass
        self.root.after(50, self.process_events)

    def redraw(self) -> None:
        if self.latest is None:
            return
        result = self.latest
        samples = np.asarray(result.samples, dtype=np.float64)
        spectrum = np.asarray(result.spectrum, dtype=np.float64)
        time_ms = np.arange(samples.size) * 1000.0 / result.sample_rate
        freq_khz = (
            np.arange(spectrum.size) * result.sample_rate / samples.size / 1000.0
        )

        self.time_axis.clear()
        self.time_axis.plot(time_ms, samples, lw=0.8)
        self.time_axis.set(
            xlabel="时间 (ms)", ylabel="ADC code", title="4096 点时域波形"
        )
        self.time_axis.grid(True, alpha=0.3)

        self.freq_axis.clear()
        if self.db_var.get():
            full_scale = max(float(np.max(spectrum)), 1.0)
            y = 20.0 * np.log10(np.maximum(spectrum, 1.0) / full_scale)
            ylabel = "相对幅值 (dB)"
        else:
            y = spectrum
            ylabel = "幅值 (FFT code)"
        self.freq_axis.plot(freq_khz, y, lw=0.9)
        self.freq_axis.set(
            xlabel="频率 (kHz)", ylabel=ylabel, title="0～110 kHz 单频频谱"
        )
        self.freq_axis.grid(True, alpha=0.3)

        for peak in result.peaks:
            x = peak.frequency_hz / 1000.0
            bin_no = int(round(peak.frequency_hz * samples.size / result.sample_rate))
            if 0 <= bin_no < y.size:
                self.freq_axis.annotate(
                    f"{x:.3f} kHz",
                    (x, y[bin_no]),
                    xytext=(5, 8),
                    textcoords="offset points",
                )

        resolution = result.sample_rate / samples.size
        peak_text = f"{result.peaks[0].frequency_hz/1000:.3f} kHz"
        otr = "触发" if result.flags & 1 else "正常"
        dac_clip = "触发" if result.flags & 2 else "正常"
        phase_range = "超范围" if result.flags & 4 else "正常"
        output_mode = "DDS" if result.flags & 8 else "流水线"
        dds_lock = "已锁定" if result.flags & 16 else "未锁定"
        dds_signal = "有信号" if result.flags & 32 else "无信号"
        dds_state = f"{dds_lock}/{dds_signal}" if result.flags & 8 else "—"
        self.status.set(
            f"Fs={result.sample_rate:,} S/s，N={samples.size}，"
            f"Δf={resolution:.3f} Hz，CRC=正常，OTR={otr}，"
            f"DAC削顶={dac_clip}，调相={phase_range}，模式={output_mode}，"
            f"DDS={dds_state}；频率：{peak_text}"
        )
        self.figure.tight_layout(pad=2.2)
        self.canvas.draw_idle()

    def save_csv(self) -> None:
        if self.latest is None:
            messagebox.showinfo("保存", "当前没有采集数据")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if not path:
            return
        result = self.latest
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["index", "time_s", "adc_code"])
            for index, value in enumerate(result.samples):
                writer.writerow([index, index / result.sample_rate, value])

    def close(self) -> None:
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    app_root = tk.Tk()
    ScopeApp(app_root)
    app_root.mainloop()
