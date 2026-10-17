#!/usr/bin/env python3
"""Render a fitted sine centreline and test the deployed geometric entry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "board_strategy"))

from lissajous_control._vendor.hybrid_frequency import (  # noqa: E402
    HybridConfig,
    fit_single_pair,
)
from lissajous_control._vendor.sweep_vision import estimate_sweep  # noqa: E402


def render(report: dict, thickness: int, supersample: int = 8) -> np.ndarray:
    fit = report["fit"]
    x0, y0, _, y1 = report["roi"]
    height = y1 - y0
    # Evaluate the analytical sine at every supersampled row. Merely scaling
    # 384 rounded vertices preserves their original staircase geometry.
    high_rows = np.arange(y0 * supersample, y1 * supersample, dtype=np.float64)
    position = (high_rows / supersample - y0) / (height - 1)
    columns = (
        x0 + fit["centre_x_roi_px"]
        + fit["amplitude_px"] * np.sin(
            2.0 * np.pi * fit["cycles"] * position + fit["phase_rad"]
        )
    )
    # Rasterizing directly at 384 px creates visible integer stair steps.
    # Preserve the analytical curve through 8x supersampling, then area-filter
    # it back to the model canvas. The result remains a one-pixel logical trace
    # after the deployed foreground threshold while retaining subpixel edges.
    points = np.column_stack((
        np.rint(columns * supersample).astype(np.int32),
        high_rows.astype(np.int32),
    )).reshape(-1, 1, 2)
    high = np.zeros((384 * supersample, 384 * supersample), np.uint8)
    cv2.polylines(
        high, [points], False, 255,
        max(1, thickness * round(supersample * 0.75)),
        cv2.LINE_AA,
    )
    return cv2.resize(high, (384, 384), interpolation=cv2.INTER_AREA)


def concise(result) -> dict:
    return {
        "valid": result.valid,
        "reason": result.reason,
        "frequency_hz": result.frequency,
        "uncertainty_hz": result.uncertainty_hz,
        "cycles": result.cycles,
        "confidence": result.confidence,
        "residual": result.residual,
        "density": result.density,
        "multiple_fraction": result.multiple_fraction,
        "coverage": result.coverage,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fit_report", type=Path)
    parser.add_argument("--window-us", type=float, required=True)
    parser.add_argument("--output", type=Path, default=Path("centerline_test"))
    args = parser.parse_args()
    source = json.loads(args.fit_report.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    config = HybridConfig().validate()
    results = {}
    for name, thickness in (("centerline_1px", 1), ("centerline_3px", 3)):
        image = render(source, thickness)
        cv2.imwrite(str(args.output / f"{name}.png"), image)
        geometry = estimate_sweep(image, args.window_us, 1000.0, 100000.0)
        pair = fit_single_pair([image, image.copy()], args.window_us, config)
        results[name] = {
            "geometry": concise(geometry),
            "hybrid_geometric_vector": {
                "valid": bool(pair[0] > 0.5),
                "frequency_hz": float(pair[1]),
                "uncertainty_hz": float(pair[2]),
                "residual": float(pair[3]),
                "cycles": float(pair[4]),
                "confidence": float(pair[5]),
            },
        }
    report = {
        "window_us": args.window_us,
        "source_fit_cycles": source["fit"]["cycles"],
        "tests": results,
    }
    (args.output / "report_concise.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
