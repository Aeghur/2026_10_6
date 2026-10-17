#!/usr/bin/env python3
"""Capture checkerboard views from a fixed camera, one pose per Enter press."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parent
LENS_DIR = ROOT / "artifacts" / "calibration" / "lens"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--output", type=Path, default=LENS_DIR / "images")
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--columns", type=int, default=10,
                        help="checkerboard inner-corner columns")
    parser.add_argument("--rows", type=int, default=7,
                        help="checkerboard inner-corner rows")
    args = parser.parse_args()
    if args.count < 1 or args.columns < 3 or args.rows < 3:
        parser.error("count must be positive and checkerboard dimensions at least 3x3")

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    camera = cv2.VideoCapture(cfg["camera_device"], cv2.CAP_V4L2)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["frame_width"])
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["frame_height"])
    camera.set(cv2.CAP_PROP_FPS, cfg["fps"])
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not camera.isOpened():
        raise RuntimeError(f"cannot open {cfg['camera_device']}")
    args.output.mkdir(parents=True, exist_ok=True)
    rejected_dir = args.output / "rejected"
    detected_dir = args.output / "detected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    detected_dir.mkdir(parents=True, exist_ok=True)
    pattern = (args.columns, args.rows)
    saved = 0
    try:
        for _ in range(10):
            camera.read()
        while saved < args.count:
            answer = input(
                f"[{saved}/{args.count}] 摆好棋盘格后按回车采集，输入 q 结束: "
            ).strip().lower()
            if answer == "q":
                break
            frame = None
            for _ in range(6):
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("camera frame read failed")
            assert frame is not None
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCornersSB(
                gray, pattern,
                flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY,
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            if not found:
                rejected = rejected_dir / f"rejected_{timestamp}.jpg"
                cv2.imwrite(str(rejected), frame)
                print(f"未检测到完整 {args.columns}x{args.rows} 内角点，未计入；检查 {rejected}")
                continue
            image_path = args.output / f"lens_{saved + 1:02d}_{timestamp}.jpg"
            preview = frame.copy()
            cv2.drawChessboardCorners(preview, pattern, corners, True)
            preview_path = detected_dir / f"lens_{saved + 1:02d}_{timestamp}_detected.jpg"
            if not cv2.imwrite(str(image_path), frame) or not cv2.imwrite(str(preview_path), preview):
                raise RuntimeError("failed to save checkerboard image")
            saved += 1
            print(f"已保存 {image_path}")
    finally:
        camera.release()
    print(f"完成：{saved} 张有效照片，目录 {args.output}")
    return 0 if saved == args.count else 1


if __name__ == "__main__":
    raise SystemExit(main())
