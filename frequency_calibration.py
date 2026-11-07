"""Interactive frequency-error calibration for the free-running DDS output.

The operator supplies the input signal-source frequency, nudges the DDS output
until phase alignment is observed, and then keeps the confirmed pair in a CSV
table.  All user-facing frequency arithmetic uses Decimal so the protocol's
1 mHz resolution is never lost to binary floating-point accumulation.
"""
from __future__ import annotations

import argparse
import csv
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from tkinter import filedialog, ttk

from serial.tools import list_ports

from msp_wave_link import AMPLITUDE_CODES, MSPM0WaveLink


FREQUENCY_QUANTUM = Decimal("0.001")
MIN_FREQUENCY_HZ = Decimal("250.000")
MAX_FREQUENCY_HZ = Decimal("210000.000")
STEPS_HZ = ("1.000", "0.100", "0.010", "0.001")
CSV_FIELDS = (
    "confirmed_at",
    "source_frequency_hz",
    "calibrated_frequency_hz",
    "frequency_error_hz",
    "frequency_error_ppm",
    "phase_degrees",
    "amplitude",
)


def parse_frequency(value: str | Decimal) -> Decimal:
    """Parse, quantize and range-check a protocol frequency."""
    try:
        frequency = Decimal(str(value)).quantize(
            FREQUENCY_QUANTUM, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError) as error:
        raise ValueError("频率必须是有效数字") from error
    if not MIN_FREQUENCY_HZ <= frequency <= MAX_FREQUENCY_HZ:
        raise ValueError("频率必须位于 250.000～210000.000 Hz")
    return frequency


def stepped_frequency(current: Decimal, step: Decimal, direction: int) -> Decimal:
    """Return one exact 1 mHz-grid adjustment, rejecting out-of-range values."""
    if direction not in (-1, 1):
        raise ValueError("direction 必须是 -1 或 1")
    if step not in tuple(Decimal(item) for item in STEPS_HZ):
        raise ValueError("步进必须是 1、0.1、0.01 或 0.001 Hz")
    return parse_frequency(current + step * direction)


@dataclass(frozen=True)
class CalibrationRecord:
    confirmed_at: str
    source_frequency_hz: Decimal
    calibrated_frequency_hz: Decimal
    phase_degrees: Decimal
    amplitude: int

    @property
    def frequency_error_hz(self) -> Decimal:
        return self.calibrated_frequency_hz - self.source_frequency_hz

    @property
    def frequency_error_ppm(self) -> Decimal:
        return self.frequency_error_hz * Decimal(1_000_000) / self.source_frequency_hz

    def as_row(self) -> dict[str, str]:
        return {
            "confirmed_at": self.confirmed_at,
            "source_frequency_hz": f"{self.source_frequency_hz:.3f}",
            "calibrated_frequency_hz": f"{self.calibrated_frequency_hz:.3f}",
            "frequency_error_hz": f"{self.frequency_error_hz:+.3f}",
            "frequency_error_ppm": f"{self.frequency_error_ppm:+.6f}",
            "phase_degrees": f"{self.phase_degrees:.2f}",
            "amplitude": str(self.amplitude),
        }


