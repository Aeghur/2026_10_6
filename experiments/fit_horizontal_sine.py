#!/usr/bin/env python3
"""Fit a finite-width horizontal sine trace to a photographed ramp sweep.

The fitted object is a tube around x(y), not a zero-width path. Its centre is
required to follow the ridge of the foreground distance transform, samples
inside the tube must be bright, and samples outside it are penalized.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def parse_roi(value: str) -> tuple[int, int, int, int]:
    parts = tuple(map(int, value.split(",")))
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI格式应为 x0,y0,x1,y1")
    return parts


def run_lengths(mask: np.ndarray) -> np.ndarray:
    values: list[int] = []
    for column in mask.T:
        edges = np.diff(np.r_[False, column, False].astype(np.int8))
        values.extend((np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)).tolist())
    return np.asarray(values, dtype=int)


def band_cycle_seed(mask: np.ndarray) -> tuple[float, float]:
    """Return sine cycles and the twice-frequency band harmonic."""
    height = mask.shape[0]
    position = np.linspace(0.0, 1.0, height)
    projection = mask.mean(axis=1).astype(np.float64)
    axis = np.arange(height, dtype=np.float64)
    projection -= np.polyval(np.polyfit(axis, projection, 2), axis)
    frequencies = np.linspace(2.0, min(80.0, height / 2.5), 7801)
    weighted = projection * np.hanning(height)
    scores = np.empty(len(frequencies), dtype=np.float64)
    for start in range(0, len(frequencies), 400):
        block = frequencies[start:start + 400]
        angle = 2.0j * np.pi * block[:, None] * position[None, :]
        scores[start:start + len(block)] = np.abs(
            np.sum(weighted[None, :] * np.exp(-angle), axis=1)
        )
    harmonic = float(frequencies[int(np.argmax(scores))])
    return harmonic / 2.0, harmonic


def evaluate(
    parameters: np.ndarray, mask: np.ndarray, inside_distance: np.ndarray,
    left: float, right: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate [cycles, amplitude, centre, phase, radius] in small batches."""
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
        normal_x, normal_y = 1.0 / norm, -slope / norm
        sample_x = curve[:, :, None] + normal_x[:, :, None] * radius[:, None, None] * factors
        sample_y = rows[None, :, None] + normal_y[:, :, None] * radius[:, None, None] * factors
        sample_x = np.clip(np.rint(sample_x).astype(int), 0, width - 1)
        sample_y = np.clip(np.rint(sample_y).astype(int), 0, height - 1)
        observed = mask[sample_y, sample_x]
        inner_support = observed[:, :, 2:5].mean(axis=(1, 2))
        outside_foreground = observed[:, :, [0, 1, 5, 6]].mean(axis=(1, 2))

        centre_x = np.clip(np.rint(curve).astype(int), 0, width - 1)
        ridge_radius = inside_distance[row_index[None, :], centre_x]
        ridge_error = np.mean(
            np.minimum(
                np.abs(ridge_radius - radius[:, None]) / (radius[:, None] + 1e-6),
                2.0,
            ), axis=1,
        )
        envelope_error = (
            np.abs((centre - amplitude - radius) - left)
            + np.abs((centre + amplitude + radius) - right)
        ) / max(right - left, 1.0)

        stop = start + len(block)
        losses[start:stop] = (
            3.0 * (1.0 - inner_support)
            + 0.35 * outside_foreground
            + 0.70 * ridge_error
            + 1.20 * envelope_error
        )
        diagnostics[start:stop] = np.column_stack(
            (inner_support, outside_foreground, ridge_error, envelope_error)
        )
    return losses, diagnostics


