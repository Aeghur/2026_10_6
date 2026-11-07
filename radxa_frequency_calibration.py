"""PC frequency calibration through SSH -> Radxa UART -> MSPM0 -> FPGA."""
from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from frequency_calibration import FrequencyCalibrationApp
from radxa_wave_link import RadxaWaveLink


class RadxaFrequencyCalibrationApp(FrequencyCalibrationApp):
    def refresh_ports(self) -> None:
        """The UART is selected on Radxa, not enumerated on the PC."""

    def __init__(self, root: tk.Tk, table_path: Path) -> None:
        super().__init__(root, table_path)
        root.title("Radxa 网络微小频率误差标定")
        root.geometry("1080x850")
        root.minsize(940, 760)

        self.connection_bar.pack_forget()
        self.host = tk.StringVar(value="192.168.137.2")
        self.username = tk.StringVar(value="radxa")
        self.directory = tk.StringVar(value="/home/radxa/vision_sorter")
        self.python_path = tk.StringVar(value="/home/radxa/vision_sorter/.venv/bin/python")
        self.uart = tk.StringVar(value="/dev/ttyS4")
        self.wait_for_confirmation = tk.BooleanVar(value=False)

        bar = ttk.LabelFrame(self.outer, text="Radxa 网络连接", padding=10)
        bar.pack(fill="x", pady=(10, 0), before=self.parameters_panel)
        self.network_inputs: list[ttk.Entry] = []
        fields = (
            ("地址", self.host),
            ("SSH 用户", self.username),
            ("程序目录", self.directory),
            ("Python 路径", self.python_path),
            ("Radxa 串口", self.uart),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(bar, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(bar, textvariable=variable, width=58)
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            self.network_inputs.append(entry)
        bar.columnconfigure(1, weight=1)
        self.connect_button = ttk.Button(bar, text="连接", command=self.toggle_connection)
        self.connect_button.grid(row=0, column=2, padx=8)
        ttk.Label(
            bar,
            text="使用本机 SSH 密钥；保持一个 SSH 和串口长连接。\n连接前请退出占用 /dev/ttyS4 的视觉程序。",
            wraplength=250,
        ).grid(row=1, column=2, rowspan=4, sticky="nw", padx=8)
        self.confirmation_button = ttk.Checkbutton(
            bar, text="等待 FPGA 数字回执（可选）",
            variable=self.wait_for_confirmation,
        )
        self.confirmation_button.grid(row=5, column=1, sticky="w", padx=8, pady=(5, 0))
        self.status.set("未连接 Radxa；连接不会改变当前硬件输出")
        self.update_controls()

    def update_controls(self) -> None:
        super().update_controls()
        state = "disabled" if self.busy or self.link is not None else "normal"
        for entry in getattr(self, "network_inputs", ()):
            entry.configure(state=state)
        if hasattr(self, "confirmation_button"):
            self.confirmation_button.configure(state=state)

    def toggle_connection(self) -> None:
        if self.busy:
            return
        if self.link is not None:
            super().toggle_connection()
            return
        settings = {
            "host": self.host.get().strip(),
            "username": self.username.get().strip(),
            "directory": self.directory.get().strip(),
            "python": self.python_path.get().strip(),
            "serial_port": self.uart.get().strip(),
            "wait_for_confirmation": self.wait_for_confirmation.get(),
        }
        if not all(settings.values()):
            self.status.set("请填写全部 Radxa 网络连接参数")
            return
        self.status.set("正在通过 SSH 连接 Radxa 并检查串口…")
        self.run_job("connected", lambda: RadxaWaveLink(**settings).open())


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 Radxa 接口进行微小频率误差标定")
    parser.add_argument(
        "--table", type=Path, default=Path("frequency_calibration.csv"),
        help="确认记录自动追加的 CSV 文件（默认 frequency_calibration.csv）",
    )
    args = parser.parse_args()
    root = tk.Tk()
    RadxaFrequencyCalibrationApp(root, args.table)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
