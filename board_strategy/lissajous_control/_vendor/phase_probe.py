"""Two-frame phase estimation from calibrated bitmaps and known DAC offsets.

No environment, hidden signal, frequency truth, vector path, or trained weights
enters this module. Correct 1:1 or 2:1 frequency alignment is a prerequisite.
"""
from dataclasses import dataclass
import math

from .pixel_vision import foreground
import numpy as np


@dataclass(frozen=True)
class BranchFit:
    valid: bool
    reason: str
    coefficient: float
    residual_div: float
    multiple_branch_fraction: float
    x_height_div: float
    y_height_div: float
    u: tuple
    middle: tuple
    half_gap: tuple
    predicted_middle: tuple
    predicted_gap: tuple


def measure_branches(image, mode, bounds=(-5., -5., 5., 5.)):
    """Fit M(u)=k*u or k*(1-2*u**2), also checking the branch spacing.

    Coordinates are centered, amplitude-normalized, and Y points upwards.
    The extreme two branches alone are not sufficient evidence: additional
    branches and a poor spacing fit explicitly invalidate the measurement.
    """
    if mode not in ('diagonal', 'circle', 'infinity'):
        raise ValueError('Unknown mode')
    mask = foreground(image)
    rows, width = mask.shape
    xmin, ymin, xmax, ymax = map(float, bounds)
    if xmax <= xmin or ymax <= ymin:
        raise ValueError('Invalid calibrated bounds')
    dx, dy = (xmax-xmin)/(width-1), (ymax-ymin)/(rows-1)
    ys, xs = np.where(mask)
    empty = BranchFit(False, '未检测到完整波形', 0., 10., 1., 0., 0., (), (), (), (), ())
    if len(xs) < 30:
        return empty
    lo, hi, top, bottom = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    xheight, yheight = (hi-lo)*dx, (bottom-top)*dy
    cx, cy = (lo+hi)/2, (top+bottom)/2
    if xheight < 2 or yheight < .5:
        return empty
    columns = np.unique(np.round(np.linspace(cx-.84*(hi-lo)/2, cx+.84*(hi-lo)/2, 81)).astype(int))
    us, middles, gaps, counts = [], [], [], []
    for column in columns:
        hit = np.flatnonzero(mask[:, column])
        if not len(hit):
            continue
        us.append((column-cx)/((hi-lo)/2))
        middles.append((cy-(hit[0]+hit[-1])/2)/((bottom-top)/2))
        gaps.append((hit[-1]-hit[0])/(bottom-top))
        counts.append(1+int(np.count_nonzero(np.diff(hit)>1)))
    if len(us) < 40:
        return empty
    u, middle, gap = np.array(us), np.array(middles), np.array(gaps)
    basis = 1-2*u*u if mode == 'infinity' else u
    coefficient = float(np.dot(basis, middle)/np.dot(basis, basis))
    fitted = float(np.clip(coefficient, -1, 1))
    pred_mid = fitted*basis
    pred_gap = np.sqrt(np.maximum(0., 1-u*u))*math.sqrt(max(0., 1-fitted*fitted))
    if mode == 'infinity':
        pred_gap *= 2*np.abs(u)
    residual = float(np.percentile(np.maximum(np.abs(middle-pred_mid), np.abs(gap-pred_gap)), 95)*yheight/2)
    multiple = sum(n > 2 for n in counts)/len(counts)
    clipped = lo <= 1 or hi >= width-2 or top <= 1 or bottom >= rows-2
    valid = not clipped and multiple <= .05 and residual <= .2 and abs(coefficient) <= 1.08
    reason = ('波形被画面边界裁切' if clipped else
              '存在多条分支，不能套用两分支调相公式' if multiple > .05 else
              '分支中线或间距不符合目标频率关系' if residual > .2 or abs(coefficient)>1.08 else
              '两分支中线与间距拟合通过')
    return BranchFit(valid, reason, coefficient, residual, multiple, float(xheight), float(yheight),
                     tuple(us), tuple(middles), tuple(gaps), tuple(map(float, pred_mid)), tuple(map(float, pred_gap)))


def estimate_phase(first, quarter_turn, mode):
    """Initial phase in degrees, given frames before/after a +90 degree command.

    Assumes negligible drift between frames and a continuous DAC accumulator.
    A single static contour cannot resolve the missing quadrature/sign.
    """
    if not first.valid or not quarter_turn.valid:
        raise ValueError('相位探测无效：'+(first.reason if not first.valid else quarter_turn.reason))
    a, b = first.coefficient, quarter_turn.coefficient
    norm = math.hypot(a, b)
    if not .85 <= norm <= 1.15:
        raise ValueError('两次探测不一致：可能存在频差、图像误识别或调相方向错误')
    if mode == 'infinity':
        angle = math.atan2(a, b)
    elif mode in ('diagonal', 'circle'):
        angle = math.atan2(-b, a)
    else:
        raise ValueError('Unknown mode')
    return math.degrees(angle) % 360


def corrected_phase(initial_dac_phase, estimated_initial_phase, mode):
    """Return an absolute phase-offset setting, referenced to the FIRST frame."""
    if mode not in ('diagonal', 'circle', 'infinity'):
        raise ValueError('Unknown mode')
    target = 90. if mode == 'circle' else 0.
    return round((initial_dac_phase+target-estimated_initial_phase) % 360, 1) % 360
