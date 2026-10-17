#!/usr/bin/env python3
"""Run the collaborator's image-only frequency/phase controller on this board."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from msp_wave_link import MSPM0WaveLink
from vision_calibrator import Camera, LensCorrector, ROOT

STRATEGY_ROOT = ROOT / "board_strategy"
sys.path.insert(0, str(STRATEGY_ROOT))

from lissajous_control import (  # noqa: E402
    Capabilities,
    Controller,
    Frame,
    Signal,
    Target,
)


class StrategyImageAdapter:
    """Convert the calibrated 12x8 scope grid to the model's 10x10 canvas."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.lens = LensCorrector(cfg)
        columns, rows = map(int, cfg.get("grid_dimensions_div", [12, 8]))
        self.ppd = 80
        self.full_width, self.full_height = columns * self.ppd, rows * self.ppd
        source = np.float32(cfg["full_grid_corners"])
        target = np.float32([
            [0, 0], [self.full_width - 1, 0],
            [self.full_width - 1, self.full_height - 1], [0, self.full_height - 1],
        ])
        self.transform = cv2.getPerspectiveTransform(source, target)

    def convert(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        corrected = self.lens.apply(frame)
        warped = cv2.warpPerspective(
            corrected, self.transform, (self.full_width, self.full_height)
        )
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array([0, 0, int(self.cfg.get("white_value_min", 175))]),
            np.array([179, int(self.cfg.get("white_saturation_max", 80)), 255]),
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # Suppress the bright screen frame and menu glyphs. Real Lissajous or
        # multi-sweep components span the calibrated plot centre in at least
        # one axis; UI fragments near an edge do not.
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        cleaned = np.zeros_like(mask)
        cx, cy = self.full_width / 2.0, self.full_height / 2.0
        for label in range(1, count):
            x, y, width, height, area = map(int, stats[label])
            touches_edge = (
                x <= 3 or y <= 3
                or x + width >= self.full_width - 3
                or y + height >= self.full_height - 3
            )
            spans_centre = x <= cx <= x + width or y <= cy <= y + height
            if not touches_edge and spans_centre and area >= 16:
                cleaned[labels == label] = 255
        mask = cleaned

        # The camera calibration covers 12x8 div. The model canvas is 10x10
        # div, so crop one division from each horizontal edge and preserve the
        # missing vertical +/-4..5 div bands as black instead of stretching.
        x0 = (self.full_width - 10 * self.ppd) // 2
        central = mask[:, x0:x0 + 10 * self.ppd]
        visible_height = round(384 * 8 / 10)
        resized = cv2.resize(central, (384, visible_height), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((384, 384), np.uint8)
        y0 = (384 - visible_height) // 2
        canvas[y0:y0 + visible_height] = resized
        return canvas, warped


class BoardAdapter:
    def __init__(self, cfg: dict, output: Path) -> None:
        self.cfg = cfg
        self.output = output
        self.camera = Camera(cfg)
        self.images = StrategyImageAdapter(cfg)
        try:
            self.link = MSPM0WaveLink(
                str(cfg.get("serial_port", "/dev/ttyS4"))
            ).open()
        except BaseException:
            self.camera.close()
            raise
        self.signal = Signal("sine", 1000.0, 1.0, 0.0)
        self.sequence = 0

    @staticmethod
    def amplitude_code(vpp: float) -> int:
        code = int(round(vpp * 2.0))
        if code not in (2, 4, 6, 8) or abs(vpp - code / 2.0) > 0.02:
            raise RuntimeError(
                f"当前协议只能输出1/2/3/4 Vpp，策略请求{vpp:.3f} Vpp；"
                "需要连续幅度协议才能继续自动调幅"
            )
        return code

    def apply(self, signal: Signal) -> Signal:
        waveform = "sawtooth" if signal.waveform == "ramp" else "sine"
        if signal.frequency_hz > 110_000:
            raise RuntimeError(
                f"策略请求{signal.frequency_hz:.3f} Hz，当前协议上限为110000 Hz"
            )
        amplitude = self.amplitude_code(signal.vpp)
        # Strategy phase is positive lead; board protocol is positive lag.
        lag = (-signal.phase_deg) % 360.0
        self.link.set_waveform(waveform, signal.frequency_hz, lag, amplitude)
        self.signal = signal
        return signal

    def capture(self, after_s: float) -> tuple[np.ndarray, float, int]:
        delay = after_s - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        raw, captured_at = self.camera.frame_timed()
        self.sequence += 1
        clean, warped = self.images.convert(raw)
        stem = f"frame_{self.sequence:04d}"
        cv2.imwrite(str(self.output / f"{stem}_raw.jpg"), raw)
        cv2.imwrite(str(self.output / f"{stem}_warped.jpg"), warped)
        cv2.imwrite(str(self.output / f"{stem}_384.png"), clean)
        return clean, captured_at, self.sequence

    def select_exposure(self, candidates: list[int], target: int) -> dict:
        records = []
        for exposure in candidates:
            self.camera.set_exposure(exposure, int(self.cfg.get("manual_gain", 0)))
            time.sleep(0.12)
            raw = self.camera.frame()
            clean, _ = self.images.convert(raw)
            ys, xs = np.where(clean >= 128)
            pixels = int(len(xs))
            row_coverage = float(len(np.unique(ys)) / 384) if pixels else 0.0
            col_coverage = float(len(np.unique(xs)) / 384) if pixels else 0.0
            usable = pixels >= 300 and row_coverage >= 0.25 and col_coverage >= 0.25
            score = abs(np.log(max(exposure, 1) / target))
            if not usable:
                score += 100.0
            record = {
                "exposure_absolute": exposure,
                "foreground_pixels": pixels,
                "row_coverage": row_coverage,
                "column_coverage": col_coverage,
                "usable": usable,
                "score": float(score),
            }
            records.append(record)
            cv2.imwrite(str(self.output / f"exposure_{exposure}_raw.jpg"), raw)
            cv2.imwrite(str(self.output / f"exposure_{exposure}_384.png"), clean)
        chosen = min(records, key=lambda item: item["score"])
        if not chosen["usable"]:
            raise RuntimeError("所有候选曝光均未获得完整可用轨迹")
        self.camera.set_exposure(chosen["exposure_absolute"], int(self.cfg.get("manual_gain", 0)))
        report = {"mode": "startup_fixed_exposure_test", "target": target,
                  "chosen": chosen["exposure_absolute"], "candidates": records}
        (self.output / "exposure_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def close(self) -> None:
        self.link.close()
        self.camera.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--mode", choices=("diagonal", "circle", "infinity"), default="circle")
    parser.add_argument("--height-div", type=int, choices=(2, 4, 6, 8), default=8)
    parser.add_argument("--skip-exposure-test", action="store_true")
    parser.add_argument(
        "--exposure-test-only", action="store_true",
        help="save startup exposure candidates and stop before strategy control",
    )
    args = parser.parse_args()
    if args.exposure_test_only and args.skip_exposure_test:
        parser.error("--exposure-test-only cannot be combined with --skip-exposure-test")

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = ROOT / "artifacts" / "diagnostics" / f"strategy_{stamp}"
    output.mkdir(parents=True, exist_ok=True)
    adapter = BoardAdapter(cfg, output)
    decisions = []
    try:
        if not args.skip_exposure_test:
            probe = Signal("ramp", 1e6 / (175.0 * 1.05), 4.0, 0.0, 175.0, 0.05)
            adapter.apply(probe)
            exposure = adapter.select_exposure(
                list(map(int, cfg.get("strategy_exposure_candidates", [60, 80, 100, 140, 200, 260, 313]))),
                int(cfg.get("strategy_target_exposure", 80)),
            )
            print("EXPOSURE", json.dumps(exposure, ensure_ascii=False), flush=True)
            if args.exposure_test_only:
                print(f"ARTIFACTS {output}")
                return

        controller = Controller(
            calibration_id=f"fixed-camera-{stamp}",
            capabilities=Capabilities(
                ramp_supported=True, continuous_phase=True,
                max_frequency_hz=110_000.0, max_vpp=4.0,
            ),
        )
        decision = controller.start(Target(args.mode, args.height_div), adapter.signal)
        while decision.status not in ("done", "failed", "stopped"):
            decisions.append(decision.to_dict())
            print("DECISION", json.dumps(decision.to_dict(), ensure_ascii=False), flush=True)
            if decision.status == "apply":
                actual = adapter.apply(decision.command.signal)
                applied_at = time.monotonic()
                controller.applied(decision.command.command_id, actual, applied_at)
                capture_after = controller.capture_after
            elif decision.status == "capture":
                capture_after = decision.capture_after_s or time.monotonic()
            else:
                raise RuntimeError(f"unexpected controller status: {decision.status}")
            image, captured_at, frame_id = adapter.capture(capture_after)
            decision = controller.step(Frame(
                image=image,
                signal=adapter.signal,
                command_id=controller.command.command_id,
                frame_id=frame_id,
                captured_at_s=captured_at,
                calibration_id=controller.calibration_id,
            ))
        decisions.append(decision.to_dict())
        result = decision.to_dict()
        (output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output / "decisions.json").write_text(
            json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"ARTIFACTS {output}")
        if not decision.success:
            raise SystemExit(2)
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
