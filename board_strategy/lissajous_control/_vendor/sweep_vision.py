"""Coarse frequency from finite pixels plus a PUBLIC commanded scan window.

The phase of the source is a fitted nuisance parameter. Multiple asynchronous
branches are explicitly rejected instead of turning their centroid into a
fictional sine wave. No source/plant/label enters this module.
"""
from dataclasses import dataclass, asdict
import math

import numpy as np

from .pixel_vision import foreground, components


@dataclass
class SweepFit:
    valid: bool = False
    reason: str = 'insufficient_trace'
    frequency: float | None = None
    uncertainty_hz: float | None = None
    cycles: float = 0.
    confidence: float = 0.
    residual: float = 1.
    density: float = 0.
    multiple_fraction: float = 1.
    coverage: float = 0.
    window_us: float = 0.
    profile: list | None = None
    fitted: list | None = None

    def public(self):
        return asdict(self)


def estimate_sweep(image, window_us, minimum=1000, maximum=100000):
    mask = foreground(image)
    rows, cols = mask.shape
    yy, xx = np.where(mask)
    result = SweepFit(window_us=float(window_us))
    if len(xx) < 50:
        return result
    top, bottom, left, right = map(int, (yy.min(), yy.max(), xx.min(), xx.max()))
    if bottom-top < rows*.3 or top <= 1 or bottom >= rows-2:
        result.reason = 'scan_clipped_or_small'
        return result
    result.density = float(mask[top:bottom+1, left:right+1].mean())
    scan_rows = np.arange(top+3, bottom-2)
    values, locations, clusters = [], [], []
    # y increases upwards; descending row indices follow the ascending ramp.
    for row in scan_rows[::-1]:
        hits = np.flatnonzero(mask[row])
        if len(hits):
            values.append((float(hits.mean())/(cols-1)*10-5)/4)
            locations.append((bottom-row)/(bottom-top))
            clusters.append(components(mask[row]))
    result.coverage = len(values)/max(len(scan_rows), 1)
    result.multiple_fraction = float(np.mean(np.array(clusters) > 1)) if clusters else 1.
    result.profile = list(map(float, values))
    if result.density > .20 or result.multiple_fraction > .28:
        # A dense bitmap alone cannot distinguish repeated sweeps from a
        # single high-frequency sweep beyond the image's spatial resolution.
        result.reason = 'trace_dense_or_multiple'
        return result
    if result.coverage < .9 or len(values) < 64:
        result.reason = 'insufficient_rows'
        return result
    s, x = np.array(locations), np.array(values)
    # Profile at finite pixel rows, not vector trajectory samples. Least squares
    # profiles out both phase and amplitude, with offset as an extra nuisance.
    lower = max(.8, minimum*window_us*1e-6)
    upper = min((len(s)-1)/5, maximum*window_us*1e-6)
    if upper <= lower:
        result.reason = 'scan_window_unresolved'
        return result
    def fit_grid(cycles):
        angle = 2*np.pi*cycles[:, None]*s[None, :]
        basis = np.stack((np.sin(angle), np.cos(angle), np.ones_like(angle)), axis=-1)
        a = np.einsum('bni,bnj->bij', basis, basis)
        b = np.einsum('bni,n->bi', basis, x)
        coef = np.linalg.solve(a+np.eye(3)[None]*1e-8, b[..., None])[..., 0]
        predicted = np.einsum('bni,bi->bn', basis, coef)
        error = np.mean((predicted-x)**2, axis=-1)
        index = int(error.argmin())
        return float(cycles[index]), coef[index], predicted[index], float(error[index])
    coarse = np.arange(lower, upper+.0125, .025)
    c, _, _, _ = fit_grid(coarse)
    fine = np.linspace(max(lower, c-.03), min(upper, c+.03), 61)
    c, coef, predicted, error = fit_grid(fine)
    amplitude = math.hypot(coef[0], coef[1])
    result.cycles = c
    result.residual = math.sqrt(error)
    result.fitted = predicted.tolist()
    if c < 1.3:
        result.reason = 'too_few_cycles_use_long_scan'
    elif c >= upper-.035 and maximum*window_us*1e-6 > upper+.1:
        result.reason = 'too_many_cycles_use_short_scan'
    elif not .7 <= amplitude <= 1.2 or result.residual > .11:
        result.reason = 'non_sinusoidal_or_overlapped_scan'
    else:
        result.valid = True
        result.reason = 'coarse_frequency_observed'
        result.frequency = c/(window_us*1e-6)
        # A conservative engineering interval, not a calibrated confidence bound.
        result.uncertainty_hz = max(100., 3*result.residual/(window_us*1e-6))
        result.confidence = float(np.clip((1-result.residual/.11)*(1-result.multiple_fraction), 0, 1))
    return result
