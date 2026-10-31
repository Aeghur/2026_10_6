"""Host-side free-running DDS calibration; raw UART protocol stays unchanged."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from msp_wave_link import MSPM0LinkError

FREQUENCY_QUANTUM_HZ = Decimal('0.001')
FREQUENCY_CAL_A = Decimal('0.00000718530722')
FREQUENCY_CAL_B = Decimal('0.000000000000457648439')
MAX_CALIBRATED_TARGET_HZ = Decimal('209998.471')


def quantized_frequency(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Frequency must be finite')
        return number.quantize(FREQUENCY_QUANTUM_HZ, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError('Invalid frequency') from exc


def calibrate_output_frequency(frequency_hz):
    target = quantized_frequency(frequency_hz)
    if not Decimal('250') <= target <= MAX_CALIBRATED_TARGET_HZ:
        raise ValueError('启用频率标定后，目标频率必须位于250.000～209998.471 Hz')
    command = (target + FREQUENCY_CAL_A * target + FREQUENCY_CAL_B * target * target).quantize(
        FREQUENCY_QUANTUM_HZ, rounding=ROUND_HALF_UP)
    if command > Decimal('210000'):
        raise ValueError('标定后的下发频率超过210000.000 Hz')
    return command


@dataclass(frozen=True)
class FrequencySetting:
    target_frequency_hz: Decimal
    command_frequency_hz: Decimal
    calibration_enabled: bool

    def to_dict(self):
        return dict(target_frequency_hz=float(self.target_frequency_hz),
                    command_frequency_hz=float(self.command_frequency_hz),
                    frequency_correction_hz=float(self.command_frequency_hz-self.target_frequency_hz),
                    calibration_enabled=self.calibration_enabled,
                    calibration_extrapolated=self.calibration_enabled and self.target_frequency_hz > 205000)


def waveform_frequency(waveform, target_frequency_hz, *, enabled=True):
    if waveform not in ('sine', 'sawtooth', 'decay_pulse'):
        raise ValueError('Unsupported free-running waveform')
    target = quantized_frequency(target_frequency_hz)
    if waveform == 'decay_pulse':
        if Decimal(str(target_frequency_hz)) != 100:
            raise ValueError('decay_pulse frequency must be exactly 100 Hz')
        return FrequencySetting(target, target, False)
    if not Decimal('250') <= target <= Decimal('210000'):
        raise ValueError('Frequency outside UART range')
    return FrequencySetting(target, calibrate_output_frequency(target) if enabled else target, bool(enabled))


def set_calibrated_waveform(link, waveform, target_frequency_hz, phase_degrees=0., amplitude=8,
                            *, enabled=True, **kwargs):
    """Pass a nominal target exactly once, validate ACK in command-frequency units."""
    setting = waveform_frequency(waveform, target_frequency_hz, enabled=enabled)
    ack = link.set_waveform(waveform, float(setting.command_frequency_hz), phase_degrees, amplitude, **kwargs)
    if ack.frequency_millihz != int(setting.command_frequency_hz * 1000):
        raise MSPM0LinkError('ACK frequency differs from calibrated command frequency')
    return ack, setting
