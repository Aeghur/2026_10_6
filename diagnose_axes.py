#!/usr/bin/env python3
"""Validate axis-intersection frequency ratios against a known X frequency."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from vision_calibrator import Analyzer, Camera, build_signal_client


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--known-x-hz", type=float, required=True)
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument(
        "--multipliers", type=float, nargs="+", default=[1.0, 2.0, 3.0],
        help="Y probe frequencies as multiples of the known X frequency",
    )
    parser.add_argument("--phases", type=float, nargs="+", default=[23.0, 67.0])
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = ROOT / "artifacts" / "diagnostics" / f"frequency_validation_{stamp}"
    output.mkdir(parents=True, exist_ok=True)

    camera = Camera(cfg)
    analyzer = Analyzer(cfg)
    signal = build_signal_client(cfg)
    records: list[dict] = []
    try:
        index = 0
        for multiplier in args.multipliers:
            probe_hz = args.known_x_hz * multiplier
            for phase in args.phases:
                index += 1
                signal.set(probe_hz, phase)
                time.sleep(max(0.8, float(cfg["settle_seconds"])))
                frame = camera.frame()
                warped = analyzer.warp(frame)
                mask = analyzer.base_trace_mask(warped)
                counts = analyzer.axis_intersections(frame)
                record: dict = {
                    "probe_y_hz": probe_hz,
                    "phase_deg": phase,
                    "expected_x_hz": args.known_x_hz,
                    "expected_fx_over_fy": args.known_x_hz / probe_hz,
                    "axis_intersections": counts,
                    "passed": False,
                }
                if counts:
                    observed_ratio = counts["y_axis"] / counts["x_axis"]
                    estimated_x = probe_hz * observed_ratio
                    error_percent = 100.0 * abs(estimated_x - args.known_x_hz) / args.known_x_hz
                    record.update({
                        "observed_fx_over_fy": observed_ratio,
                        "estimated_x_hz": estimated_x,
                        "error_percent": error_percent,
                        "passed": error_percent <= 5.0,
                    })
                records.append(record)
                stem = f"{index:02d}_{probe_hz:g}Hz_{phase:g}deg"
                cv2.imwrite(str(output / f"{stem}_raw.jpg"), frame)
                cv2.imwrite(str(output / f"{stem}_warped.jpg"), warped)
                cv2.imwrite(str(output / f"{stem}_mask.png"), mask)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        try:
            signal.set(args.known_x_hz, 45.0)
        finally:
            signal.close()
            camera.close()

    valid = [item for item in records if item["axis_intersections"] is not None]
    report = {
        "known_x_hz": args.known_x_hz,
        "samples": len(records),
        "valid_samples": len(valid),
        "passed_samples": sum(bool(item["passed"]) for item in records),
        "passed": bool(records) and all(bool(item["passed"]) for item in records),
        "records": records,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("REPORT", json.dumps(report, ensure_ascii=False), flush=True)
    print(f"ARTIFACTS {output}", flush=True)
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
