#!/usr/bin/env python3
"""Browser-based live preview and capture for fixed-camera lens calibration."""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "artifacts" / "calibration" / "lens" / "images"
POSES = (
    "向左移动", "向右移动", "向上移动", "向下移动",
    "左上角", "右上角", "左下角", "右下角",
    "左边向后倾斜", "右边向后倾斜", "上边向后倾斜", "下边向后倾斜",
    "靠近一些", "远离一些",
)
TARGET_CENTERS = (
    (0.27, 0.50), (0.73, 0.50), (0.50, 0.27), (0.50, 0.73),
    (0.30, 0.30), (0.70, 0.30), (0.30, 0.70), (0.70, 0.70),
    (0.50, 0.50), (0.50, 0.50), (0.50, 0.50), (0.50, 0.50),
    (0.50, 0.50), (0.50, 0.50),
)


class PreviewState:
    def __init__(self, output: Path, pattern: tuple[int, int]) -> None:
        self.output = output
        self.pattern = pattern
        self.detected_dir = output / "detected"
        self.rejected_dir = output / "rejected"
        self.output.mkdir(parents=True, exist_ok=True)
        self.detected_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.frame = None
        self.preview_jpeg: bytes | None = None
        self.found = False
        self.frame_time = 0.0
        existing = []
        for path in self.output.glob("lens_pose_*.jpg"):
            try:
                existing.append(int(path.name.split("_")[2]))
            except (IndexError, ValueError):
                pass
        self.session_saved = max(existing, default=0)
        self.message = "等待相机画面"
        self.running = True

    def status(self) -> dict:
        with self.lock:
            pose = POSES[min(self.session_saved, len(POSES) - 1)]
            return {
                "found": self.found,
                "fresh": time.monotonic() - self.frame_time < 1.5,
                "session_saved": self.session_saved,
                "target": len(POSES),
                "next_pose": pose if self.session_saved < len(POSES) else "已完成建议姿态",
                "message": self.message,
            }

    def capture(self) -> tuple[bool, str]:
        with self.lock:
            if self.frame is None or not self.found or time.monotonic() - self.frame_time >= 1.5:
                self.message = "未检测到完整角点，调整后再保存"
                return False, self.message
            frame = self.frame.copy()
            pose_index = self.session_saved
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCornersSB(
            gray, self.pattern,
            flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY,
        )
        if not found:
            with self.lock:
                self.message = "精确复检失败，请稳定纸板后重试"
            return False, self.message
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        pose = POSES[min(pose_index, len(POSES) - 1)]
        safe_pose = f"pose_{pose_index + 1:02d}"
        raw_path = self.output / f"lens_{safe_pose}_{stamp}.jpg"
        detected_path = self.detected_dir / f"lens_{safe_pose}_{stamp}_detected.jpg"
        marked = frame.copy()
        cv2.drawChessboardCorners(marked, self.pattern, corners, True)
        if not cv2.imwrite(str(raw_path), frame) or not cv2.imwrite(str(detected_path), marked):
            raise RuntimeError("failed to save calibration image")
        with self.lock:
            self.session_saved += 1
            self.message = f"已保存：{pose}"
        return True, str(raw_path)


