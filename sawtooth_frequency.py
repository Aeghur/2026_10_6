#!/usr/bin/env python3
"""Measure X frequency by using the DAC sawtooth as a photographed timebase."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from msp_wave_link import MSPM0WaveLink
from vision_calibrator import Camera, LensCorrector, ROOT


class SawtoothAnalyzer:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.lens = LensCorrector(cfg)
        columns, rows = map(int, cfg.get("grid_dimensions_div", [12, 8]))
        pixels_per_div = int(cfg.get("sawtooth_pixels_per_div", 80))
        self.width = columns * pixels_per_div
        self.height = rows * pixels_per_div
        source = np.float32(cfg["full_grid_corners"])
        target = np.float32([
            [0, 0], [self.width - 1, 0],
            [self.width - 1, self.height - 1], [0, self.height - 1],
        ])
        self.transform = cv2.getPerspectiveTransform(source, target)
        self.value_min = int(cfg.get("white_value_min", 175))
        self.saturation_max = int(cfg.get("white_saturation_max", 80))

    def warp(self, frame: np.ndarray) -> np.ndarray:
        corrected = self.lens.apply(frame)
        return cv2.warpPerspective(
            corrected, self.transform, (self.width, self.height)
        )

    def mask(self, warped: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array([0, 0, self.value_min]),
            np.array([179, self.saturation_max, 255]),
        )
        return cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
        )

    def centerline(self, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        candidates = [
            label for label in range(1, count)
            if stats[label, cv2.CC_STAT_HEIGHT] >= self.height * 0.35
            and stats[label, cv2.CC_STAT_WIDTH] < self.width * 0.95
        ]
        if not candidates:
            raise RuntimeError("未找到贯穿Y方向的锯齿扫描轨迹")
        label = max(candidates, key=lambda item: stats[item, cv2.CC_STAT_AREA])
        component = labels == label
        ys: list[float] = []
        xs: list[float] = []
        for y in range(self.height):
            row = np.flatnonzero(component[y])
            if len(row) >= 4:
                ys.append(float(y))
                xs.append(float(np.median(row)))
        if len(ys) < self.height * 0.3:
            raise RuntimeError("有效锯齿中心线覆盖不足")
        overlay = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        for y, x in zip(ys, xs):
            overlay[int(y), int(round(x))] = (0, 0, 255)
        return np.asarray(ys), np.asarray(xs), overlay

    @staticmethod
    def _fit_at(cycles: float, t: np.ndarray, x: np.ndarray) -> tuple[float, np.ndarray]:
        design = np.column_stack([
            np.sin(2 * np.pi * cycles * t),
            np.cos(2 * np.pi * cycles * t),
            np.ones(len(t)), t,
        ])
        coefficients = np.linalg.lstsq(design, x, rcond=None)[0]
        rms = float(np.sqrt(np.mean(np.square(x - design @ coefficients))))
        return rms, coefficients

    def analyze(self, frame: np.ndarray) -> tuple[dict, dict[str, np.ndarray]]:
        warped = self.warp(frame)
        mask = self.mask(warped)
        ys, xs, overlay = self.centerline(mask)
        y_min, y_max = float(ys.min()), float(ys.max())
        t = (ys - y_min) / max(y_max - y_min, 1.0)

        uniform_t = np.linspace(0.0, 1.0, self.height)
        uniform_x = np.interp(uniform_t, t, xs)
        trend = np.polyval(np.polyfit(uniform_t, uniform_x, 1), uniform_t)
        spectrum = np.abs(np.fft.rfft((uniform_x - trend) * np.hanning(self.height), 8192))
        frequencies = np.fft.rfftfreq(8192, d=1.0 / self.height)
        usable = (frequencies >= 0.25) & (frequencies <= self.height / 8)
        seed = float(frequencies[np.flatnonzero(usable)[np.argmax(spectrum[usable])]])

        lower = max(0.25, seed - 1.5)
        upper = min(self.height / 8, seed + 1.5)
        best_rms = float("inf")
        best_cycles = seed
        best_coefficients = np.zeros(4)
        for cycles in np.linspace(lower, upper, 1501):
            rms, coefficients = self._fit_at(float(cycles), t, xs)
            if rms < best_rms:
                best_rms = rms
                best_cycles = float(cycles)
                best_coefficients = coefficients

        amplitude = float(np.hypot(best_coefficients[0], best_coefficients[1]))
        result = {
            "cycles_per_sweep": best_cycles,
            "fit_rms_px": best_rms,
            "trace_amplitude_px": amplitude,
            "trace_rows": len(ys),
            "trace_y_range": [y_min, y_max],
            "single_value_coverage": len(ys) / (y_max - y_min + 1.0),
        }
        result["locked"] = bool(
            result["single_value_coverage"] >= 0.8
            and best_rms <= float(self.cfg.get("sawtooth_lock_max_rms_px", 20.0))
            and amplitude <= self.width * 0.45
        )
        return result, {"warped": warped, "mask": mask, "centerline": overlay}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--saw-frequency", type=float, default=1000.0)
    parser.add_argument("--amplitude", type=int, choices=(2, 4, 6, 8), default=6)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument(
        "--known-x-hz", type=float,
        help="known X frequency; report the measured sawtooth timebase scale",
    )
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    analyzer = SawtoothAnalyzer(cfg)
    camera = Camera(cfg)
    link = MSPM0WaveLink(str(cfg.get("serial_port", "/dev/ttyS4"))).open()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = ROOT / "artifacts" / "diagnostics" / f"sawtooth_{stamp}"
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    errors: list[dict] = []
    try:
        ack = link.set_sawtooth(args.saw_frequency, 0.0, args.amplitude)
        time.sleep(max(0.8, float(cfg.get("settle_seconds", 0.4))))
        for index in range(args.samples):
            frame = camera.frame()
            cv2.imwrite(str(output / f"{index + 1:02d}_raw.jpg"), frame)
            try:
                result, images = analyzer.analyze(frame)
            except RuntimeError as error:
                errors.append({"sample": index + 1, "error": str(error)})
                continue
            result["sample"] = index + 1
            results.append(result)
            for name, image in images.items():
                cv2.imwrite(str(output / f"{index + 1:02d}_{name}.png"), image)
    finally:
        link.close()
        camera.close()

    locked_samples = [item for item in results if item["locked"]]
    locked = len(locked_samples) > len(results) // 2
    report = {
        "status": "locked" if locked else ("unlocked" if results else "error"),
        "waveform": "sawtooth",
        "commanded_saw_hz": args.saw_frequency,
        "ack_saw_hz": ack.frequency_millihz / 1000.0,
        "amplitude": args.amplitude,
        "locked": locked,
        "samples": results,
        "errors": errors,
    }
    if results:
        cycles = float(np.median([item["cycles_per_sweep"] for item in results]))
        report["median_cycles_per_sweep"] = cycles
    if locked:
        # With a free-running sawtooth, a thin repeatable curve means every
        # sweep starts at the same X phase. The fundamental lock is fx = fsaw.
        report["estimated_x_hz"] = args.saw_frequency
    if args.known_x_hz is not None:
        report["known_x_hz"] = args.known_x_hz
        if locked:
            report["frequency_error_hz"] = args.saw_frequency - args.known_x_hz
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"ARTIFACTS {output}")
    if not locked:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
