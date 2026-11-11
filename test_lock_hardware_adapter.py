import unittest
from unittest.mock import Mock

from lock_board.lock_control import Signal
from lock_hardware_test import apply_lock_signal, arm_hardware_double
from msp_wave_link import MSPM0LinkError, MSPM0TimeoutError
from protocol import TargetAck
from test_double_protocol import sample_ack


class AdapterTests(unittest.TestCase):
    def test_arm_uses_dedicated_method_and_does_not_convert_fpga_clock(self):
        link = Mock()
        link.arm_double_at_falling_zero.return_value = sample_ack(counter=0xf1000000)
        ack, host_ack_time, timing = arm_hardware_double(link)
        link.arm_double_at_falling_zero.assert_called_once_with()
        link.set_waveform.assert_not_called()
        self.assertEqual(timing['ack_type'], 0x94)
        self.assertEqual(timing['sample_counter_clock_domain'], 'fpga')
        self.assertEqual(ack.applied_sample_counter, 0xf1000000)
        self.assertEqual(host_ack_time, timing['ack_received_s'])

    def test_arm_timeout_has_no_retry_or_fallback_command(self):
        link = Mock()
        link.arm_double_at_falling_zero.side_effect = MSPM0TimeoutError('unknown output')
        with self.assertRaises(MSPM0TimeoutError): arm_hardware_double(link)
        link.arm_double_at_falling_zero.assert_called_once_with()
        link.set_waveform.assert_not_called()

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