def camera_loop(state: PreviewState, cfg: dict) -> None:
    camera = cv2.VideoCapture(cfg["camera_device"], cv2.CAP_V4L2)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["frame_width"])
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["frame_height"])
    camera.set(cv2.CAP_PROP_FPS, cfg["fps"])
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not camera.isOpened():
        state.message = f"无法打开 {cfg['camera_device']}"
        state.running = False
        return
    try:
        for _ in range(8):
            camera.read()
        while state.running:
            ok, frame = camera.read()
            if not ok:
                with state.lock:
                    state.message = "相机读取失败"
                time.sleep(0.1)
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCorners(
                gray, state.pattern,
                flags=cv2.CALIB_CB_ADAPTIVE_THRESH |
                      cv2.CALIB_CB_NORMALIZE_IMAGE |
                      cv2.CALIB_CB_FAST_CHECK,
            )
            preview = frame.copy()
            if found:
                corners = cv2.cornerSubPix(
                    gray, corners, (5, 5), (-1, -1),
                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
                )
                cv2.drawChessboardCorners(preview, state.pattern, corners, True)
                points = corners.reshape(-1, 2)
                current = points.mean(axis=0)
                x_span = float(points[:, 0].max() - points[:, 0].min())
                y_span = float(points[:, 1].max() - points[:, 1].min())
                with state.lock:
                    pose_index = min(state.session_saved, len(POSES) - 1)
                target_fraction = TARGET_CENTERS[pose_index]
                target = (
                    int(target_fraction[0] * frame.shape[1]),
                    int(target_fraction[1] * frame.shape[0]),
                )
                current_xy = tuple(current.astype(int))
                cv2.circle(preview, target, 48, (255, 180, 0), 3)
                cv2.line(preview, current_xy, target, (255, 180, 0), 3)
                cv2.circle(preview, current_xy, 9, (0, 255, 255), -1)
                if x_span > frame.shape[1] * 0.72 or y_span > frame.shape[0] * 0.68:
                    cv2.rectangle(preview, (8, 54), (650, 94), (0, 0, 0), -1)
                    cv2.putText(preview, "BOARD TOO LARGE - MOVE FARTHER",
                                (18, 83), cv2.FONT_HERSHEY_SIMPLEX,
                                0.72, (0, 165, 255), 2, cv2.LINE_AA)
            color = (0, 220, 0) if found else (0, 0, 255)
            text = "CORNERS OK - SPACE TO SAVE" if found else "MOVE BOARD - FULL 10x7 NOT FOUND"
            cv2.rectangle(preview, (8, 8), (590, 48), (0, 0, 0), -1)
            cv2.putText(preview, text, (18, 37), cv2.FONT_HERSHEY_SIMPLEX,
                        0.75, color, 2, cv2.LINE_AA)
            encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if encoded:
                with state.lock:
                    state.frame = frame
                    state.preview_jpeg = jpeg.tobytes()
                    state.found = bool(found)
                    state.frame_time = time.monotonic()
                    if found:
                        state.message = "角点完整，可以保存"
    finally:
        camera.release()


PAGE = """<!doctype html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>Radxa lens calibration</title><style>
body{font-family:sans-serif;background:#16181c;color:#eee;margin:0;text-align:center}
main{max-width:1100px;margin:auto;padding:14px}img{width:100%;background:#000;border-radius:8px}
.row{display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap;margin:12px}
button{font-size:20px;padding:12px 28px;border:0;border-radius:8px;background:#28a745;color:white}
#state{font-size:20px}.ok{color:#52db75}.bad{color:#ff6b6b}small{color:#bbb}</style></head>
<body><main><h2>镜头标定实时预览</h2><div id=state>连接中...</div>
<div class=row><b id=pose></b><button id=save>保存当前姿态（空格）</button></div>
<img src=/stream alt=preview><p><small>必须显示绿色角点后才能保存；摄像头保持固定，只移动纸板。</small></p>
<script>
async function update(){let s=await (await fetch('/status')).json();let e=document.querySelector('#state');
e.className=s.found&&s.fresh?'ok':'bad';e.textContent=s.message+'｜本次 '+s.session_saved+'/'+s.target;
document.querySelector('#pose').textContent='下一姿态：'+s.next_pose}
async function save(){let r=await fetch('/capture',{method:'POST'});let j=await r.json();alert(j.message);update()}
document.querySelector('#save').onclick=save;document.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();save()}});
setInterval(update,500);update();</script></main></body></html>""".encode("utf-8")


def handler_factory(state: PreviewState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def send_json(self, value, status=HTTPStatus.OK):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)
            elif self.path == "/status":
                self.send_json(state.status())
            elif self.path == "/stream":
                self.send_response(HTTPStatus.OK)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while state.running:
                        with state.lock:
                            jpeg = state.preview_jpeg
                        if jpeg is None:
                            time.sleep(0.05)
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if self.path != "/capture":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                ok, message = state.capture()
                self.send_json({"ok": ok, "message": message},
                               HTTPStatus.OK if ok else HTTPStatus.CONFLICT)
            except Exception as error:
                self.send_json({"ok": False, "message": str(error)},
                               HTTPStatus.INTERNAL_SERVER_ERROR)
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--columns", type=int, default=10)
    parser.add_argument("--rows", type=int, default=7)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    state = PreviewState(args.output, (args.columns, args.rows))
    producer = threading.Thread(target=camera_loop, args=(state, cfg), daemon=True)
    producer.start()
    server = ThreadingHTTPServer((args.host, args.port), handler_factory(state))
    print(f"Preview: http://192.168.1.198:{args.port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.running = False
        server.shutdown()
        server.server_close()
        producer.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
