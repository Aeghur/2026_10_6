#!/usr/bin/env python3
"""Estimate camera lens distortion from multiple checkerboard photographs."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
LENS_DIR = ROOT / "artifacts" / "calibration" / "lens"


def view_errors(objects, points, rotations, translations, matrix, distortion):
    errors = []
    for world, observed, rotation, translation in zip(
        objects, points, rotations, translations
    ):
        projected, _ = cv2.projectPoints(
            world, rotation, translation, matrix, distortion
        )
        delta = projected.reshape(-1, 2) - observed.reshape(-1, 2)
        errors.append(float(np.sqrt(np.mean(np.sum(delta * delta, axis=1)))))
    return np.asarray(errors)


def calibrate(paths, columns, rows, square_size):
    pattern = (columns, rows)
    world = np.zeros((columns * rows, 3), np.float32)
    world[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * square_size
    accepted = []
    image_size = None
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            print(f"SKIP unreadable: {path}")
            continue
        size = (image.shape[1], image.shape[0])
        if image_size is not None and size != image_size:
            print(f"SKIP resolution {size}, expected {image_size}: {path}")
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCornersSB(
            gray, pattern,
            flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY,
        )
        if not found:
            print(f"SKIP checkerboard not found: {path}")
            continue
        image_size = size
        accepted.append((path, image, corners.astype(np.float32)))
        print(f"FOUND {path}")
    if len(accepted) < 8 or image_size is None:
        raise RuntimeError(
            f"need at least 8 usable checkerboard images; found {len(accepted)}"
        )

    def run(indices):
        objects = [world.copy() for _ in indices]
        points = [accepted[index][2] for index in indices]
        rms, matrix, distortion, rotations, translations = cv2.calibrateCamera(
            objects, points, image_size, None, None, flags=cv2.CALIB_FIX_K3
        )
        errors = view_errors(
            objects, points, rotations, translations, matrix, distortion
        )
        return rms, matrix, distortion, errors

    indices = list(range(len(accepted)))
    # A flexible sheet can bend in a few hand-held poses. Refit after each
    # robust rejection pass so those views cannot distort the lens model.
    while True:
        trial = run(indices)
        median = float(np.median(trial[-1]))
        mad = float(np.median(np.abs(trial[-1] - median)))
        limit = max(0.75, median + 2.5 * max(mad, 0.03))
        kept = [index for index, error in zip(indices, trial[-1]) if error <= limit]
        if len(kept) < 8 or len(kept) == len(indices):
            break
        indices = kept
    rms, matrix, distortion, errors = run(indices)
    views = [accepted[index] for index in indices]
    report = {
        "model": "opencv_pinhole_k3_fixed",
        "image_size": list(image_size),
        "checkerboard_inner_corners": [columns, rows],
        "square_size": square_size,
        "camera_matrix": matrix.tolist(),
        "distortion_coefficients": distortion.reshape(-1).tolist(),
        "rms_reprojection_error_px": float(rms),
        "mean_view_error_px": float(np.mean(errors)),
        "max_view_error_px": float(np.max(errors)),
        "views": [
            {"path": str(view[0]), "error_px": float(error)}
            for view, error in zip(views, errors)
        ],
        "rejected_views": [
            str(accepted[index][0])
            for index in range(len(accepted)) if index not in indices
        ],
    }
    return report, views


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="paths or quoted glob patterns")
    parser.add_argument("--columns", type=int, default=10,
                        help="checkerboard inner-corner columns")
    parser.add_argument("--rows", type=int, default=7,
                        help="checkerboard inner-corner rows")
    parser.add_argument("--square-size", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=LENS_DIR / "lens_calibration.json")
    parser.add_argument("--preview", type=Path,
                        default=LENS_DIR / "lens_calibration_preview.jpg")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--write-config", action="store_true")
    args = parser.parse_args()
    if args.columns < 3 or args.rows < 3 or args.square_size <= 0:
        parser.error("invalid checkerboard dimensions")
    paths = sorted({Path(match) for expression in args.images
                    for match in glob.glob(expression)})
    if not paths:
        parser.error("no input images matched")

    report, views = calibrate(paths, args.columns, args.rows, args.square_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.preview.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    matrix = np.asarray(report["camera_matrix"], np.float64)
    distortion = np.asarray(report["distortion_coefficients"], np.float64)
    preview = cv2.undistort(views[0][1], matrix, distortion, None, matrix)
    if not cv2.imwrite(str(args.preview), preview):
        raise RuntimeError(f"cannot write preview: {args.preview}")
    if args.write_config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        try:
            name = str(args.output.resolve().relative_to(ROOT))
        except ValueError:
            name = str(args.output.resolve())
        config["lens_calibration"] = name
        args.config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Updated {args.config}")
    summary_keys = (
        "rms_reprojection_error_px", "mean_view_error_px",
        "max_view_error_px", "rejected_views",
    )
    print(json.dumps({key: report[key] for key in summary_keys},
                     ensure_ascii=False, indent=2))
    print("Calibration:", args.output)
    print("Preview:", args.preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
