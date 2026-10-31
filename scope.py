"""PC waveform control using the same UART interface as Radxa."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime

from serial.tools import list_ports

from msp_wave_link import MSPM0WaveLink, encode_waveform_payload


class ScopeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("PC 波形控制 · MSPM0 → FPGA")
        root.geometry("940x760")
        root.minsize(800, 640)
        self.link = None
        self.busy = False
        self.closing = False
        self.events = queue.Queue()
        self.port = tk.StringVar()
        self.waveform = tk.StringVar(value="正弦波")
        self.direction = tk.StringVar(value="缓升快降")
        self.frequency = tk.StringVar(value="10000.000")
        self.phase = tk.StringVar(value="0.00")
        self.amplitude = tk.StringVar(value="8")
        self.decay_microseconds = tk.StringVar(value="1000")
        self.double = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="未连接；连接不会自动改变输出")
        self.confirmed = tk.StringVar(value="尚无已确认的输出参数")
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="自由运行波形控制", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="与 Radxa 共用协议 · 1,000,000 baud / 8N1 · 250 Hz～210 kHz").pack(anchor="w", pady=(4, 14))
        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Label(bar, text="串口").pack(side="left")
        self.port_box = ttk.Combobox(bar, textvariable=self.port, width=24)
        self.port_box.pack(side="left", padx=8)
        self.refresh_btn = ttk.Button(bar, text="刷新", command=self.refresh_ports)
        self.refresh_btn.pack(side="left")
        self.connect_btn = ttk.Button(bar, text="连接", command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=8)
        panel = ttk.LabelFrame(outer, text="目标参数（编辑后点击应用）", padding=14)
        panel.pack(fill="x", pady=14)
        self.inputs = []
        def field(row, label, variable, values=None):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=6)
            if values:
                widget = ttk.Combobox(panel, textvariable=variable, values=values, state="readonly", width=24)
            else:
                widget = ttk.Entry(panel, textvariable=variable, width=27)
            widget.grid(row=row, column=1, sticky="w", padx=12)
            self.inputs.append(widget)
            return widget
        field(0, "波形", self.waveform, ("正弦波", "锯齿波", "100 Hz 快降脉冲"))
        self.direction_box = field(1, "锯齿方向", self.direction, ("缓升快降", "缓降快升"))
        self.frequency_entry = field(2, "频率 / Hz", self.frequency)
        self.frequency_hint = ttk.Label(panel, text="精度 0.001 Hz；最终输出 250～210000 Hz")
        self.frequency_hint.grid(row=2, column=2, sticky="w")
        field(3, "滞后相位 / °", self.phase)
        ttk.Label(panel, text="0～359.99°；正值表示滞后").grid(row=3, column=2, sticky="w")
        field(4, "幅度档位 / 8", self.amplitude, ("2", "4", "6", "8"))
        ttk.Label(panel, text="相对峰值档位，不是电压读数").grid(row=4, column=2, sticky="w")
        self.decay_entry = field(5, "下降时间 / us", self.decay_microseconds)
        self.decay_hint = ttk.Label(panel, text="仅用于100 Hz快降脉冲，范围1～9999 us")
        self.decay_hint.grid(row=5, column=2, sticky="w")
        self.double_btn = ttk.Checkbutton(panel, text="二倍频（下发频率 = 填写频率 × 2）", variable=self.double)
        self.double_btn.grid(row=6, column=1, columnspan=2, sticky="w", pady=8)
        self.apply_btn = ttk.Button(panel, text="应用输出", command=self.apply, state="disabled")
        self.apply_btn.grid(row=7, column=1, sticky="w", pady=6)
        ttk.Label(outer, textvariable=self.status, wraplength=850).pack(anchor="w")
        ack_panel = ttk.LabelFrame(outer, text="最近一次端到端确认", padding=12)
        ack_panel.pack(fill="x", pady=12)
        ttk.Label(ack_panel, textvariable=self.confirmed, wraplength=850).pack(anchor="w")
        ttk.Label(outer, text="断开连接会保持最后输出；当前协议没有停止输出命令。\nPC 请使用支持 1 Mbaud 的 3.3 V USB-TTL，TX → PB5，RX → PB4，GND 共地。", wraplength=850).pack(anchor="w")
        self.log = tk.Text(outer, height=7, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(12, 0))
        self.waveform.trace_add("write", lambda *_: self.update_controls())
        self.refresh_ports()
        self.update_controls()
        root.after(50, self.process_events)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def refresh_ports(self):
        ports = [p.device for p in list_ports.comports()]
        self.port_box["values"] = ports
        if ports and not self.port.get():
            self.port.set(ports[0])

    def update_controls(self):
        decay_pulse = self.waveform.get() == "100 Hz 快降脉冲"
        if decay_pulse and self.frequency.get() != "100.000":
            self.frequency.set("100.000")
            self.double.set(False)
        state = "disabled" if self.busy else "normal"
        self.port_box.configure(state="disabled" if self.busy or self.link else "normal")
        self.refresh_btn.configure(state=state)
        self.connect_btn.configure(state=state, text="断开" if self.link else "连接")
        self.apply_btn.configure(state="normal" if self.link and not self.busy else "disabled")
        for widget in self.inputs:
            widget.configure(state="disabled" if self.busy else ("readonly" if isinstance(widget, ttk.Combobox) else "normal"))
        self.direction_box.configure(state="readonly" if not self.busy and self.waveform.get() == "锯齿波" else "disabled")
        self.frequency_entry.configure(state="disabled" if self.busy or decay_pulse else "normal")
        self.decay_entry.configure(state="normal" if decay_pulse and not self.busy else "disabled")
        self.double_btn.configure(state="disabled" if self.busy or decay_pulse else "normal")
        self.frequency_hint.configure(text=(
            "固定100 Hz：保持负峰值，瞬升正峰值后按设定时间下降"
            if decay_pulse else "精度 0.001 Hz；最终输出 250～210000 Hz"))

    def run_job(self, kind, operation):
        self.busy = True
        self.update_controls()
        def worker():
            try:
                self.events.put((kind, operation()))
            except Exception as error:
                self.events.put(("error", error))
        threading.Thread(target=worker, daemon=True).start()

    def toggle_connection(self):
        if self.busy:
            return
        if self.link:
            link = self.link
            def disconnect():
                link.close()
            self.run_job("disconnected", disconnect)
        else:
            port = self.port.get().strip()
            if not port:
                self.status.set("请选择或填写串口，例如 COM18")
                return
            def connect():
                link = MSPM0WaveLink(port, timeout=1.0)
                try:
                    link.open()
                except Exception:
                    link.close()
                    raise
                return link
            self.status.set("正在连接串口…")
            self.run_job("connected", connect)

    def apply(self):
        if self.busy or not self.link:
            return
        try:
            waveform = {
                "正弦波": "sine",
                "锯齿波": "sawtooth",
                "100 Hz 快降脉冲": "decay_pulse",
            }[self.waveform.get()]
            direction = "falling" if waveform == "sawtooth" and self.direction.get() == "缓降快升" else "rising"
            frequency = float(self.frequency.get()) * (2 if self.double.get() else 1)
            phase = float(self.phase.get())
            amplitude = int(self.amplitude.get())
            decay_microseconds = int(self.decay_microseconds.get())
            encode_waveform_payload(
                waveform, frequency, phase, amplitude, direction=direction,
                decay_microseconds=decay_microseconds)
        except (ValueError, OverflowError) as error:
            self.status.set(f"参数无效：{error}")
            return
        link = self.link
        self.confirmed.set("正在等待确认；此前记录不能代表当前硬件状态")
        self.status.set("正在等待 MSPM0 / FPGA 确认…")
        def send():
            ack = link.set_waveform(
                waveform, frequency, phase, amplitude, direction=direction,
                decay_microseconds=decay_microseconds)
            tolerance = max(0.002, frequency * 0.5e-6)
            expected_phase = round(phase * 100)
            expected_amp = {8: 0, 2: 1, 4: 2, 6: 3}[amplitude]
            if (not ack.mode & 1 or ack.mode & 0x80 or
                    (ack.mode >> 2) & 3 != expected_amp or
                    ack.amplitude_code != expected_amp or
                    ack.phase_centidegrees != expected_phase or
                    abs(ack.frequency_millihz / 1000 - frequency) > tolerance):
                raise ValueError("确认参数与请求不一致，请检查固件与硬件链路")
            if (waveform == "decay_pulse" and
                    ack.decay_microseconds != decay_microseconds):
                raise ValueError("确认的下降时间与请求不一致")
            return ack
        self.run_job("applied", send)

    def process_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.busy = False
                if kind == "connected":
                    self.link = value
                    self.confirmed.set("连接后尚未下发参数；硬件当前状态未知")
                    self.status.set(f"已连接 {value.port}；可以应用输出")
                elif kind == "disconnected":
                    self.link = None
                    self.confirmed.set("已断开；硬件可能继续保持最后输出")
                    self.status.set("已断开连接")
                elif kind == "applied":
                    ack = value
                    name = {
                        0x00: "正弦波",
                        0x20: "缓升快降锯齿",
                        0x40: "100 Hz 快降脉冲",
                        0x60: "缓降快升锯齿",
                    }[ack.mode & 0x60]
                    amplitude = {0: 8, 1: 2, 2: 4, 3: 6}[ack.amplitude_code]
                    decay_text = (f"  |  下降 {ack.decay_microseconds} us"
                                  if ack.mode & 0x60 == 0x40 else "")
                    self.confirmed.set(f"{name}  |  {ack.frequency_millihz / 1000:.3f} Hz  |  滞后 {ack.phase_centidegrees / 100:.2f}°  |  幅度 {amplitude}/8{decay_text}")
                    self.status.set("FPGA 已应用并确认输出参数（模拟输出请用示波器核验）")
                else:
                    if self.link:
                        try:
                            self.link.close()
                        except Exception:
                            pass
                        self.link = None
                    self.confirmed.set("未确认；硬件可能已经改变输出，请重新连接并应用")
                    self.status.set(f"操作失败：{value}")
                self.log.configure(state="normal")
                self.log.insert("end", f"{datetime.now():%H:%M:%S}  {self.status.get()}\n")
                self.log.see("end")
                self.log.configure(state="disabled")
                self.update_controls()
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            if self.link:
                self.link.close()
            self.root.destroy()
            return
        self.root.after(50, self.process_events)

    def close(self):
        self.closing = True
        if self.busy:
            self.status.set("正在等待当前串口操作结束后关闭…")
        else:
            if self.link:
                self.link.close()
            self.root.destroy()


if __name__ == "__main__":
    app_root = tk.Tk()
    ScopeApp(app_root)
    app_root.mainloop()
