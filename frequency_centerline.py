"""Board-only centreline extraction for the ramp frequency stage.

Raw calibrated images still feed the learned frequency experts.  This module
creates a separate one-pixel sine centreline solely for their geometric
cross-check; it is never used by phase, amplitude, or hold verification.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from lissajous_control.backend import FrequencyBackend
from lissajous_control._vendor.pixel_vision import foreground
from lissajous_control._vendor.sweep_vision import estimate_sweep


def _band_seed(mask: np.ndarray) -> float:
    height = mask.shape[0]
    position = np.linspace(0.0, 1.0, height)
    profile = mask.mean(axis=1).astype(np.float64)
    axis = np.arange(height, dtype=np.float64)
    profile -= np.polyval(np.polyfit(axis, profile, 2), axis)
    harmonics = np.linspace(2.0, min(80.0, height / 2.5), 5001)
    weighted = profile * np.hanning(height)
    scores = np.empty(len(harmonics), dtype=np.float64)
    for start in range(0, len(harmonics), 400):
        block = harmonics[start:start + 400]
        angle = 2.0j * np.pi * block[:, None] * position[None, :]
        scores[start:start + len(block)] = np.abs(
            np.sum(weighted[None, :] * np.exp(-angle), axis=1)
        )
    return float(harmonics[int(np.argmax(scores))] / 2.0)


def _vertical_run_mode(mask: np.ndarray) -> int:
    runs: list[int] = []
    for column in mask.T:
        edges = np.diff(np.r_[False, column, False].astype(np.int8))
        runs.extend((np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)).tolist())
    values = np.asarray(runs, dtype=int)
    values = values[(values >= 2) & (values <= max(3, mask.shape[0] // 20))]
    return int(np.argmax(np.bincount(values))) if len(values) else 2


def _evaluate(parameters, mask, inside_distance, left, right):
    height, width = mask.shape
    rows = np.arange(2, height - 2, 2, dtype=np.float64)
    position = rows / (height - 1)
    row_index = rows.astype(int)
    losses = np.empty(len(parameters), dtype=np.float64)
    diagnostics = np.empty((len(parameters), 4), dtype=np.float64)
    factors = np.array([-1.70, -1.25, -0.65, 0.0, 0.65, 1.25, 1.70])
    for start in range(0, len(parameters), 256):
        block = parameters[start:start + 256]
        cycles, amplitude, centre, phase, radius = block.T
        angle = 2.0 * np.pi * cycles[:, None] * position + phase[:, None]
        curve = centre[:, None] + amplitude[:, None] * np.sin(angle)
        slope = (
            amplitude[:, None] * 2.0 * np.pi * cycles[:, None]
            / (height - 1) * np.cos(angle)
        )
        norm = np.sqrt(1.0 + np.square(slope))
        nx, ny = 1.0 / norm, -slope / norm
        sample_x = curve[:, :, None] + nx[:, :, None] * radius[:, None, None] * factors
        sample_y = rows[None, :, None] + ny[:, :, None] * radius[:, None, None] * factors
        sample_x = np.clip(np.rint(sample_x).astype(int), 0, width - 1)
        sample_y = np.clip(np.rint(sample_y).astype(int), 0, height - 1)
        observed = mask[sample_y, sample_x]
        inner = observed[:, :, 2:5].mean(axis=(1, 2))
        outside = observed[:, :, [0, 1, 5, 6]].mean(axis=(1, 2))
        centre_x = np.clip(np.rint(curve).astype(int), 0, width - 1)
        ridge_radius = inside_distance[row_index[None, :], centre_x]
        ridge = np.mean(
            np.minimum(
                np.abs(ridge_radius - radius[:, None]) / (radius[:, None] + 1e-6), 2.0
            ), axis=1,
        )
        envelope = (
            np.abs((centre - amplitude - radius) - left)
            + np.abs((centre + amplitude + radius) - right)
        ) / max(right - left, 1.0)
        stop = start + len(block)
        losses[start:stop] = 3 * (1 - inner) + .35 * outside + .7 * ridge + 1.2 * envelope
        diagnostics[start:stop] = np.column_stack((inner, outside, ridge, envelope))
    return losses, diagnostics


def _render_smooth(cycles, amplitude, centre, phase, y0, y1, *, supersample=8):
    high_rows = np.arange(y0 * supersample, y1 * supersample, dtype=np.float64)
    position = (high_rows / supersample - y0) / max(y1 - y0 - 1, 1)
    columns = centre + amplitude * np.sin(2 * np.pi * cycles * position + phase)
    points = np.column_stack((
        np.rint(columns * supersample).astype(np.int32), high_rows.astype(np.int32),
    )).reshape(-1, 1, 2)
    high = np.zeros((384 * supersample, 384 * supersample), np.uint8)
    cv2.polylines(high, [points], False, 255, round(supersample * .75), cv2.LINE_AA)
    return cv2.resize(high, (384, 384), interpolation=cv2.INTER_AREA)


class FrequencyCenterlineExtractor:
    def __init__(self, roi, *, coarse_samples=8_000, fine_samples=10_000):
        self.roi = tuple(map(int, roi))
        if len(self.roi) != 4:
            raise ValueError("frequency centreline ROI must contain x0,y0,x1,y1")
        self.coarse_samples = int(coarse_samples)
        self.fine_samples = int(fine_samples)

    def _thin_trace(self, image, window_us):
        measured = estimate_sweep(image, window_us, 1000.0, 100000.0)
        if not measured.valid:
            return None
        mask = foreground(image)
        yy, _ = np.where(mask)
        top, bottom = int(yy.min()), int(yy.max())
        rows, values = [], []
        for row in range(top, bottom + 1):
            hits = np.flatnonzero(mask[row])
            if len(hits):
                rows.append(row)
                values.append(float(hits.mean()))
        position = (np.asarray(rows) - top) / max(bottom - top, 1)
        angle = 2 * np.pi * measured.cycles * position
        design = np.column_stack((np.sin(angle), np.cos(angle), np.ones(len(angle))))
        sine, cosine, centre = np.linalg.lstsq(design, values, rcond=None)[0]
        amplitude = float(np.hypot(sine, cosine))
        phase = float(np.arctan2(cosine, sine) % (2 * np.pi))
        result = _render_smooth(measured.cycles, amplitude, centre, phase, top, bottom + 1)
        return result, {
            "method": "thin_trace_refit",
            "cycles": measured.cycles,
            "input_confidence": measured.confidence,
            "input_residual": measured.residual,
        }

    def extract(self, image, window_us, *, seed=0):
        array = np.asarray(image)
        thin = self._thin_trace(array, window_us)
        if thin is not None:
            centreline, report = thin
        else:
            x0, y0, x1, y1 = self.roi
            mask = foreground(array)[y0:y1, x0:x1]
            if mask.shape[0] < 80 or mask.shape[1] < 80:
                raise ValueError("frequency centreline ROI is too small")
            yy, xx = np.where(mask)
            if len(xx) < 100:
                raise ValueError("frequency centreline has insufficient foreground")
            left, right = map(float, np.percentile(xx, [.05, 99.95]))
            diameter = _vertical_run_mode(mask)
            radius = diameter / 2.0
            centre = (left + right) / 2.0
            amplitude = (right - left) / 2.0 - radius
            cycles = _band_seed(mask)
            allowed_low = max(.8, 1000.0 * window_us * 1e-6)
            allowed_high = min((mask.shape[0] - 1) / 5, 100000.0 * window_us * 1e-6)
            if not allowed_low <= cycles <= allowed_high:
                raise ValueError("band cycle seed is outside the commanded window range")
            distance = cv2.distanceTransform(
                mask.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE
            )
            rng = np.random.default_rng(seed)
            count = self.coarse_samples
            radius_low = max(.75, radius * .65)
            radius_high = max(radius_low + .25, radius * 1.45)
            coarse = np.column_stack((
                rng.uniform(max(allowed_low, cycles - .7), min(allowed_high, cycles + .7), count),
                rng.uniform(amplitude - 10, amplitude + 5, count),
                rng.uniform(centre - 6, centre + 6, count),
                rng.uniform(0, 2 * np.pi, count),
                rng.uniform(radius_low, radius_high, count),
            ))
            loss, _ = _evaluate(coarse, mask, distance, left, right)
            first = coarse[int(np.argmin(loss))]
            count = self.fine_samples
            fine = np.column_stack((
                rng.uniform(max(allowed_low, first[0] - .16), min(allowed_high, first[0] + .16), count),
                rng.uniform(first[1] - 5, first[1] + 5, count),
                rng.uniform(first[2] - 4, first[2] + 4, count),
                rng.uniform(first[3] - .4, first[3] + .4, count) % (2 * np.pi),
                rng.uniform(max(1, first[4] - 1), first[4] + 1, count),
            ))
            loss, diagnostics = _evaluate(fine, mask, distance, left, right)
            index = int(np.argmin(loss))
            cycles, amplitude, centre, phase, radius = fine[index]
            inner, outside, ridge, envelope = diagnostics[index]
            if inner < .90 or ridge > .60 or envelope > .10:
                raise ValueError("finite-width sine fit did not meet centreline requirements")
            centreline = _render_smooth(
                cycles, amplitude, x0 + centre, phase, y0, y1
            )
            report = {
                "method": "finite_width_sine_tube",
                "cycles": float(cycles),
                "line_width_px": float(2 * radius),
                "inner_tube_support": float(inner),
                "outside_foreground_fraction": float(outside),
                "ridge_radius_relative_error": float(ridge),
                "envelope_relative_error": float(envelope),
            }
        check = estimate_sweep(centreline, window_us, 1000.0, 100000.0)
        report["output_valid"] = bool(check.valid)
        report["output_reason"] = check.reason
        report["output_frequency_hz"] = check.frequency
        report["output_confidence"] = check.confidence
        report["output_residual"] = check.residual
        if not check.valid:
            raise ValueError("extracted centreline failed geometric validation: " + check.reason)
        return centreline, report


class FrequencyStageBackend:
    """Use fitted centrelines only inside Controller's ramp-frequency backend."""
    def __init__(self, cfg: dict, output: Path):
        self.inner = FrequencyBackend(threads=2)
        self.output = Path(output)
        self.extractor = FrequencyCenterlineExtractor(
            cfg.get("strategy_frequency_centerline_roi", [20, 88, 355, 307]),
            coarse_samples=int(cfg.get("strategy_centerline_coarse_samples", 8000)),
            fine_samples=int(cfg.get("strategy_centerline_fine_samples", 10000)),
        )
        self.sequence = 0

    def reset(self):
        self.inner.reset()

    def next_window(self):
        return self.inner.next_window()

    def observe(self, window, images):
        self.sequence += 1
        geometry_images, reports = [], []
        for index, image in enumerate(images, 1):
            try:
                centreline, report = self.extractor.extract(
                    image, float(window), seed=1000 * self.sequence + index
                )
            except (TypeError, ValueError) as error:
                centreline = np.zeros((384, 384), np.uint8)
                report = {"method": "rejected", "output_valid": False, "error": str(error)}
            geometry_images.append(centreline)
            reports.append(report)
            stem = f"frequency_{self.sequence:02d}_{float(window):g}us_pair{index}"
            cv2.imwrite(str(self.output / f"{stem}_centerline.png"), centreline)
            (self.output / f"{stem}_centerline.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        result = self.inner.observe(window, images, geometry_images=geometry_images)
        result["centerline_geometry"] = reports
        result["centerline_scope"] = "frequency_stage_only"
        return result
