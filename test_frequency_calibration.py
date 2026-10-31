from decimal import Decimal
import unittest
from unittest.mock import Mock

from frequency_calibration import calibrate_output_frequency, waveform_frequency, set_calibrated_waveform
from msp_wave_link import MSPM0LinkError
from protocol import TargetAck
from phase_handoff import start_after_frequency
from phase_board.phase_control import Signal


class CalibrationTests(unittest.TestCase):
    def test_reference_points(self):
        for target, command in [('250', '250.002'), ('1000', '1000.007'),
            ('5000', '5000.036'), ('10000', '10000.072'), ('50000', '50000.360'),
            ('61200', '61200.441'), ('100000', '100000.723'), ('120000', '120000.869'),
            ('150000', '150001.088'), ('180000', '180001.308'),
            ('205000', '205001.492'), ('209998.471', '210000.000')]:
            with self.subTest(target=target):
                self.assertEqual(calibrate_output_frequency(target), Decimal(command))

    def test_bounds_and_nonfinite(self):
        for invalid in ['249.999', '209998.472', 'NaN', 'Infinity', '-Infinity']:
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                calibrate_output_frequency(invalid)
        self.assertEqual(calibrate_output_frequency('1000.0005'), Decimal('1000.008'))

    def test_double_then_calibrate(self):
        setting = waveform_frequency('sine', 100000 * 2)
        self.assertEqual(setting.command_frequency_hz, Decimal('200001.455'))
        self.assertNotEqual(setting.command_frequency_hz, calibrate_output_frequency(100000) * 2)

    def test_ack_and_logs(self):
        link = Mock()
        link.set_waveform.return_value = TargetAck(61200441, 9000, 0, 17)
        ack, setting = set_calibrated_waveform(link, 'sine', 61200, 90, 8)
        link.set_waveform.assert_called_once_with('sine', 61200.441, 90, 8)
        self.assertEqual(setting.to_dict()['target_frequency_hz'], 61200)
        self.assertEqual(setting.to_dict()['command_frequency_hz'], 61200.441)
        self.assertEqual(setting.to_dict()['frequency_correction_hz'], .441)
        link.set_waveform.return_value = TargetAck(61200000, 9000, 0, 17)
        with self.assertRaises(MSPM0LinkError):
            set_calibrated_waveform(link, 'sine', 61200)

    def test_bypass_and_waveform_options(self):
        link = Mock()
        link.set_waveform.return_value = TargetAck(100000, 0, 0, 81, 1000)
        _, setting = set_calibrated_waveform(link, 'decay_pulse', 100, decay_microseconds=1000)
        self.assertFalse(setting.calibration_enabled)
        link.set_waveform.assert_called_once_with('decay_pulse', 100., 0., 8, decay_microseconds=1000)
        self.assertEqual(waveform_frequency('sine', 61200, enabled=False).command_frequency_hz, Decimal('61200.000'))
        self.assertEqual(waveform_frequency('sawtooth', 61200).command_frequency_hz, Decimal('61200.441'))
        link.reset_mock()
        link.set_waveform.return_value = TargetAck(61200000, 0, 0, 17)
        set_calibrated_waveform(link, 'sine', 61200, enabled=False)
        link.set_waveform.assert_called_once_with('sine', 61200., 0., 8)
        link.reset_mock()
        link.set_waveform.return_value = TargetAck(61200441, 1234, 3, 113)
        set_calibrated_waveform(link, 'sawtooth', 61200, 12.34, 6, direction='falling')
        link.set_waveform.assert_called_once_with('sawtooth', 61200.441, 12.34, 6, direction='falling')

    def test_phase_handoff_keeps_wire_frequency(self):
        signal = Signal(61200.441, 4, 0)
        controller, decision = start_after_frequency(61200, actual_signal=signal,
            command_id='ack', applied_at_s=1., continuous_phase=True,
            output_command_frequency_hz=61200.441)
        self.assertEqual(decision.status, 'capture')
        command = controller._issue(90, 2., 'probe').command
        self.assertEqual(command.signal.frequency_hz, 61200.441)
        with self.assertRaises(ValueError):
            controller.applied(command.command_id, Signal(61200, 4, 90), 2.1)


if __name__ == '__main__':
    unittest.main()
