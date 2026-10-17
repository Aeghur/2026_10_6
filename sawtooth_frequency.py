#!/usr/bin/env python3
"""Measure X frequency by using the DAC sawtooth as a photographed timebase."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from msp_wave_link import MSPM0WaveLink
from vision_calibrator import Analyzer, Camera, LensCorrector, ROOT


class SawtoothAnalyzer:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.lens = LensCorrector(cfg)
        columns, rows = map(int, cfg.get("grid_dimensions_div", [12, 8]))
        pixels_per_div = int(cfg.get("sawtooth_pixels_per_div", 80))
        self.width = columns * pixels_per_div
        self.height = rows * pixels_per_div
        source = np.float32(cfg["full_grid_corners"])
        target = np.float32([
            [0, 0], [self.width - 1, 0],
            [self.width - 1, self.height - 1], [0, self.height - 1],
        ])
        self.transform = cv2.getPerspectiveTransform(source, target)
        self.value_min = int(cfg.get("white_value_min", 175))
        self.saturation_max = int(cfg.get("white_saturation_max", 80))

    def warp(self, frame: np.ndarray) -> np.ndarray:
        corrected = self.lens.apply(frame)
        return cv2.warpPerspective(
            corrected, self.transform, (self.width, self.height)
        )

    def mask(self, warped: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array([0, 0, self.value_min]),
            np.array([179, self.saturation_max, 255]),
        )
        return cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
        )

    def centerline(self, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        candidates = [
            label for label in range(1, count)
            if stats[label, cv2.CC_STAT_HEIGHT] >= self.height * 0.35
            and stats[label, cv2.CC_STAT_WIDTH] < self.width * 0.95
        ]
        if not candidates:
            raise RuntimeError("未找到贯穿Y方向的锯齿扫描轨迹")
        label = max(candidates, key=lambda item: stats[item, cv2.CC_STAT_AREA])
        component = labels == label
        ys: list[float] = []
        xs: list[float] = []
        for y in range(self.height):
            row = np.flatnonzero(component[y])
            if len(row) >= 4:
                ys.append(float(y))
                xs.append(float(np.median(row)))
        if len(ys) < self.height * 0.3:
            raise RuntimeError("有效锯齿中心线覆盖不足")
        overlay = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        for y, x in zip(ys, xs):
            overlay[int(y), int(round(x))] = (0, 0, 255)
        return np.asarray(ys), np.asarray(xs), overlay

    @staticmethod
    def _fit_at(cycles: float, t: np.ndarray, x: np.ndarray) -> tuple[float, np.ndarray]:
        design = np.column_stack([
            np.sin(2 * np.pi * cycles * t),
            np.cos(2 * np.pi * cycles * t),
            np.ones(len(t)), t,
        ])
        coefficients = np.linalg.lstsq(design, x, rcond=None)[0]
        rms = float(np.sqrt(np.mean(np.square(x - design @ coefficients))))
        return rms, coefficients

    def analyze(self, frame: np.ndarray) -> tuple[dict, dict[str, np.ndarray]]:
        warped = self.warp(frame)
        mask = self.mask(warped)
        ys, xs, overlay = self.centerline(mask)
        y_min, y_max = float(ys.min()), float(ys.max())
        t = (ys - y_min) / max(y_max - y_min, 1.0)

        uniform_t = np.linspace(0.0, 1.0, self.height)
        uniform_x = np.interp(uniform_t, t, xs)
        trend = np.polyval(np.polyfit(uniform_t, uniform_x, 1), uniform_t)
        spectrum = np.abs(np.fft.rfft((uniform_x - trend) * np.hanning(self.height), 8192))
        frequencies = np.fft.rfftfreq(8192, d=1.0 / self.height)
        usable = (frequencies >= 0.25) & (frequencies <= self.height / 8)
        seed = float(frequencies[np.flatnonzero(usable)[np.argmax(spectrum[usable])]])

        lower = max(0.25, seed - 1.5)
        upper = min(self.height / 8, seed + 1.5)
        best_rms = float("inf")
        best_cycles = seed
        best_coefficients = np.zeros(4)
        for cycles in np.linspace(lower, upper, 1501):
            rms, coefficients = self._fit_at(float(cycles), t, xs)
            if rms < best_rms:
                best_rms = rms
                best_cycles = float(cycles)
                best_coefficients = coefficients

        amplitude = float(np.hypot(best_coefficients[0], best_coefficients[1]))
        result = {
            "cycles_per_sweep": best_cycles,
            "fit_rms_px": best_rms,
            "trace_amplitude_px": amplitude,
            "trace_rows": len(ys),
            "trace_y_range": [y_min, y_max],
            "single_value_coverage": len(ys) / (y_max - y_min + 1.0),
        }
        result["locked"] = bool(
            result["single_value_coverage"] >= 0.8
            and best_rms <= float(self.cfg.get("sawtooth_lock_max_rms_px", 20.0))
            and amplitude <= self.width * 0.45
        )
        return result, {"warped": warped, "mask": mask, "centerline": overlay}

    def estimate_harmonic_cycles(self, frame: np.ndarray) -> dict:
        """Estimate many X cycles from the spacing of horizontal extrema bands."""
        mask = self.mask(self.warp(frame))
        x0, x1 = int(self.width * 0.20), int(self.width * 0.80)
        projection = np.mean(mask[:, x0:x1] > 0, axis=1)
        smooth = np.convolve(projection, np.ones(9) / 9.0, mode="same")
        threshold = max(0.18, float(np.percentile(smooth, 70)))
        peaks: list[int] = []
        for y in range(5, self.height - 5):
            if smooth[y] < threshold or smooth[y] < np.max(smooth[y - 5:y + 6]):
                continue
            if not peaks or y - peaks[-1] > 12:
                peaks.append(y)
            elif smooth[y] > smooth[peaks[-1]]:
                peaks[-1] = y
        if len(peaks) < 6:
            raise RuntimeError("高频亮带数量不足")
        spacings = np.diff(np.asarray(peaks, dtype=float))
        median_spacing = float(np.median(spacings))
        good = spacings[
            (spacings >= median_spacing * 0.70)
            & (spacings <= median_spacing * 1.30)
        ]
        if len(good) < 4:
            raise RuntimeError("高频亮带间距不稳定")
        half_period_px = float(np.median(good))
        sweep_height_px = float(self.cfg.get("sawtooth_sweep_height_px", 467.0))
        cycles = sweep_height_px / (2.0 * half_period_px)
        return {
            "cycles_per_sweep": cycles,
            "extrema_count": len(peaks),
            "half_period_px": half_period_px,
            "sweep_height_px": sweep_height_px,
        }


def estimate_sawtooth_seed(
    cfg: dict, camera: Camera, link: MSPM0WaveLink,
    analyzer: SawtoothAnalyzer, amplitude: int, output: Path,
) -> tuple[float | None, list[dict]]:
    """Use two low sawtooth frequencies to estimate a high unknown X input."""
    minimum = float(cfg["frequency_min_hz"])
    maximum = float(cfg["frequency_max_hz"])
    observations: list[dict] = []
    estimates: list[float] = []
    for index, saw_hz in enumerate((minimum, min(maximum, minimum * 1.2)), 1):
        link.set_sawtooth(saw_hz, 0.0, amplitude)
        time.sleep(max(0.5, float(cfg.get("settle_seconds", 0.4))))
        frame = camera.frame()
        cv2.imwrite(str(output / f"seed_saw_{index}_{saw_hz:.3f}Hz.jpg"), frame)
        try:
            result = analyzer.estimate_harmonic_cycles(frame)
            estimate = float(saw_hz * result["cycles_per_sweep"])
            result.update({"saw_hz": saw_hz, "estimate_hz": estimate})
            observations.append(result)
            if result["cycles_per_sweep"] >= 2.0:
                estimates.append(estimate)
        except RuntimeError as error:
            observations.append({"saw_hz": saw_hz, "error": str(error)})
    if not estimates:
        return None, observations
    return float(np.clip(np.median(estimates), minimum, maximum)), observations


def estimate_sine_seed(
    cfg: dict, camera: Camera, link: MSPM0WaveLink
) -> tuple[float, list[dict]]:
    """Use the existing sine-lobe geometry once to seed the sawtooth search."""
    analyzer = Analyzer(cfg)
    minimum = float(cfg["frequency_min_hz"])
    maximum = float(cfg["frequency_max_hz"])
    middle = float(np.sqrt(minimum * maximum))
    references = []
    for frequency, phase in (
        (minimum, 17.0), (minimum, 71.0),
        (middle, 29.0), (middle, 83.0),
        (maximum, 41.0), (maximum, 107.0),
    ):
        link.set_waveform("sine", frequency, phase, int(cfg["amplitude_level"]))
        time.sleep(max(0.5, float(cfg.get("settle_seconds", 0.4))))
        references.append(camera.frame())
    analyzer.set_background_frames(references)

    observations = []
    for phase in (23.0, 67.0):
        link.set_waveform("sine", middle, phase, int(cfg["amplitude_level"]))
        time.sleep(max(0.5, float(cfg.get("settle_seconds", 0.4))))
        geometry = analyzer.lobe_geometry(camera.frame())
        lobes = int(geometry["count"])
        orientation = geometry["orientation"]
        estimate = None
        if lobes == 1:
            estimate = middle
        elif lobes > 1 and orientation == "horizontal":
            estimate = middle / lobes
        elif lobes > 1 and orientation == "vertical":
            estimate = middle * lobes
        if estimate is not None:
            estimate = float(np.clip(estimate, minimum, maximum))
        observations.append({
            "phase_deg": phase,
            "geometry": geometry,
            "estimate_hz": estimate,
        })
    valid = [item for item in observations if item["estimate_hz"] is not None]
    if not valid:
        raise RuntimeError("正弦波瓣初估失败，无法启动锯齿自动搜索")
    valid.sort(key=lambda item: int(item["geometry"]["count"]), reverse=True)
    return float(valid[0]["estimate_hz"]), observations


def lock_score(result: dict) -> float:
    """Continuous objective: thin single-valued curves score lowest."""
    score = float(result["fit_rms_px"])
    score += max(0.0, float(result["trace_amplitude_px"]) - 430.0) * 0.15
    score += max(0.0, 0.9 - float(result["single_value_coverage"])) * 300.0
    return score


def automatic_search(
    cfg: dict, camera: Camera, link: MSPM0WaveLink,
    analyzer: SawtoothAnalyzer, initial_frequency: float | None,
    amplitude: int, output: Path,
) -> tuple[float, list[dict], list[dict]]:
    if initial_frequency is None:
        seed, seed_observations = estimate_sawtooth_seed(
            cfg, camera, link, analyzer, amplitude, output
        )
        if seed is None:
            seed, sine_observations = estimate_sine_seed(cfg, camera, link)
            seed_observations.extend(sine_observations)
    else:
        seed = initial_frequency
        seed_observations = []
    minimum = float(cfg["frequency_min_hz"])
    maximum = float(cfg["frequency_max_hz"])
    center = float(np.clip(seed, minimum, maximum))
    span = max(100.0, center * (0.03 if initial_frequency is None else 0.20))
    probes: list[dict] = []
    probe_index = 0
    for stage in range(4):
        points = 25 if stage == 0 and initial_frequency is None else 9
        frequencies = np.linspace(center - span, center + span, points)
        stage_results = []
        for frequency in frequencies:
            frequency = float(np.clip(frequency, minimum, maximum))
            if any(abs(item["frequency_hz"] - frequency) < 0.0005 for item in probes):
                continue
            probe_index += 1
            link.set_sawtooth(frequency, 0.0, amplitude)
            time.sleep(max(0.5, float(cfg.get("settle_seconds", 0.4))))
            frame = camera.frame()
            cv2.imwrite(str(output / f"probe_{probe_index:03d}_{frequency:.3f}Hz.jpg"), frame)
            try:
                result, _ = analyzer.analyze(frame)
                score = lock_score(result)
                record = {
                    "stage": stage + 1, "frequency_hz": frequency,
                    "score": score, **result,
                }
            except RuntimeError as error:
                record = {
                    "stage": stage + 1, "frequency_hz": frequency,
                    "score": 1_000_000.0, "locked": False,
                    "error": str(error),
                }
            probes.append(record)
            stage_results.append(record)
            print("PROBE", json.dumps(record, ensure_ascii=False), flush=True)
        # Include previously measured points. The current centre may already
        # be the best point and is intentionally skipped as a duplicate.
        best = min(probes, key=lambda item: float(item["score"]))
        center = float(best["frequency_hz"])
        span /= 4.0

    # A slightly wrong frequency can look thin in one exposure. Recheck the
    # best distinct candidates across time; only true lock keeps the extracted
    # centreline stationary between frames.
    ranked = sorted(probes, key=lambda item: float(item["score"]))
    finalists: list[float] = []
    for stage in range(1, 5):
        stage_items = [item for item in probes if item["stage"] == stage]
        if stage_items:
            frequency = float(min(stage_items, key=lambda item: float(item["score"]))["frequency_hz"])
            if all(abs(frequency - chosen) >= 0.5 for chosen in finalists):
                finalists.append(frequency)
    for item in ranked:
        frequency = float(item["frequency_hz"])
        if all(abs(frequency - chosen) >= 0.5 for chosen in finalists):
            finalists.append(frequency)
        if len(finalists) >= 7:
            break
    temporal_records = []
    for frequency in finalists:
        link.set_sawtooth(frequency, 0.0, amplitude)
        time.sleep(max(0.5, float(cfg.get("settle_seconds", 0.4))))
        curves = []
        frame_scores = []
        for sample in range(3):
            frame = camera.frame()
            try:
                result, images = analyzer.analyze(frame)
                ys, xs, _ = analyzer.centerline(images["mask"])
                common_y = np.arange(analyzer.height, dtype=float)
                curves.append(np.interp(common_y, ys, xs, left=np.nan, right=np.nan))
                frame_scores.append(lock_score(result))
            except RuntimeError:
                frame_scores.append(1_000_000.0)
        temporal_spread = 1_000_000.0
        if len(curves) >= 2:
            stack = np.stack(curves)
            valid_columns = np.count_nonzero(np.isfinite(stack), axis=0) == len(curves)
            if np.count_nonzero(valid_columns) >= analyzer.height * 0.3:
                temporal_spread = float(np.median(np.std(stack[:, valid_columns], axis=0)))
        temporal_score = float(np.median(frame_scores)) + temporal_spread * 2.0
        record = {
            "stage": "temporal", "frequency_hz": frequency,
            "frame_scores": frame_scores,
            "temporal_spread_px": temporal_spread,
            "score": temporal_score,
            "locked": temporal_score < 30.0,
        }
        probes.append(record)
        temporal_records.append(record)
        print("TEMPORAL", json.dumps(record, ensure_ascii=False), flush=True)
    best_temporal = min(temporal_records, key=lambda item: float(item["score"]))
    if bool(best_temporal["locked"]):
        chosen = best_temporal
    else:
        # Independent X and Y clocks can retain phase drift even when their
        # displayed frequencies agree. Temporal stability may prove phase
        # lock, but must not override the spatial search minimum.
        final_stage = [item for item in probes if item.get("stage") == 4]
        valid_final = [
            item for item in final_stage
            if float(item.get("score", 1_000_000.0)) < 1_000_000.0
        ]
        chosen = min(valid_final or ranked, key=lambda item: float(item["score"]))
    return float(chosen["frequency_hz"]), seed_observations, probes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--saw-frequency", type=float, default=1000.0)
    parser.add_argument("--auto", action="store_true", help="automatically search unknown X frequency")
    parser.add_argument("--initial-frequency", type=float, help="optional seed for --auto")
    parser.add_argument("--amplitude", type=int, choices=(2, 4, 6, 8), default=6)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument(
        "--known-x-hz", type=float,
        help="known X frequency; report the measured sawtooth timebase scale",
    )
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    analyzer = SawtoothAnalyzer(cfg)
    camera = Camera(cfg)
    link = MSPM0WaveLink(str(cfg.get("serial_port", "/dev/ttyS4"))).open()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = ROOT / "artifacts" / "diagnostics" / f"sawtooth_{stamp}"
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    errors: list[dict] = []
    search_probes: list[dict] = []
    seed_observations: list[dict] = []
    try:
        target_frequency = args.saw_frequency
        if args.auto:
            target_frequency, seed_observations, search_probes = automatic_search(
                cfg, camera, link, analyzer, args.initial_frequency,
                args.amplitude, output,
            )
        ack = link.set_sawtooth(target_frequency, 0.0, args.amplitude)
        time.sleep(max(0.8, float(cfg.get("settle_seconds", 0.4))))
        for index in range(args.samples):
            frame = camera.frame()
            cv2.imwrite(str(output / f"{index + 1:02d}_raw.jpg"), frame)
            try:
                result, images = analyzer.analyze(frame)
            except RuntimeError as error:
                errors.append({"sample": index + 1, "error": str(error)})
                continue
            result["sample"] = index + 1
            results.append(result)
            for name, image in images.items():
                cv2.imwrite(str(output / f"{index + 1:02d}_{name}.png"), image)
    finally:
        link.close()
        camera.close()

    locked_samples = [item for item in results if item["locked"]]
    locked = len(locked_samples) > len(results) // 2
    spatially_converged = False
    if args.auto:
        final_stage = [item for item in search_probes if item.get("stage") == 4]
        spatially_converged = bool(final_stage) and min(
            float(item.get("score", 1_000_000.0)) for item in final_stage
        ) <= 30.0
    report = {
        "status": (
            "locked" if locked else
            "estimated" if spatially_converged else
            "unlocked" if results else "error"
        ),
        "waveform": "sawtooth",
        "automatic_search": args.auto,
        "commanded_saw_hz": target_frequency,
        "ack_saw_hz": ack.frequency_millihz / 1000.0,
        "amplitude": args.amplitude,
        "locked": locked,
        "samples": results,
        "errors": errors,
        "seed_observations": seed_observations,
        "search_probes": search_probes,
    }
    if results:
        cycles = float(np.median([item["cycles_per_sweep"] for item in results]))
        report["median_cycles_per_sweep"] = cycles
    if locked or spatially_converged:
        # The spatial minimum estimates fx = fsaw. Multi-frame stability is a
        # stronger, optional indication that their relative phase is fixed.
        report["estimated_x_hz"] = target_frequency
        report["phase_locked"] = locked
    if args.known_x_hz is not None:
        report["known_x_hz"] = args.known_x_hz
        if locked or spatially_converged:
            report["frequency_error_hz"] = target_frequency - args.known_x_hz
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"ARTIFACTS {output}")
    if not (locked or spatially_converged):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
