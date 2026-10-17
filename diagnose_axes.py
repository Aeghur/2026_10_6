#!/usr/bin/env python3
"""Validate real-scope lobe recognition against a known X frequency."""

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
    samples: list[tuple[int, float, float, float, object]] = []
    try:
        index = 0
        for multiplier in args.multipliers:
            probe_hz = args.known_x_hz * multiplier
            for phase in args.phases:
                index += 1
                signal.set(probe_hz, phase)
                time.sleep(max(0.8, float(cfg["settle_seconds"])))
                frame = camera.frame()
                stem = f"{index:02d}_{probe_hz:g}Hz_{phase:g}deg"
                cv2.imwrite(str(output / f"{stem}_raw.jpg"), frame)
                samples.append((index, multiplier, probe_hz, phase, frame))

        analyzer.set_background_frames([sample[4] for sample in samples])
        assert analyzer.background is not None
        cv2.imwrite(str(output / "synthetic_background.jpg"), analyzer.background)

        records: list[dict] = []
        for index, multiplier, probe_hz, phase, frame in samples:
            warped = analyzer.warp(frame)
            mask = analyzer.base_trace_mask(warped)
            record = {
                "probe_y_hz": probe_hz,
                "phase_deg": phase,
                "expected_lobes": int(round(multiplier)),
                "detected_lobes": analyzer.lobe_count(frame),
                "axis_intersections": analyzer.axis_intersections(frame),
            }
            records.append(record)
            stem = f"{index:02d}_{probe_hz:g}Hz_{phase:g}deg"
            cv2.imwrite(str(output / f"{stem}_warped.jpg"), warped)
            cv2.imwrite(str(output / f"{stem}_mask.png"), mask)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        try:
            signal.set(args.known_x_hz, 45.0)
        finally:
            signal.close()
            camera.close()

    groups = []
    for multiplier in args.multipliers:
        probe_hz = args.known_x_hz * multiplier
        detected = [
            item["detected_lobes"] for item in records
            if abs(item["probe_y_hz"] - probe_hz) < 0.1
        ]
        expected = int(round(multiplier))
        groups.append({
            "probe_y_hz": probe_hz,
            "expected_lobes": expected,
            "detected_lobes_by_phase": detected,
            # A clipped tangent can open a lobe at one phase; another phase
            # normally closes it, so retain the strongest observation.
            "detected_lobes": max(detected, default=0),
            "passed": max(detected, default=0) == expected,
        })
    report = {
        "known_x_hz": args.known_x_hz,
        "samples": len(records),
        "passed": bool(groups) and all(bool(item["passed"]) for item in groups),
        "groups": groups,
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
