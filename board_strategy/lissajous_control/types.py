"""Units and immutable records at the hardware boundary."""
from dataclasses import dataclass, asdict, field
import math
from typing import Any


def finite(*values):
    return all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in values)


@dataclass(frozen=True)
class Signal:
    waveform: str = 'sine'
    frequency_hz: float = 1000.
    vpp: float = 1.
    phase_deg: float = 0.  # Increasing offset ADVANCES sine phase; not lag.
    rise_time_us: float | None = None
    reset_ratio: float = .05  # reset duration / rising duration

    def __post_init__(self):
        if self.waveform not in ('sine', 'ramp') or not finite(self.frequency_hz, self.vpp, self.phase_deg, self.reset_ratio):
            raise ValueError('Invalid signal')
        if not 0 < self.frequency_hz <= 1e7 or not 0 < self.vpp <= 20 or not 0 <= self.phase_deg < 360:
            raise ValueError('Signal values out of range; phase is 0 <= deg < 360')
        if self.waveform == 'ramp':
            if not finite(self.rise_time_us) or self.rise_time_us <= 0 or self.reset_ratio <= 0:
                raise ValueError('Ramp needs rise_time_us and positive reset_ratio')
            expected = 1e6 / (self.rise_time_us * (1 + self.reset_ratio))
            if abs(self.frequency_hz / expected - 1) > .0005:
                raise ValueError('Ramp frequency and rising duration disagree')


@dataclass(frozen=True)
class Target:
    mode: str = 'circle'
    height_div: int = 8

    def __post_init__(self):
        if self.mode not in ('diagonal', 'circle', 'infinity') or self.height_div not in (2, 4, 6, 8):
            raise ValueError('Target: diagonal/circle/infinity; height_div: 2/4/6/8')

    @property
    def frequency_ratio(self):
        return 2 if self.mode == 'infinity' else 1


@dataclass(frozen=True)
class Capabilities:
    ramp_supported: bool = False
    continuous_phase: bool = False
    max_frequency_hz: float = 200000.
    max_vpp: float = 5.

    def __post_init__(self):
        if not finite(self.max_frequency_hz, self.max_vpp) or min(self.max_frequency_hz, self.max_vpp) <= 0:
            raise ValueError('Invalid hardware limits')


@dataclass(frozen=True)
class Options:
    settle_s: float = .08
    pair_interval_s: float = .04
    frame_max_age_s: float = .3
    max_hold_gap_s: float = .2
    hold_seconds: float = 5.
    control_limit_s: float = 10.
    amplitude_tolerance_div: float = .2
    shape_tolerance_div: float = .2  # Engineering threshold, not an exam-specified static metric.
    drift_limit_div: float = 1.
    x_reference_div: float = 8.
    max_phase_probe_gap_s: float = .25
    max_trim_hz: float = 5.

    def __post_init__(self):
        values = asdict(self)
        if not finite(*values.values()) or min(values.values()) <= 0:
            raise ValueError('Options must be finite and positive')
        if self.hold_seconds < 5 or self.amplitude_tolerance_div > .2 or self.drift_limit_div > 1:
            raise ValueError('Do not relax the required amplitude/drift/5-second hold limits')


@dataclass(frozen=True)
class Frame:
    image: Any  # Clean 384x384 grayscale PIL image or uint8 ndarray.
    signal: Signal  # ACTUAL applied DAC output; never the unknown source signal.
    command_id: str
    frame_id: int  # Strictly increasing capture sequence; not HTTP request sequence.
    captured_at_s: float  # Capture/exposure midpoint, board time.monotonic clock.
    calibration_id: str


@dataclass(frozen=True)
class Command:
    command_id: str
    signal: Signal
    reason: str
    settle_s: float
    reset_accumulator: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    status: str
    stage: str
    reason: str
    command: Command | None = None
    capture_after_s: float | None = None
    success: bool = False
    telemetry: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
