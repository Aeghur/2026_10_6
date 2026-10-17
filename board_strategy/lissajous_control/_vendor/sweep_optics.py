"""Report-inspired ramp probing, including asynchronous repeated sweeps.

Only rendering reads private source parameters. 'single' explicitly assumes an
oscilloscope has latched one complete ascending sweep; it is a diagnostic
acquisition condition, never an action the learner can magically enable.
"""
from dataclasses import dataclass
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .active_optics import OpticalPlant, CameraFrame, Setting


@dataclass(frozen=True)
class SweepSetting:
    window_us: float = 175.
    vpp: float = 4.
    phase: float = 0.
    waveform: str = 'ramp'

    @property
    def frequency(self):
        return 1e6/(self.window_us*1.05)


class SweepPlant(OpticalPlant):
    def __init__(self, frequency, source_phase, seed=1, domain='clean', size=384,
                 acquisition='free', exposure_ms=8., persistence_ms=2.):
        super().__init__(frequency, source_phase, seed, domain, size)
        if acquisition not in ('free', 'single'):
            raise ValueError('acquisition must be free or single')
        self.acquisition = acquisition
        self.ramp_exposure = exposure_ms/1000
        self.ramp_persistence = persistence_ms/1000
        # Sine-mode frames include a complete 100 Hz-grid common period.
        self.exposure, self.persistence = .012, .002 if domain == 'physical' else 0.
        self.ramp_origin = 0.
        self.curvature = self.rng.uniform(-.008, .008) if domain == 'physical' else 0.

    def command(self, setting, wait=None):
        if isinstance(setting, SweepSetting):
            if setting.window_us not in (175., 1750.) or not .1 <= setting.vpp <= 5:
                raise ValueError('Unsupported ramp command')
            self.setting = setting
            self.ramp_origin = self.time
            self.commands += 1
            self.advance(self.latency if wait is None else wait)
            return self.capture()
        return super().command(setting, wait)

    def capture(self):
        if not isinstance(self.setting, SweepSetting):
            return super().capture()
        if self.last_frame is not None and self.rng.random() < self.drop_rate:
            old = self.last_frame
            return CameraFrame(old.image, old.timestamp, old.setting, self.time-old.timestamp, False)
        window = self.setting.window_us*1e-6/(1+self.ppm*1e-6)
        reset = window*.05
        cycle = window+reset
        duration = self.ramp_exposure+self.ramp_persistence
        last = math.floor((self.time-self.ramp_origin-window)/cycle)
        if self.acquisition == 'single':
            # Explicit latched-sweep assumption, not an ordinary USB snapshot.
            starts = [self.ramp_origin+last*cycle]
        else:
            first = math.floor((self.time-duration-self.ramp_origin)/cycle)
            starts = [self.ramp_origin+k*cycle for k in range(first, last+2)]
        layers = np.zeros((self.size, self.size), np.float32)
        for start in starts:
            for returning in (False, True) if self.acquisition == 'free' else (False,):
                a, b = (start+window, start+cycle) if returning else (start, start+window)
                if self.acquisition == 'free':
                    a, b = max(a, self.time-duration), min(b, self.time)
                if b <= a:
                    continue
                count = max(16, int((b-a)*self._frequency*32)+2)
                times = np.linspace(a, b, count)
                s = (1-(times-start-window)/reset) if returning else (times-start)/window
                ramp = -1+2*s+self.curvature*4*s*(1-s)
                x = 4*np.sin(2*np.pi*self._frequency*times+self._source_phase)
                y = self.setting.vpp*self.gain*ramp
                points = np.column_stack(((x+5)/10*(self.size-1), (5-y)/10*(self.size-1)))
                layer = Image.new('L', (self.size, self.size))
                ImageDraw.Draw(layer).line(points.reshape(-1).tolist(), fill=255, width=1)
                # Saturation/trace accumulation intentionally preserves overlap.
                np.maximum(layers, np.asarray(layer, np.float32), out=layers)
        pixels = layers*self.brightness
        if self.grid:
            for index in range(11):
                p = round(index/10*(self.size-1))
                pixels[p, :] = np.maximum(pixels[p, :], self.grid)
                pixels[:, p] = np.maximum(pixels[:, p], self.grid)
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
        if self.blur:
            image = image.filter(ImageFilter.GaussianBlur(self.blur))
        if self.noise:
            pixels = np.asarray(image, np.float32)+self.noise_rng.normal(0, self.noise, image.size[::-1])
            image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
        frame = CameraFrame(image, self.time, self.setting)
        self.last_frame = frame
        return frame
