#!/usr/bin/env python3
"""Create and review a fixed-camera oscilloscope graticule calibration.

The four supplied points are the corners of the complete graticule, ordered
TL, TR, BR, BL.  The program derives the centred square used by the vision
solver in division coordinates; the waveform is never used to fit geometry.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from vision_calibrator import LensCorrector


ROOT = Path(__file__).resolve().parent


def ordered_quad(values: list[float]) -> np.ndarray:
    points = np.asarray(values, dtype=np.float32).reshape(4, 2)
    cross = np.cross(points[1] - points[0], points[2] - points[1])
    if cross <= 0:
        raise ValueError("corners must be ordered TL TR BR BL")
    if cv2.contourArea(points) < 10_000:
        raise ValueError("calibration quadrilateral is too small")
    return points


def centred_region(
    full: np.ndarray, grid_columns: int, grid_rows: int, region_divs: int
) -> np.ndarray:
    if region_divs > min(grid_columns, grid_rows):
        raise ValueError("region-divs cannot exceed the graticule dimensions")
    inset_x = (grid_columns - region_divs) / (2.0 * grid_columns)
    inset_y = (grid_rows - region_divs) / (2.0 * grid_rows)
    tl, tr, br, bl = full
    top_left = tl + inset_x * (tr - tl)
    top_right = tr - inset_x * (tr - tl)
    bottom_left = bl + inset_x * (br - bl)
    bottom_right = br - inset_x * (br - bl)
    left_top = top_left + inset_y * (bottom_left - top_left)
    left_bottom = bottom_left - inset_y * (bottom_left - top_left)
    right_span = bottom_right - top_right
    right_top = top_right + inset_y * right_span
    right_bottom = bottom_right - inset_y * right_span
    return np.asarray([left_top, right_top, right_bottom, left_bottom], np.float32)


def draw_overlay(
    image: np.ndarray, full: np.ndarray, region: np.ndarray, region_divs: int
) -> np.ndarray:
    overlay = image.copy()
    cv2.polylines(overlay, [np.rint(full).astype(np.int32)], True, (0, 210, 255), 3)
    cv2.polylines(overlay, [np.rint(region).astype(np.int32)], True, (0, 255, 0), 3)
    transform = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [region_divs, 0], [region_divs, region_divs], [0, region_divs]]),
        region,
    )
    for index in range(region_divs + 1):
        vertical = np.float32([[[index, 0]], [[index, region_divs]]])
        horizontal = np.float32([[[0, index]], [[region_divs, index]]])
        for line in (vertical, horizontal):
            points = cv2.perspectiveTransform(line, transform).reshape(2, 2)
            cv2.line(overlay, tuple(np.rint(points[0]).astype(int)),
                     tuple(np.rint(points[1]).astype(int)), (0, 170, 0), 1)
    for label, point in zip(("TL", "TR", "BR", "BL"), full):
        xy = tuple(np.rint(point).astype(int))
        cv2.circle(overlay, xy, 6, (0, 210, 255), -1)
        cv2.putText(overlay, label, (xy[0] + 8, xy[1] + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 210, 255), 2)
    return overlay


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--full-grid-corners", type=float, nargs=8,
                        metavar=("TL_X", "TL_Y", "TR_X", "TR_Y",
                                 "BR_X", "BR_Y", "BL_X", "BL_Y"))
    parser.add_argument("--grid-columns", type=int, default=12)
    parser.add_argument("--grid-rows", type=int, default=8)
    parser.add_argument("--region-divs", type=int, default=8)
    parser.add_argument(
        "--output-prefix", type=Path,
        default=ROOT / "artifacts" / "calibration" / "scope" / "scope_calibration",
    )
    parser.add_argument("--write-config", action="store_true")
    args = parser.parse_args()

    image = cv2.imread(str(args.image))
    if image is None:
        parser.error(f"cannot read image: {args.image}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    image = LensCorrector(config).apply(image)
    raw_corners = args.full_grid_corners or config.get("full_grid_corners")
    if raw_corners is None:
        parser.error("provide --full-grid-corners or set full_grid_corners in config")
    full = ordered_quad(raw_corners)
    height, width = image.shape[:2]
    if np.any(full[:, 0] < 0) or np.any(full[:, 0] >= width) or \
       np.any(full[:, 1] < 0) or np.any(full[:, 1] >= height):
        parser.error(f"corners fall outside the {width}x{height} image")

    region = centred_region(full, args.grid_columns, args.grid_rows, args.region_divs)
    warp_size = int(config.get("warp_size", 640))
    target = np.float32([[0, 0], [warp_size - 1, 0],
                         [warp_size - 1, warp_size - 1], [0, warp_size - 1]])
    transform = cv2.getPerspectiveTransform(region, target)
    warped = cv2.warpPerspective(image, transform, (warp_size, warp_size))
    overlay = draw_overlay(image, full, region, args.region_divs)

    prefix = args.output_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    overlay_path = prefix.with_name(prefix.name + "_overlay.jpg")
    warped_path = prefix.with_name(prefix.name + "_warped.jpg")
    report_path = prefix.with_suffix(".json")
    if not cv2.imwrite(str(overlay_path), overlay) or not cv2.imwrite(str(warped_path), warped):
        raise RuntimeError("failed to write calibration images")
    report = {
        "source_image": str(args.image),
        "image_size": [width, height],
        "grid_divisions": [args.grid_columns, args.grid_rows],
        "region_divisions": [args.region_divs, args.region_divs],
        "full_grid_corners": full.tolist(),
        "grid_corners": region.tolist(),
        "pixels_per_div": warp_size / args.region_divs,
        "overlay": str(overlay_path),
        "warped": str(warped_path),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.write_config:
        config["full_grid_corners"] = [[round(float(x), 2), round(float(y), 2)] for x, y in full]
        config["grid_dimensions_div"] = [args.grid_columns, args.grid_rows]
        config["analysis_region_div"] = [args.region_divs, args.region_divs]
        config["grid_corners"] = [[round(float(x), 2), round(float(y), 2)] for x, y in region]
        args.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {args.config}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Review the green grid in", overlay_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
