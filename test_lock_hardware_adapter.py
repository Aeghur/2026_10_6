import unittest
from unittest.mock import Mock

from lock_board.lock_control import Signal
from lock_hardware_test import apply_lock_signal
from msp_wave_link import MSPM0LinkError
from protocol import TargetAck


class AdapterTests(unittest.TestCase):
    def test_absolute_trim_is_not_calibrated_again(self):
        link = Mock()
        link.set_waveform.return_value = TargetAck(30000217, 27000, 0, 17)
        actual, when, timing = apply_lock_signal(link, Signal(30000.217, 4., 90.))
        link.set_waveform.assert_called_once_with('sine', 30000.217, 270., 8)
        self.assertEqual(actual, Signal(30000.217, 4., 90.))
        self.assertLessEqual(timing['send_s'], when)
        self.assertLessEqual(when, timing['ack_received_s'])

    def test_wrong_frequency_ack_is_rejected(self):
        link = Mock()
        link.set_waveform.return_value = TargetAck(30000000, 0, 0, 17)
        with self.assertRaises(MSPM0LinkError):
            apply_lock_signal(link, Signal(30000.217, 4.))

    def test_actual_quantized_phase_is_returned(self):
        link = Mock()
        link.set_waveform.return_value = TargetAck(30000217, 1224, 2, 17)
        actual, _, _ = apply_lock_signal(link, Signal(30000.217, 2., 347.756))
        self.assertEqual(actual, Signal(30000.217, 2., 347.76))
        with self.assertRaises(ValueError):
            apply_lock_signal(link, Signal(30000.217, 2.5))


if __name__ == '__main__':
    unittest.main()
