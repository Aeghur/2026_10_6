#!/usr/bin/env python3
"""Camera-only Lissajous frequency and phase matcher.

The program never reads X-channel state.  It observes camera frames and can
drive either the original HTTP simulator or the real MSPM0/FPGA UART link.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
DIAGNOSTICS_DIR = ARTIFACTS / "diagnostics"
RESULTS_DIR = ARTIFACTS / "results"


@dataclass
class Measurement:
    score: float
    linearity: float
    residual: float
    angle_deg: float
    pixels: int


class LensCorrector:
    """Apply OpenCV radial/tangential lens correction before perspective warp."""

    def __init__(self, cfg: dict) -> None:
        calibration_name = cfg.get("lens_calibration")
        self.enabled = bool(calibration_name)
        self._maps: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        if not self.enabled:
            return
        calibration_path = Path(str(calibration_name))
        if not calibration_path.is_absolute():
            calibration_path = ROOT / calibration_path
        record = json.loads(calibration_path.read_text(encoding="utf-8"))
        self.reference_size = tuple(map(int, record["image_size"]))
        self.camera_matrix = np.asarray(record["camera_matrix"], dtype=np.float64)
        self.distortion = np.asarray(
            record["distortion_coefficients"], dtype=np.float64
        )
        if self.camera_matrix.shape != (3, 3) or self.distortion.size < 4:
            raise ValueError(f"invalid lens calibration: {calibration_path}")

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return frame
        height, width = frame.shape[:2]
        key = (width, height)
        if key not in self._maps:
            reference_width, reference_height = self.reference_size
            matrix = self.camera_matrix.copy()
            matrix[0, :] *= width / reference_width
            matrix[1, :] *= height / reference_height
            self._maps[key] = cv2.initUndistortRectifyMap(
                matrix, self.distortion, None, matrix, key, cv2.CV_32FC1
            )
        map_x, map_y = self._maps[key]
        return cv2.remap(
            frame, map_x, map_y, cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )


class Camera:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.set_exposure(
            int(cfg.get("manual_exposure", 313)),
            int(cfg.get("manual_gain", 0)),
        )
        self.cap = cv2.VideoCapture(cfg["camera_device"], cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["frame_width"])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["frame_height"])
        self.cap.set(cv2.CAP_PROP_FPS, cfg["fps"])
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open {cfg['camera_device']}")
        for _ in range(8):
            self.cap.read()

    def set_exposure(self, exposure: int, gain: int | None = None) -> None:
        """Select a fixed V4L2 exposure; auto_exposure=1 is manual mode."""
        controls = [
            "auto_exposure=1",
            f"exposure_time_absolute={int(exposure)}",
        ]
        if gain is not None:
            controls.append(f"gain={int(gain)}")
        controls.extend([
            "white_balance_automatic=0",
            "white_balance_temperature=4000",
        ])
        subprocess.run(
            [
                "v4l2-ctl", "-d", self.cfg["camera_device"],
                "--set-ctrl=" + ",".join(controls),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.current_exposure = int(exposure)

    def frame(self) -> np.ndarray:
        image, _ = self.frame_timed()
        return image

    def frame_timed(self) -> tuple[np.ndarray, float]:
        """Return a fresh frame and an estimated exposure midpoint."""
        # Flush buffered frames so every observation follows the latest command.
        for _ in range(4):
            ok, _ = self.cap.read()
            if not ok:
                raise RuntimeError("camera frame read failed")
        ok, image = self.cap.read()
        finished = time.monotonic()
        if not ok:
            raise RuntimeError("camera frame read failed")
        # UVC exposure_time_absolute is expressed in 100 us units. OpenCV does
        # not expose the kernel buffer timestamp, so this is the closest
        # available midpoint after explicitly draining old frames.
        captured_at = finished - self.current_exposure * 0.00005
        return image, captured_at

    def close(self) -> None:
        self.cap.release()


class Analyzer:
    def __init__(self, cfg: dict) -> None:
        size = cfg["warp_size"]
        source = np.float32(cfg["grid_corners"])
        target = np.float32([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]])
        self.size = size
        self.min_trace_pixels = int(cfg.get("min_trace_pixels", 500))
        self.trace_color = str(cfg.get("trace_color", "green")).lower()
        self.white_saturation_max = int(cfg.get("white_saturation_max", 80))
        self.white_value_min = int(cfg.get("white_value_min", 175))
        self.lens = LensCorrector(cfg)
        self.transform = cv2.getPerspectiveTransform(source, target)
        self.background: np.ndarray | None = None

    def warp(self, frame: np.ndarray) -> np.ndarray:
        corrected = self.lens.apply(frame)
        return cv2.warpPerspective(corrected, self.transform, (self.size, self.size))

    def set_background(self, frame: np.ndarray) -> None:
        self.background = self.warp(frame)

    def set_background_frames(self, frames: list[np.ndarray]) -> None:
        """Build a static-screen reference when the real DAC cannot be muted.

        The trace moves between controlled captures while the graticule and
        reflections remain fixed.  A low per-pixel percentile therefore keeps
        the screen background and rejects the bright moving trace.
        """
        if len(frames) < 3:
            raise ValueError("at least three frames are required for a synthetic background")
        warped = np.stack([self.warp(frame) for frame in frames])
        self.background = np.percentile(warped, 10, axis=0).astype(np.uint8)

    def base_trace_mask(self, warped: np.ndarray) -> np.ndarray:
        """Return the optical trace without deleting the two centre axes."""
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        if self.trace_color == "white":
            optical_trace = cv2.inRange(
                hsv,
                np.array([0, 0, self.white_value_min]),
                np.array([179, self.white_saturation_max, 255]),
            )
        elif self.trace_color == "green":
            optical_trace = cv2.inRange(
                hsv, np.array([35, 35, 55]), np.array([105, 255, 255])
            )
        else:
            raise ValueError(f"unsupported trace_color: {self.trace_color}")
        if self.background is not None:
            # A zero-amplitude optical reference removes the grid, axes, screen
            # curvature and room reflections without reading simulator state.
            difference = cv2.absdiff(warped, self.background)
            delta = np.max(difference, axis=2).astype(np.uint8)
            changed = cv2.threshold(delta, 24, 255, cv2.THRESH_BINARY)[1]
            mask = cv2.bitwise_and(optical_trace, changed)
        else:
            mask = optical_trace
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        return mask

    def trace_mask(self, warped: np.ndarray) -> np.ndarray:
        mask = self.base_trace_mask(warped)
        center = self.size // 2
        axis_band = max(7, self.size // 80)
        mask[:, center - axis_band:center + axis_band + 1] = 0
        mask[center - axis_band:center + axis_band + 1, :] = 0

        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        clean = np.zeros_like(mask)
        for label in range(1, count):
            x, y, width, height, area = stats[label]
            if area < 12 or width < 3 or height < 3:
                continue
            # Reject residual thin horizontal/vertical illumination changes.
            if width > 8 * height or height > 8 * width:
                continue
            clean[labels == label] = 255
        return clean

    def lobe_geometry(self, frame: np.ndarray) -> dict:
        """Count closed lobes and report whether they spread along X or Y."""
        mask = self.base_trace_mask(self.warp(frame))
        padded = cv2.copyMakeBorder(mask, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=0)
        contours, hierarchy = cv2.findContours(
            padded, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )
        if hierarchy is None:
            return {"count": 0, "orientation": None}
        minimum_area = self.size * self.size * 0.001
        holes = [
            contour for index, contour in enumerate(contours)
            if hierarchy[0, index, 3] >= 0
            and cv2.contourArea(contour) >= minimum_area
        ]
        if len(holes) <= 1:
            return {"count": len(holes), "orientation": "equal" if holes else None}
        centers = []
        for contour in holes:
            moments = cv2.moments(contour)
            centers.append((
                moments["m10"] / moments["m00"],
                moments["m01"] / moments["m00"],
            ))
        spread_x = float(np.ptp([center[0] for center in centers]))
        spread_y = float(np.ptp([center[1] for center in centers]))
        return {
            "count": len(holes),
            "orientation": "horizontal" if spread_x >= spread_y else "vertical",
            "spread": [spread_x, spread_y],
        }

    def lobe_count(self, frame: np.ndarray) -> int:
        return int(self.lobe_geometry(frame)["count"])

    def measure(self, frame: np.ndarray, debug_prefix: Path | None = None) -> Measurement:
        warped = self.warp(frame)
        mask = self.trace_mask(warped)
        ys, xs = np.nonzero(mask)
        if len(xs) < self.min_trace_pixels:
            return Measurement(10.0, 1.0, 1.0, 0.0, len(xs))

        points = np.column_stack((xs, ys)).astype(np.float64)
        center = np.median(points, axis=0)
        centered = points - center
        covariance = np.cov(centered, rowvar=False)
        values, vectors = np.linalg.eigh(covariance)
        major = vectors[:, 1]
        linearity = float(values[0] / max(values[1], 1e-9))
        distances = np.abs(centered[:, 0] * major[1] - centered[:, 1] * major[0])
        residual = float(np.median(distances) / self.size)
        angle = float(math.degrees(math.atan2(-major[1], major[0])) % 180)
        # Residual is robust to a few surviving grid pixels; PCA penalizes loops.
        # A same-phase signal rises toward the right in voltage coordinates
        # (about 45 degrees after square-grid rectification). A 135-degree line
        # is frequency-matched but 180 degrees out of phase, so penalize it.
        angle_error = abs(angle - 45.0)
        angle_error = min(angle_error, 180.0 - angle_error)
        orientation_penalty = 0.12 * angle_error / 90.0
        score = residual + 0.18 * math.sqrt(max(linearity, 0.0)) + orientation_penalty

        if debug_prefix is not None:
            debug_prefix.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_prefix.with_suffix(".jpg")), warped)
            cv2.imwrite(str(debug_prefix.with_name(debug_prefix.name + "_mask").with_suffix(".png")), mask)
        return Measurement(score, linearity, residual, angle, len(xs))

    @staticmethod
    def _run_count(present: np.ndarray, minimum_width: int = 2, merge_gap: int = 4) -> int:
        """Count separated occupied runs, bridging camera/antialiasing gaps."""
        present = present.astype(bool)
        false_runs = np.flatnonzero(~present)
        for index in false_runs:
            left = max(0, index - merge_gap)
            right = min(len(present), index + merge_gap + 1)
            if present[left:index].any() and present[index + 1:right].any():
                present[index] = True
        starts = np.flatnonzero(present & ~np.r_[False, present[:-1]])
        ends = np.flatnonzero(present & ~np.r_[present[1:], False])
        return sum(1 for start, end in zip(starts, ends) if end - start + 1 >= minimum_width)

    def _axis_count(
        self, mask: np.ndarray, horizontal: bool, half_width: int, offset: int = 0
    ) -> int:
        center = self.size // 2 + offset
        if horizontal:
            strip = mask[center - half_width:center + half_width + 1, :]
            projection = np.count_nonzero(strip, axis=0)
        else:
            strip = mask[:, center - half_width:center + half_width + 1]
            projection = np.count_nonzero(strip, axis=1)
        # A real crossing occupies several pixels through the strip. Requiring
        # two pixels suppresses isolated sensor noise while retaining oblique lines.
        return self._run_count(projection >= 2)

    def _center_branch_count(self, mask: np.ndarray) -> int:
        """Recover coincident centre crossings by counting exits from a small disc."""
        center = self.size // 2
        radii = (14, 18, 22)
        estimates: list[int] = []
        angles = np.linspace(0.0, 2.0 * math.pi, 720, endpoint=False)
        for radius in radii:
            occupied = np.zeros(len(angles), dtype=bool)
            # Sample a 5-pixel-thick annulus so broken/antialiased strokes survive.
            for radial_offset in range(-2, 3):
                sample_radius = radius + radial_offset
                xs = np.rint(center + sample_radius * np.cos(angles)).astype(int)
                ys = np.rint(center + sample_radius * np.sin(angles)).astype(int)
                occupied |= mask[ys, xs] > 0
            # Circular closing bridges small camera gaps, including at 0/2pi.
            doubled = np.r_[occupied, occupied]
            kernel = np.ones(9, dtype=np.uint8)
            closed = np.convolve(doubled.astype(np.uint8), kernel, mode="same") > 0
            ring = closed[len(occupied) // 2:len(occupied) // 2 + len(occupied)]
            transitions = int(np.count_nonzero(ring != np.roll(ring, 1)))
            exits = transitions // 2
            if exits >= 2 and exits % 2 == 0:
                estimates.append(exits // 2)
        if not estimates:
            return 0
        return int(round(float(np.median(estimates))))

    def axis_intersections(self, frame: np.ndarray) -> dict | None:
        """Count distinct trace intersections with the central X and Y axes.

        Three strip widths are independent measurements of the same geometry.
        Their median is used only when at least two agree closely.
        """
        mask = self.base_trace_mask(self.warp(frame))
        pixels = int(np.count_nonzero(mask))
        ys, xs = np.nonzero(mask)
        if pixels < self.min_trace_pixels:
            return None
        span_x = int(xs.max() - xs.min())
        span_y = int(ys.max() - ys.min())
        # Wrong/covered window and failed background subtraction must stop the
        # run instead of silently triggering a broad frequency search.
        if span_x < self.size * 0.18 or span_y < self.size * 0.18 or pixels > self.size * self.size * 0.18:
            return None

        widths = (3, 5, 8)
        center = self.size // 2
        center_present = bool(np.count_nonzero(
            mask[center - 4:center + 5, center - 4:center + 5]
        ))
        center_branches = self._center_branch_count(mask) if center_present else 0

        def corrected_axis_count(horizontal: bool, width: int) -> int:
            raw = self._axis_count(mask, horizontal, width)
            if not center_present or center_branches <= 1:
                return raw
            # The projection merges every centre crossing into one run. Replace
            # that run by the number of independently observed centre strands.
            return raw + center_branches - 1

        x_axis = [corrected_axis_count(True, width) for width in widths]
        y_axis = [corrected_axis_count(False, width) for width in widths]

        def consensus(values: list[int]) -> int | None:
            positive = [value for value in values if value > 0]
            if len(positive) < 2:
                return None
            median = int(round(float(np.median(positive))))
            agreeing = [value for value in positive if abs(value - median) <= 1]
            return int(round(float(np.median(agreeing)))) if len(agreeing) >= 2 else None

        # Nearby parallel cuts normally preserve the count but separate a centre
        # coincidence. They are retained as a second, independent diagnostic.
        offset = max(4, self.size // 100)
        x_offset = [self._axis_count(mask, True, 3, sign * distance)
                    for distance in (offset, offset * 2) for sign in (-1, 1)]
        y_offset = [self._axis_count(mask, False, 3, sign * distance)
                    for distance in (offset, offset * 2) for sign in (-1, 1)]

        # The zero-amplitude optical reference is itself a horizontal centre
        # trace. Its subtraction can fragment the exact X-axis projection, so
        # use nearby parallel cuts to separate coincident centre crossings.
        nx = consensus(x_axis)
        ny = consensus(y_axis)
        offset_nx = consensus(x_offset)
        if offset_nx is not None:
            nx = offset_nx
        if nx is None or ny is None:
            return None
        return {
            "x_axis_methods": x_axis,
            "y_axis_methods": y_axis,
            "x_axis": nx,
            "y_axis": ny,
            "center_present": center_present,
            "center_branches": center_branches,
            "x_offset_methods": x_offset,
            "y_offset_methods": y_offset,
            "pixels": pixels,
            "span": [span_x, span_y],
        }


class HttpSignalClient:
    def __init__(self, url: str, amplitude: float) -> None:
        self.url = url
        self.amplitude = amplitude

    def set(self, frequency: float, phase: float, amplitude: float | None = None) -> None:
        body = json.dumps({
            "frequency": float(frequency),
            "phase": float(((phase + 180) % 360) - 180),
            "amplitude": float(self.amplitude if amplitude is None else amplitude),
        }).encode()
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError(f"signal endpoint returned HTTP {response.status}")

    @property
    def supports_zero_amplitude(self) -> bool:
        return True

    def close(self) -> None:
        pass


class UARTSignalClient:
    """Real DAC controller through Radxa UART -> MSPM0 -> FPGA."""

    def __init__(self, port: str, amplitude_level: int) -> None:
        from msp_wave_link import MSPM0WaveLink

        self.amplitude_level = int(amplitude_level)
        self.link = MSPM0WaveLink(port).open()

    @property
    def supports_zero_amplitude(self) -> bool:
        return False

    def set(
        self, frequency: float, phase: float, amplitude: float | None = None
    ) -> None:
        level = self.amplitude_level if amplitude is None else int(amplitude)
        if level not in (2, 4, 6, 8):
            raise ValueError("real DAC amplitude must be one of 2, 4, 6 or 8")
        self.link.set_target(
            frequency_hz=float(frequency),
            phase_degrees=float(phase % 360.0),
            amplitude=level,
        )

    def close(self) -> None:
        self.link.close()


def build_signal_client(cfg: dict) -> HttpSignalClient | UARTSignalClient:
    backend = str(cfg.get("control_backend", "http")).lower()
    if backend == "http":
        return HttpSignalClient(cfg["signal_url"], cfg["amplitude_vpp"])
    if backend == "uart":
        return UARTSignalClient(
            str(cfg.get("serial_port", "/dev/ttyS4")),
            int(cfg.get("amplitude_level", 8)),
        )
    raise ValueError(f"unsupported control_backend: {backend}")


class Calibrator:
    def __init__(
        self, cfg: dict, camera: Camera, analyzer: Analyzer,
        signal: HttpSignalClient | UARTSignalClient,
    ) -> None:
        self.cfg = cfg
        self.camera = camera
        self.analyzer = analyzer
        self.signal = signal
        self.measurements = 0

    def capture_background(self) -> None:
        if not bool(self.cfg.get("capture_background", True)):
            print("Background capture disabled; using optical trace threshold.", flush=True)
            return
        if self.signal.supports_zero_amplitude:
            print("Capturing optical zero-amplitude background...", flush=True)
            self.signal.set(self.cfg["frequency_min_hz"], 0, amplitude=0)
            time.sleep(max(0.8, self.cfg["settle_seconds"] * 2))
            self.analyzer.set_background(self.camera.frame())
            return

        print("Capturing moving-trace references for static-grid removal...", flush=True)
        minimum = float(self.cfg["frequency_min_hz"])
        maximum = float(self.cfg["frequency_max_hz"])
        middle = math.sqrt(minimum * maximum)
        frames: list[np.ndarray] = []
        for frequency, phase in (
            (minimum, 17.0), (minimum, 71.0),
            (middle, 29.0), (middle, 83.0),
            (maximum, 41.0), (maximum, 107.0),
        ):
            self.signal.set(frequency, phase)
            time.sleep(max(0.8, float(self.cfg["settle_seconds"])))
            frames.append(self.camera.frame())
            self.measurements += 1
        self.analyzer.set_background_frames(frames)
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        assert self.analyzer.background is not None
        cv2.imwrite(
            str(DIAGNOSTICS_DIR / "synthetic_background.jpg"),
            self.analyzer.background,
        )

    def evaluate(self, frequency: float, phase: float) -> Measurement:
        self.signal.set(frequency, phase)
        time.sleep(self.cfg["settle_seconds"])
        result = self.analyzer.measure(self.camera.frame())
        self.measurements += 1
        print(
            f"#{self.measurements:04d} f={frequency:9.2f}Hz phase={phase:7.2f}deg "
            f"score={result.score:.6f} linearity={result.linearity:.6f} "
            f"residual={result.residual:.6f} pixels={result.pixels}",
            flush=True,
        )
        return result

    def best_phase(self, frequency: float, phases: list[float]) -> tuple[float, Measurement]:
        candidates = [(phase, self.evaluate(frequency, phase)) for phase in phases]
        return min(candidates, key=lambda item: item[1].score)

    def estimate_frequency_from_lobes(self) -> float | None:
        """Estimate X from closed-lobe count at a logarithmic-midpoint Y probe."""
        minimum = float(self.cfg["frequency_min_hz"])
        maximum = float(self.cfg["frequency_max_hz"])
        probe = math.sqrt(minimum * maximum)
        estimates: list[tuple[int, float, dict]] = []
        for phase in (23.0, 67.0):
            self.signal.set(probe, phase)
            time.sleep(self.cfg["settle_seconds"])
            frame = self.camera.frame()
            geometry = self.analyzer.lobe_geometry(frame)
            self.measurements += 1
            lobes = int(geometry["count"])
            orientation = geometry["orientation"]
            if lobes == 1:
                estimate = probe
            elif lobes > 1 and orientation == "horizontal":
                estimate = probe / lobes
            elif lobes > 1 and orientation == "vertical":
                estimate = probe * lobes
            else:
                print(f"LOBES probe={probe:.2f} phase={phase:.1f} invalid {geometry}", flush=True)
                continue
            estimate = float(np.clip(estimate, minimum, maximum))
            estimates.append((lobes, estimate, geometry))
            print(
                f"LOBES probe={probe:.2f} phase={phase:.1f} geometry={geometry} "
                f"estimate={estimate:.2f}Hz",
                flush=True,
            )
        if not estimates:
            return None
        # Prefer the phase with the most closed lobes; tangency at the image
        # boundary can open one or more lobes in the other observation.
        estimates.sort(key=lambda item: item[0], reverse=True)
        return estimates[0][1]

    def estimate_frequency_from_axis_intersections(self) -> float | None:
        """Jump toward fx from the measured crossing ratio; never scan a range."""
        minimum, maximum = self.cfg["frequency_min_hz"], self.cfg["frequency_max_hz"]
        probe_frequency = 10000.0
        estimates: list[float] = []
        phases = (23.0, 67.0, 37.0, 113.0, 151.0)
        for iteration, phase in enumerate(phases):
            self.signal.set(probe_frequency, phase)
            time.sleep(self.cfg["settle_seconds"])
            counts = self.analyzer.axis_intersections(self.camera.frame())
            self.measurements += 1
            if counts is None:
                print(f"AXES probe={probe_frequency:.2f} phase={phase:.1f} invalid", flush=True)
                continue
            x_offsets = counts["x_offset_methods"]
            if max(x_offsets) - min(x_offsets) > 2:
                print(f"AXES probe={probe_frequency:.2f} merged counts={counts}", flush=True)
                continue
            ratio = counts["y_axis"] / counts["x_axis"]
            estimate = float(np.clip(probe_frequency * ratio, minimum, maximum))
            estimates.append(estimate)
            print(
                f"AXES step={iteration + 1} probe={probe_frequency:.2f} "
                f"counts={counts} fx/fy={ratio:.6f} next={estimate:.2f}Hz",
                flush=True,
            )
            if abs(ratio - 1.0) <= 0.08:
                return estimate
            # Damp large integer-count jumps while retaining direct convergence.
            probe_frequency = math.sqrt(probe_frequency * estimate)
        return estimates[-1] if len(estimates) >= 2 else None

    def refine_frequency(
        self, frequency: float, phase: float, result: Measurement
    ) -> tuple[float, float, Measurement]:
        """Three-point shrinking search; samples no predetermined frequency grid."""
        minimum, maximum = self.cfg["frequency_min_hz"], self.cfg["frequency_max_hz"]
        step = max(1000.0, frequency * 0.08)
        best_frequency, best_phase, best_result = frequency, phase, result
        while step >= 1.0:
            probes = sorted({
                max(minimum, best_frequency - step),
                best_frequency,
                min(maximum, best_frequency + step),
            })
            candidates = [(best_frequency, best_phase, best_result)]
            for probe in probes:
                if abs(probe - best_frequency) < 0.25:
                    continue
                probe_phase, probe_result = self.best_phase(
                    probe, [best_phase - 30.0, best_phase, best_phase + 30.0]
                )
                candidates.append((probe, probe_phase, probe_result))
            chosen = min(candidates, key=lambda item: item[2].score)
            if abs(chosen[0] - best_frequency) < 0.25:
                step /= 4.0
            else:
                best_frequency, best_phase, best_result = chosen
                step /= 2.0
        return best_frequency, best_phase, best_result

    def refine_phase(
        self, frequency: float, phase: float, result: Measurement
    ) -> tuple[float, Measurement]:
        """Three-point shrinking phase search without enumerating an angle range."""
        step = 45.0
        best_phase, best_result = phase, result
        while step >= 0.25:
            candidates = [(best_phase, best_result)]
            for probe_phase in (best_phase - step, best_phase + step):
                candidates.append((probe_phase, self.evaluate(frequency, probe_phase)))
            chosen_phase, chosen_result = min(candidates, key=lambda item: item[1].score)
            if abs(chosen_phase - best_phase) < 1e-9:
                step /= 3.0
            else:
                best_phase, best_result = chosen_phase, chosen_result
                step /= 2.0
        return best_phase, best_result

    def run(self, resume_frequency: float | None = None) -> dict:
        self.capture_background()
        minimum, maximum = self.cfg["frequency_min_hz"], self.cfg["frequency_max_hz"]
        if resume_frequency is None:
            ratio_frequency = self.estimate_frequency_from_lobes()
            if ratio_frequency is None:
                ratio_frequency = self.estimate_frequency_from_axis_intersections()
            if ratio_frequency is not None:
                best_frequency = round(ratio_frequency)
                best_phase, best_result = self.best_phase(best_frequency, [0, 45, 90, 135])
                best_frequency, best_phase, best_result = self.refine_frequency(
                    best_frequency, best_phase, best_result
                )
            else:
                failure = {
                    "status": "error",
                    "reason": "axis-intersection frequency estimate invalid or inconsistent",
                    "measurements": self.measurements,
                }
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                (RESULTS_DIR / "result.json").write_text(
                    json.dumps(failure, indent=2), encoding="utf-8"
                )
                raise RuntimeError(failure["reason"])

        else:
            best_frequency = float(resume_frequency)
            best_phase, best_result = self.best_phase(best_frequency, [0, 45, 90, 135])

        best_phase, best_result = self.refine_phase(best_frequency, best_phase, best_result)

        self.signal.set(best_frequency, best_phase)
        time.sleep(self.cfg["settle_seconds"])
        final_frame = self.camera.frame()
        final = self.analyzer.measure(final_frame, RESULTS_DIR / "final_result")
        output = {
            "frequency_hz": best_frequency,
            "phase_deg": float(((best_phase + 180) % 360) - 180),
            "amplitude_vpp": self.cfg["amplitude_vpp"],
            "score": final.score,
            "linearity": final.linearity,
            "residual": final.residual,
            "angle_deg": final.angle_deg,
            "pixels": final.pixels,
            "measurements": self.measurements,
        }
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "result.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        print("RESULT", json.dumps(output, ensure_ascii=False), flush=True)
        return output


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--once", action="store_true", help="measure current image without changing Y")
    parser.add_argument(
        "--image", help="analyze one saved camera image without opening camera or changing Y"
    )
    parser.add_argument(
        "--check-control", action="store_true",
        help="open and close the configured control backend without changing Y",
    )
    parser.add_argument("--resume-frequency", type=float, help="resume phase search from a camera-derived frequency")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.check_control:
        signal = build_signal_client(cfg)
        try:
            print(f"CONTROL_OK backend={type(signal).__name__}")
        finally:
            signal.close()
        return

    analyzer = Analyzer(cfg)
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            raise RuntimeError(f"cannot read image: {args.image}")
        result = analyzer.measure(frame, DIAGNOSTICS_DIR / "current")
        print(json.dumps(result.__dict__, indent=2))
        return

    camera = Camera(cfg)
    signal = None
    try:
        if args.once:
            # Diagnostic mode is deliberately read-only: never change the DAC.
            result = analyzer.measure(camera.frame(), DIAGNOSTICS_DIR / "current")
            print(json.dumps(result.__dict__, indent=2))
        else:
            signal = build_signal_client(cfg)
            Calibrator(cfg, camera, analyzer, signal).run(args.resume_frequency)
    finally:
        if signal is not None:
            signal.close()
        camera.close()


if __name__ == "__main__":
    main()