def append_record(path: Path, record: CalibrationRecord) -> None:
    """Append one durable, Excel-readable UTF-8 CSV row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(record.as_row())


class FrequencyCalibrationApp:
    def __init__(self, root: tk.Tk, table_path: Path) -> None:
        self.root = root
        self.table_path = table_path.resolve()
        self.link: MSPM0WaveLink | None = None
        self.busy = False
        self.closing = False
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.records: list[CalibrationRecord] = []
        self.session_source: Decimal | None = None
        self.applied_frequency: Decimal | None = None
        self.applied_phase: Decimal | None = None
        self.applied_amplitude: int | None = None
        self.record_pending = False

        self.port = tk.StringVar()
        self.source_frequency = tk.StringVar(value="10000.000")
        self.output_frequency = tk.StringVar(value="10000.000")
        self.step = tk.StringVar(value="0.100")
        self.phase = tk.StringVar(value="0.00")
        self.amplitude = tk.StringVar(value="8")
        self.status = tk.StringVar(value="未连接；连接不会改变当前硬件输出")
        self.session = tk.StringVar(value="尚未开始标定")
        self.table_location = tk.StringVar(value=f"自动保存：{self.table_path}")

        root.title("微小频率误差标定")
        root.geometry("1080x760")
        root.minsize(900, 650)
        outer = ttk.Frame(root, padding=18)
        self.outer = outer
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer, text="微小频率误差标定", font=("Microsoft YaHei UI", 20, "bold")
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="人工观察定相 · 频率协议精度 0.001 Hz · 每次调整均等待 MSPM0 / FPGA 端到端确认",
        ).pack(anchor="w", pady=(4, 14))

        connection = ttk.Frame(outer)
        self.connection_bar = connection
        connection.pack(fill="x")
        ttk.Label(connection, text="串口").pack(side="left")
        self.port_box = ttk.Combobox(connection, textvariable=self.port, width=23)
        self.port_box.pack(side="left", padx=8)
        self.refresh_button = ttk.Button(connection, text="刷新", command=self.refresh_ports)
        self.refresh_button.pack(side="left")
        self.connect_button = ttk.Button(connection, text="连接", command=self.toggle_connection)
        self.connect_button.pack(side="left", padx=8)

        panel = ttk.LabelFrame(outer, text="标定参数", padding=14)
        self.parameters_panel = panel
        panel.pack(fill="x", pady=14)
        ttk.Label(panel, text="输入信号源频率 / Hz").grid(row=0, column=0, sticky="w", pady=6)
        self.source_entry = ttk.Entry(panel, textvariable=self.source_frequency, width=22)
        self.source_entry.grid(row=0, column=1, sticky="w", padx=10)
        ttk.Label(panel, text="作为本轮基准；精确到 0.001 Hz").grid(row=0, column=2, sticky="w")

        ttk.Label(panel, text="DDS 调整频率 / Hz").grid(row=1, column=0, sticky="w", pady=6)
        self.output_entry = ttk.Entry(
            panel, textvariable=self.output_frequency, width=22, state="readonly"
        )
        self.output_entry.grid(row=1, column=1, sticky="w", padx=10)
        ttk.Label(panel, textvariable=self.session).grid(row=1, column=2, sticky="w")

        ttk.Label(panel, text="单次步进 / Hz").grid(row=2, column=0, sticky="w", pady=6)
        self.step_box = ttk.Combobox(
            panel, textvariable=self.step, values=STEPS_HZ, state="readonly", width=19
        )
        self.step_box.grid(row=2, column=1, sticky="w", padx=10)
        nudge_bar = ttk.Frame(panel)
        nudge_bar.grid(row=2, column=2, sticky="w")
        self.minus_button = ttk.Button(nudge_bar, text="−  减小", command=lambda: self.nudge(-1))
        self.minus_button.pack(side="left")
        self.plus_button = ttk.Button(nudge_bar, text="＋  增大", command=lambda: self.nudge(1))
        self.plus_button.pack(side="left", padx=8)

        ttk.Label(panel, text="输出滞后相位 / °").grid(row=3, column=0, sticky="w", pady=6)
        self.phase_entry = ttk.Entry(panel, textvariable=self.phase, width=22)
        self.phase_entry.grid(row=3, column=1, sticky="w", padx=10)
        ttk.Label(panel, text="默认 0.00°；范围 0～359.99°").grid(row=3, column=2, sticky="w")

        ttk.Label(panel, text="输出幅度档位 / 8").grid(row=4, column=0, sticky="w", pady=6)
        self.amplitude_box = ttk.Combobox(
            panel, textvariable=self.amplitude, values=("2", "4", "6", "8"),
            state="readonly", width=19,
        )
        self.amplitude_box.grid(row=4, column=1, sticky="w", padx=10)

        action_bar = ttk.Frame(panel)
        action_bar.grid(row=5, column=1, columnspan=2, sticky="w", pady=(10, 4))
        self.start_button = ttk.Button(
            action_bar, text="从信号源频率开始", command=self.start_calibration
        )
        self.start_button.pack(side="left")
        self.keep_button = ttk.Button(
            action_bar, text="已定相：确认并保留", command=self.keep_record
        )
        self.keep_button.pack(side="left", padx=10)

        ttk.Label(outer, textvariable=self.status, wraplength=1030).pack(anchor="w")

        table_panel = ttk.LabelFrame(outer, text="本次已确认标定表", padding=10)
        table_panel.pack(fill="both", expand=True, pady=(12, 8))
        columns = ("time", "source", "calibrated", "error", "ppm", "phase", "amplitude")
        self.table = ttk.Treeview(table_panel, columns=columns, show="headings", height=10)
        headings = {
            "time": "确认时间", "source": "信号源 / Hz", "calibrated": "标定输出 / Hz",
            "error": "频差 / Hz", "ppm": "频差 / ppm", "phase": "相位 / °",
            "amplitude": "幅度档位",
        }
        widths = {"time": 160, "source": 130, "calibrated": 130, "error": 110,
                  "ppm": 110, "phase": 90, "amplitude": 90}
        for name in columns:
            self.table.heading(name, text=headings[name])
            self.table.column(name, width=widths[name], anchor="center")
        scrollbar = ttk.Scrollbar(table_panel, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.table_location, wraplength=850).pack(side="left")
        ttk.Button(footer, text="另存表格…", command=self.export_records).pack(side="right")

        self.refresh_ports()
        self.update_controls()
        root.after(50, self.process_events)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def refresh_ports(self) -> None:
        ports = [item.device for item in list_ports.comports()]
        self.port_box["values"] = ports
        if ports and not self.port.get():
            self.port.set(ports[0])

    def update_controls(self) -> None:
        idle = not self.busy
        connected = self.link is not None
        active = connected and self.applied_frequency is not None
        self.port_box.configure(state="normal" if idle and not connected else "disabled")
        self.refresh_button.configure(state="normal" if idle and not connected else "disabled")
        self.connect_button.configure(
            state="normal" if idle else "disabled", text="断开" if connected else "连接"
        )
        self.source_entry.configure(state="normal" if idle else "disabled")
        self.phase_entry.configure(state="normal" if idle else "disabled")
        self.step_box.configure(state="readonly" if idle else "disabled")
        self.amplitude_box.configure(state="readonly" if idle else "disabled")
        self.start_button.configure(state="normal" if idle and connected else "disabled")
        nudge_state = "normal" if idle and active else "disabled"
        self.minus_button.configure(state=nudge_state)
        self.plus_button.configure(state=nudge_state)
        self.keep_button.configure(
            state="normal" if idle and active and self.record_pending else "disabled"
        )

    def run_job(self, kind: str, operation) -> None:
        self.busy = True
        self.update_controls()

        def worker() -> None:
            try:
                self.events.put((kind, operation()))
            except Exception as error:  # surfaced in the GUI event loop
                self.events.put(("error", error))

        threading.Thread(target=worker, daemon=True).start()

    def toggle_connection(self) -> None:
        if self.busy:
            return
        if self.link is not None:
            link = self.link
            self.run_job("disconnected", link.close)
            return
        port = self.port.get().strip()
        if not port:
            self.status.set("请选择或填写串口，例如 COM18")
            return

        def connect() -> MSPM0WaveLink:
            link = MSPM0WaveLink(port, timeout=1.0)
            try:
                return link.open()
            except Exception:
                link.close()
                raise

        self.status.set("正在连接串口…")
        self.run_job("connected", connect)

    def read_output_settings(self) -> tuple[Decimal, int]:
        try:
            phase = Decimal(self.phase.get()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            amplitude = int(self.amplitude.get())
        except (InvalidOperation, ValueError) as error:
            raise ValueError("相位或幅度不是有效数字") from error
        if not Decimal("0.00") <= phase <= Decimal("359.99"):
            raise ValueError("相位必须位于 0～359.99°")
        if amplitude not in AMPLITUDE_CODES:
            raise ValueError("幅度必须是 2、4、6 或 8")
        return phase, amplitude

    def send_frequency(self, frequency: Decimal, source: Decimal | None = None) -> None:
        if self.link is None:
            return
        try:
            phase, amplitude = self.read_output_settings()
        except ValueError as error:
            self.status.set(f"参数无效：{error}")
            return
        link = self.link
        self.status.set(f"正在下发 {frequency:.3f} Hz 并等待 FPGA 确认…")

        def send():
            ack = link.set_waveform("sine", float(frequency), float(phase), amplitude)
            was_confirmed = getattr(link, "wait_for_confirmation", True)
            expected_phase = int(phase * 100)
            expected_amplitude = AMPLITUDE_CODES[amplitude]
            if (ack.phase_centidegrees != expected_phase or
                    ack.amplitude_code != expected_amplitude or
                    ack.mode & 0x60 != 0 or
                    abs(ack.frequency_millihz - int(frequency * 1000)) > 2):
                raise ValueError("确认参数与请求不一致，请检查固件与硬件链路")
            confirmed = Decimal(ack.frequency_millihz) / Decimal(1000)
            return confirmed, source, phase, amplitude, was_confirmed

        self.run_job("applied", send)

    def start_calibration(self) -> None:
        try:
            source = parse_frequency(self.source_frequency.get())
        except ValueError as error:
            self.status.set(f"参数无效：{error}")
            return
        self.source_frequency.set(f"{source:.3f}")
        self.send_frequency(source, source=source)

    def nudge(self, direction: int) -> None:
        if self.applied_frequency is None:
            return
        try:
            candidate = stepped_frequency(
                self.applied_frequency, Decimal(self.step.get()), direction
            )
        except ValueError as error:
            self.status.set(f"无法调整：{error}")
            return
        self.send_frequency(candidate)

    def keep_record(self) -> None:
        if (self.session_source is None or self.applied_frequency is None or
                self.applied_phase is None or self.applied_amplitude is None):
            self.status.set("请先开始标定并获得 FPGA 确认")
            return
        try:
            record = CalibrationRecord(
                confirmed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                source_frequency_hz=self.session_source,
                calibrated_frequency_hz=self.applied_frequency,
                phase_degrees=self.applied_phase,
                amplitude=self.applied_amplitude,
            )
            append_record(self.table_path, record)
        except (OSError, ValueError) as error:
            self.status.set(f"保存失败：{error}")
            return
        self.records.append(record)
        self.record_pending = False
        row = record.as_row()
        self.table.insert("", "end", values=(
            row["confirmed_at"], row["source_frequency_hz"],
            row["calibrated_frequency_hz"], row["frequency_error_hz"],
            row["frequency_error_ppm"], row["phase_degrees"], row["amplitude"],
        ))
        self.table.yview_moveto(1.0)
        self.status.set(
            f"已确认并保存：频差 {record.frequency_error_hz:+.3f} Hz，"
            f"{record.frequency_error_ppm:+.6f} ppm"
        )
        self.update_controls()

    def export_records(self) -> None:
        if not self.records:
            self.status.set("本次尚无已确认记录，暂时没有可另存的表格")
            return
        destination = filedialog.asksaveasfilename(
            title="另存标定表格", defaultextension=".csv",
            filetypes=(("CSV 表格", "*.csv"), ("所有文件", "*.*")),
            initialfile="frequency_calibration.csv",
        )
        if not destination:
            return
        path = Path(destination)
        try:
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(record.as_row() for record in self.records)
        except OSError as error:
            self.status.set(f"另存失败：{error}")
            return
        self.status.set(f"本次 {len(self.records)} 条记录已另存到 {path}")

    def process_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.busy = False
                if kind == "connected":
                    self.link = value  # type: ignore[assignment]
                    self.status.set(f"已连接 {self.link.port}；请输入信号源频率后开始标定")
                elif kind == "disconnected":
                    self.link = None
                    self.session_source = None
                    self.applied_frequency = None
                    self.applied_phase = None
                    self.applied_amplitude = None
                    self.record_pending = False
                    self.session.set("尚未开始标定")
                    self.status.set("已断开；硬件可能继续保持最后输出")
                elif kind == "applied":
                    confirmed, source, phase, amplitude, was_confirmed = value  # type: ignore[misc]
                    if source is not None:
                        self.session_source = source
                    self.applied_frequency = confirmed
                    self.applied_phase = phase
                    self.applied_amplitude = amplitude
                    self.record_pending = True
                    self.output_frequency.set(f"{confirmed:.3f}")
                    error = confirmed - self.session_source if self.session_source else Decimal(0)
                    self.session.set(
                        f"本轮基准 {self.session_source:.3f} Hz；当前频差 {error:+.3f} Hz"
                    )
                    delivery = "FPGA 已确认" if was_confirmed else "Radxa 串口已写入（未等 FPGA 回执）"
                    self.status.set(
                        f"{delivery}：正弦 {confirmed:.3f} Hz，"
                        f"相位 {phase:.2f}°，幅度 {amplitude}/8；请观察是否定相"
                    )
                else:
                    if self.link is not None:
                        try:
                            self.link.close()
                        except Exception:
                            pass
                    self.link = None
                    self.applied_frequency = None
                    self.applied_phase = None
                    self.applied_amplitude = None
                    self.record_pending = False
                    self.status.set(f"操作失败并已断开：{value}")
                self.update_controls()
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            if self.link is not None:
                self.link.close()
            self.root.destroy()
            return
        self.root.after(50, self.process_events)

    def close(self) -> None:
        self.closing = True
        if self.busy:
            self.status.set("正在等待当前串口操作结束后关闭…")
        else:
            if self.link is not None:
                self.link.close()
            self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="微小频率误差人工定相标定界面")
    parser.add_argument(
        "--table", type=Path, default=Path("frequency_calibration.csv"),
        help="确认记录自动追加的 CSV 文件（默认 frequency_calibration.csv）",
    )
    args = parser.parse_args()
    root = tk.Tk()
    FrequencyCalibrationApp(root, args.table)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