def search_width_model(mask: np.ndarray, seed_cycles: float) -> tuple[np.ndarray, dict]:
    height = mask.shape[0]
    inside_distance = cv2.distanceTransform(
        mask.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE
    )
    _, xx = np.where(mask)
    left, right = map(float, np.percentile(xx, [0.05, 99.95]))
    lengths = run_lengths(mask)
    plausible = lengths[(lengths >= 2) & (lengths <= max(3, height // 20))]
    histogram = np.bincount(plausible) if len(plausible) else np.array([0, 0, 1])
    diameter_seed = float(np.argmax(histogram))
    radius_seed = diameter_seed / 2.0
    centre_seed = (left + right) / 2.0
    amplitude_seed = (right - left) / 2.0 - radius_seed
    rng = np.random.default_rng(20261023)

    count = 70_000
    coarse = np.column_stack((
        rng.uniform(max(0.8, seed_cycles - 0.9), seed_cycles + 0.9, count),
        rng.uniform(amplitude_seed - 10.0, amplitude_seed + 5.0, count),
        rng.uniform(centre_seed - 6.0, centre_seed + 6.0, count),
        rng.uniform(0.0, 2.0 * np.pi, count),
        rng.uniform(max(1.5, radius_seed * 0.65), radius_seed * 1.45, count),
    ))
    coarse_loss, _ = evaluate(coarse, mask, inside_distance, left, right)
    first = coarse[int(np.argmin(coarse_loss))]

    count = 90_000
    fine = np.column_stack((
        rng.uniform(first[0] - 0.18, first[0] + 0.18, count),
        rng.uniform(first[1] - 5.0, first[1] + 5.0, count),
        rng.uniform(first[2] - 4.0, first[2] + 4.0, count),
        (rng.uniform(first[3] - 0.45, first[3] + 0.45, count) % (2.0 * np.pi)),
        rng.uniform(max(1.0, first[4] - 1.0), first[4] + 1.0, count),
    ))
    fine_loss, fine_diag = evaluate(fine, mask, inside_distance, left, right)
    index = int(np.argmin(fine_loss))
    winner = fine[index]
    near = fine[:, 0][fine_loss <= fine_loss[index] + 0.04]
    diagnostic = fine_diag[index]
    details = {
        "foreground_envelope_x": [left, right],
        "vertical_run_mode_px": diameter_seed,
        "objective": float(fine_loss[index]),
        "inner_tube_support": float(diagnostic[0]),
        "outside_foreground_fraction": float(diagnostic[1]),
        "ridge_radius_relative_error": float(diagnostic[2]),
        "envelope_relative_error": float(diagnostic[3]),
        "near_optimum_cycle_range": [float(near.min()), float(near.max())],
        "near_optimum_samples": int(len(near)),
    }
    return winner, details


def render_model(
    image: np.ndarray, roi: tuple[int, int, int, int], parameters: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = roi
    cycles, amplitude, centre, phase, radius = parameters
    height = y1 - y0
    position = np.linspace(0.0, 1.0, height)
    curve = x0 + centre + amplitude * np.sin(2.0 * np.pi * cycles * position + phase)
    points = np.column_stack((
        np.rint(curve).astype(np.int32), np.arange(y0, y1, dtype=np.int32)
    )).reshape(-1, 1, 2)
    tube = np.zeros(image.shape[:2], np.uint8)
    thickness = max(1, int(round(2.0 * radius)))
    cv2.polylines(tube, [points], False, 255, thickness, cv2.LINE_AA)
    overlay = image.copy()
    tint = np.zeros_like(image)
    tint[:, :, 2] = tube
    overlay = cv2.addWeighted(overlay, 1.0, tint, 0.55, 0.0)
    cv2.polylines(overlay, [points], False, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.rectangle(overlay, (x0, y0), (x1 - 1, y1 - 1), (0, 255, 0), 1)
    return overlay, tube


def fit(image: np.ndarray, roi: tuple[int, int, int, int]) -> tuple[dict, np.ndarray, np.ndarray]:
    x0, y0, x1, y1 = roi
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = gray[y0:y1, x0:x1] >= 127
    if mask.shape[0] < 80 or mask.shape[1] < 80:
        raise ValueError("ROI太小")
    band_cycles, harmonic = band_cycle_seed(mask)
    parameters, details = search_width_model(mask, band_cycles)
    overlay, tube = render_model(image, roi, parameters)
    tube_roi = tube[y0:y1, x0:x1] > 0
    intersection = np.count_nonzero(tube_roi & mask)
    precision = intersection / max(np.count_nonzero(tube_roi), 1)
    recall = intersection / max(np.count_nonzero(mask), 1)
    cycles, amplitude, centre, phase, radius = parameters
    report = {
        "model": "finite_width_sine_tube_v1",
        "roi": list(roi),
        "fit": {
            "cycles": float(cycles),
            "amplitude_px": float(amplitude),
            "centre_x_roi_px": float(centre),
            "phase_rad": float(phase),
            "line_radius_px": float(radius),
            "line_width_px": float(2.0 * radius),
            "rendered_tube_precision": float(precision),
            "rendered_tube_recall": float(recall),
            **details,
        },
        "band_projection": {
            "dominant_harmonic_cycles": harmonic,
            "inferred_sine_cycles": band_cycles,
        },
        "warning": (
            "outside_foreground_fraction measures repeated-sweep overlap; "
            "the fitted tube represents one supported branch, not all white pixels"
        ),
    }
    return report, overlay, tube


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--roi", type=parse_roi, required=True, help="x0,y0,x1,y1")
    parser.add_argument("--output", type=Path, default=Path("sine_fit_test"))
    parser.add_argument("--window-us", type=float)
    args = parser.parse_args()
    image = cv2.imread(str(args.image))
    if image is None:
        raise SystemExit(f"无法读取 {args.image}")
    report, overlay, tube = fit(image, args.roi)
    if args.window_us:
        report["frequency_hz"] = report["fit"]["cycles"] / (args.window_us * 1e-6)
        report["frequency_hz_from_bands"] = (
            report["band_projection"]["inferred_sine_cycles"] / (args.window_us * 1e-6)
        )
    args.output.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output / "finite_width_sine_overlay.png"), overlay)
    cv2.imwrite(str(args.output / "fitted_tube_mask.png"), tube)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
