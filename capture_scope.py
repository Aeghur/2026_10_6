#!/usr/bin/env python3
"""Capture one scope frame and optionally save a lens-corrected copy."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2

from vision_calibrator import LensCorrector


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument(
        "--lens-calibration", type=Path,
        default=ROOT / "artifacts" / "calibration" / "lens" /
                "lens_calibration_candidate.json",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "captures")
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    camera = cv2.VideoCapture(cfg["camera_device"], cv2.CAP_V4L2)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["frame_width"])
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["frame_height"])
    camera.set(cv2.CAP_PROP_FPS, cfg["fps"])
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not camera.isOpened():
        raise RuntimeError(f"cannot open {cfg['camera_device']}")
    frame = None
    try:
        for _ in range(12):
            ok, frame = camera.read()
            if not ok:
                raise RuntimeError("camera frame read failed")
    finally:
        camera.release()
    assert frame is not None
    args.output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = args.output / f"scope_{stamp}_raw.jpg"
    if not cv2.imwrite(str(raw_path), frame):
        raise RuntimeError(f"cannot write {raw_path}")
    print("RAW", raw_path)

    if args.lens_calibration.exists():
        lens_cfg = dict(cfg)
        lens_cfg["lens_calibration"] = str(args.lens_calibration.resolve())
        corrected = LensCorrector(lens_cfg).apply(frame)
        corrected_path = args.output / f"scope_{stamp}_undistorted.jpg"
        if not cv2.imwrite(str(corrected_path), corrected):
            raise RuntimeError(f"cannot write {corrected_path}")
        print("UNDISTORTED", corrected_path)
    else:
        print("Lens calibration not found; saved raw frame only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
