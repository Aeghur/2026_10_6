#!/usr/bin/env python3
"""Capture raw and perspective-corrected frames for alignment inspection."""

import json
from pathlib import Path

import cv2

from vision_calibrator import Analyzer


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "artifacts" / "captures"
OUTPUT.mkdir(parents=True, exist_ok=True)


camera = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
if not camera.isOpened():
    raise RuntimeError("cannot open /dev/video0")
frame = None
for _ in range(12):
    ok, frame = camera.read()
    if not ok:
        raise RuntimeError("camera frame read failed")
camera.release()
if not cv2.imwrite(str(OUTPUT / "camera_check.jpg"), frame):
    raise RuntimeError("failed to save camera_check.jpg")

config = json.loads(Path("config.json").read_text(encoding="utf-8"))
warped = Analyzer(config).warp(frame)
if not cv2.imwrite(str(OUTPUT / "camera_check_warped.jpg"), warped):
    raise RuntimeError("failed to save camera_check_warped.jpg")
