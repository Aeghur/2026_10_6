"""V3 optical simulator. Only CameraFrame crosses the observation boundary.

An approximate saturating display integrates four temporal subframes, with
optional blur, grid, noise, clock errors and stale camera frames. It is not a
calibrated model of a particular oscilloscope/camera.
"""
from dataclasses import dataclass
import math
import os
import random

os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

GRID = np.arange(1000, 100001, 100, dtype=np.float32)
VERSION = 'active-optics-3.0'


@dataclass(frozen=True)
class Setting:
    frequency: float = 1000.
    vpp: float = 1.
    phase: float = 0.


@dataclass(frozen=True)
class CameraFrame:
    image: Image.Image
    timestamp: float
    setting: Setting
    age: float = 0.
    fresh: bool = True


class OpticalPlant:
    """Private truth is used only to render; training labels are kept outside."""
    def __init__(self, frequency, source_phase, seed=1, domain='clean', size=384):
        self._frequency, self._source_phase = float(frequency), math.radians(source_phase)
        self.rng = random.Random(seed)
        self.noise_rng = np.random.default_rng(seed)
        self.domain, self.size = domain, size
        self.time, self.carrier, self.nominal_carrier = 0., 0., 0.
        self.setting = Setting()
        self.last_frame = None
        self.commands = 0
        self.latency = self.rng.uniform(.09, .16)
        self.exposure = self.rng.uniform(.006, .016)
        self.persistence = self.rng.uniform(.002, .008)
        self.brightness = self.rng.uniform(.85, 1.)
        self.blur = self.rng.uniform(0., .35)
        self.noise = self.rng.uniform(0., 5.)
        self.grid = self.rng.uniform(12., 28.)
        self.ppm = self.rng.uniform(-2., 2.) if domain=='physical' else 0.
        self.gain = self.rng.uniform(.97, 1.03) if domain=='physical' else 1.
        self.phase_lag = self.rng.uniform(-8., 8.) if domain=='physical' else 0.
        self.drop_rate = .025 if domain=='physical' else 0.
        if domain=='ideal':
            self.noise, self.grid, self.blur, self.brightness = 0., 0., 0., 1.

    def advance(self, duration):
        duration = float(duration)
        if duration < 0 or not math.isfinite(duration):
            raise ValueError('Time must advance monotonically')
        self.nominal_carrier += 2*math.pi*self.setting.frequency*duration
        self.carrier = (self.carrier+2*math.pi*self.setting.frequency*(1+self.ppm*1e-6)*duration) % (2*math.pi)
        self.time += duration

    def command(self, setting, wait=None):
        if not (100 <= setting.frequency <= 200100 and .1 <= setting.vpp <= 5):
            raise ValueError('DAC setting outside simulated limits')
        self.setting = Setting(float(setting.frequency), float(setting.vpp), float(setting.phase)%360)
        self.commands += 1
        self.advance(self.latency if wait is None else wait)
        return self.capture()

    def capture(self):
        if self.last_frame is not None and self.rng.random() < self.drop_rate:
            old = self.last_frame
            return CameraFrame(old.image, old.timestamp, self.setting, self.time-old.timestamp, False)
        size = self.size
        duration = self.exposure+self.persistence
        accumulation = np.zeros((size, size), np.float32)
        for part in range(4):
            begin, end = -duration+part*duration/4, -duration+(part+1)*duration/4
            count = max(200, int(max(self._frequency, self.setting.frequency)*(end-begin)*24)+1)
            tau = np.linspace(begin, end, count)
            x = 4*np.sin(2*np.pi*self._frequency*(self.time+tau)+self._source_phase)
            y = self.setting.vpp*self.gain*np.sin(2*np.pi*self.setting.frequency*(1+self.ppm*1e-6)*tau+
                self.carrier+math.radians(self.setting.phase+self.phase_lag))
            points = np.column_stack(((x+5)/10*(size-1), (5-y)/10*(size-1)))
            layer = Image.new('L', (size, size))
            ImageDraw.Draw(layer).line(points.reshape(-1).tolist(), fill=255, width=1)
            accumulation += np.asarray(layer, dtype=np.float32)/1020
        # Approximate exposure average plus display/camera gamma/saturation.
        array = 235*self.brightness*np.power(accumulation, .45)
        if self.grid:
            for index in range(11):
                p = round(index/10*(size-1))
                array[p, :] = np.maximum(array[p, :], self.grid)
                array[:, p] = np.maximum(array[:, p], self.grid)
        image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
        if self.blur:
            image = image.filter(ImageFilter.GaussianBlur(self.blur))
        if self.noise:
            array = np.asarray(image, np.float32)+self.noise_rng.normal(0, self.noise, (size, size))
            image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
        frame = CameraFrame(image, self.time, self.setting)
        self.last_frame = frame
        return frame


def encode_frame(frame):
    """Pixels and public command/time metadata only; no labels or simulator."""
    pixels = np.asarray(frame.image, np.float32)/255.
    if pixels.ndim != 2:
        raise ValueError('Expected calibrated grayscale camera frame')
    thumbnail = np.asarray(frame.image.resize((96,96), Image.Resampling.BOX), np.float32)[None]/255.
    # Preserve fine full-resolution strokes as six rows and columns, in
    # addition to the CNN thumbnail. All features remain pixel measurements.
    locations = np.linspace(.18, .82, 6)
    strips = [pixels[round(v*(pixels.shape[0]-1)), :] for v in locations]
    strips += [pixels[:, round(v*(pixels.shape[1]-1))] for v in locations]
    strips = np.stack([np.interp(np.linspace(0,len(s)-1,128), np.arange(len(s)), s) for s in strips]).astype(np.float32)
    p = math.radians(frame.setting.phase)
    meta = np.array([frame.setting.frequency/200000, frame.setting.vpp/4, math.sin(p), math.cos(p),
                     frame.timestamp/10, min(frame.age,1), float(frame.fresh)], np.float32)
    return thumbnail, strips, meta


def signature(image):
    """Finite-pixel descriptor for preflight/approximate information teacher."""
    small = np.asarray(image.resize((24,24), Image.Resampling.BOX), np.float32).ravel()/255
    pixels = np.asarray(image, np.float32)/255
    projections = np.r_[pixels.mean(0)[::4], pixels.mean(1)[::4]]
    return np.r_[small, projections].astype(np.float32)
