"""Network waveform window: PC --SSH--> Radxa --UART--> MSPM0 --> FPGA."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from scope import ScopeApp
from radxa_wave_link import RadxaWaveLink


class RadxaScopeApp(ScopeApp):
    def refresh_ports(self):
        pass

    def __init__(self, root):
        super().__init__(root)
        root.title("Radxa 网络波形控制 · 192.168.137.2")
        root.geometry("1000x850")
        root.minsize(940, 800)
        outer = self.port_box.master.master
        children = list(outer.winfo_children())
        children[1].configure(text="PC → SSH → Radxa → MSPM0 → FPGA · 现有 1 Mbaud 波形协议")
        children[2].pack_forget()
        bar = ttk.LabelFrame(outer, text="Radxa 网络连接", padding=10)
        bar.pack(fill="x", pady=8, before=children[3])
        self.host = tk.StringVar(value="192.168.137.2")
        self.username = tk.StringVar(value="radxa")
        self.directory = tk.StringVar(value="/home/radxa/vision_sorter")
        self.python_path = tk.StringVar(value="/home/radxa/vision_sorter/.venv/bin/python")
        self.uart = tk.StringVar(value="/dev/ttyS4")
        self.network_inputs = []
        for row, (label, variable) in enumerate([
                ("地址", self.host), ("SSH 用户", self.username),
                ("程序目录", self.directory), ("Python 路径", self.python_path),
                ("Radxa 串口", self.uart)]):
            ttk.Label(bar, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(bar, textvariable=variable, width=55)
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            self.network_inputs.append(entry)
        bar.columnconfigure(1, weight=1)
        self.connect_btn = ttk.Button(bar, text="连接", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=2, padx=8)
        ttk.Label(bar, text="使用本机 SSH 密钥；不保存密码。连接只检查环境并打开串口。", wraplength=240).grid(row=1, column=2, rowspan=4, sticky="nw", padx=8)
        self.port_box = self.network_inputs[0]
        self.refresh_btn = ttk.Button(bar)  # Shared state handling; intentionally not shown.
        for child in children:
            if isinstance(child, ttk.Label) and "USB-TTL" in str(child.cget("text")):
                child.configure(text="Radxa 沿用现有接线。请先退出占用 /dev/ttyS4 的视觉自动控制程序。\n断开网络仍保持最后输出；当前协议没有停止输出命令。")
        self.status.set("未连接 Radxa；点击连接不会改变输出")
        self.update_controls()

    def update_controls(self):
        super().update_controls()
        for entry in getattr(self, "network_inputs", []):
            entry.configure(state="disabled" if self.busy or self.link else "normal")

    def toggle_connection(self):
        if self.busy:
            return
        if self.link:
            return super().toggle_connection()
        settings = dict(host=self.host.get().strip(), username=self.username.get().strip(),
                        directory=self.directory.get().strip(), python=self.python_path.get().strip(),
                        serial_port=self.uart.get().strip())
        if not all(settings.values()):
            self.status.set("请填写所有网络连接参数")
            return
        self.status.set("正在连接 Radxa 并检查串口…")
        self.run_job("connected", lambda: RadxaWaveLink(**settings).open())


if __name__ == "__main__":
    root = tk.Tk()
    RadxaScopeApp(root)
    root.mainloop()
