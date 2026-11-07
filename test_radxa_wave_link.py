import io
import json
from types import SimpleNamespace
import unittest

from msp_wave_link import MSPM0LinkError
from radxa_wave_link import MARKER, RadxaWaveLink


class NetworkLinkTests(unittest.TestCase):
    def make_link(self, response):
        link = RadxaWaveLink(timeout=0.01)
        link.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
        link.messages.put(MARKER + json.dumps(response))
        return link

    def test_waveform_forwarded_with_fractional_units(self):
        link = self.make_link({'ack': dict(frequency_millihz=544218,
            phase_centidegrees=9000, amplitude_code=2, mode=0x79)})
        ack = link.set_waveform('sawtooth', 544.218, 90, 4, direction='falling')
        self.assertEqual(ack.frequency_millihz, 544218)
        self.assertEqual(json.loads(link.process.stdin.getvalue()), dict(
            waveform='sawtooth', frequency_hz=544.218, phase_degrees=90,
            amplitude=4, direction='falling', decay_microseconds=1000,
            wait_for_confirmation=True))

    def test_unconfirmed_mode_reports_uart_write_not_fpga_ack(self):
        link = RadxaWaveLink(timeout=0.01, wait_for_confirmation=False)
        link.process = SimpleNamespace(stdin=io.StringIO(), poll=lambda: None)
        link.messages.put(MARKER + json.dumps({'sent': True}))
        ack = link.set_waveform('sine', 10000.001, 0, 8)
        request = json.loads(link.process.stdin.getvalue())
        self.assertFalse(request['wait_for_confirmation'])
        self.assertEqual(ack.frequency_millihz, 10_000_001)

    def test_invalid_target_not_sent(self):
        link = self.make_link({})
        with self.assertRaises(ValueError):
            link.set_waveform('sine', 210001)
        self.assertEqual(link.process.stdin.getvalue(), '')

    def test_decay_pulse_forwarded(self):
        link = self.make_link({'ack': dict(frequency_millihz=100000,
            phase_centidegrees=0, amplitude_code=0, mode=0x51,
            decay_microseconds=625)})
        ack = link.set_waveform(
            'decay_pulse', 100.0, decay_microseconds=625)
        self.assertEqual(ack.mode & 0x60, 0x40)
        self.assertEqual(ack.decay_microseconds, 625)
        self.assertEqual(
            json.loads(link.process.stdin.getvalue())["decay_microseconds"],
            625)

    def test_direction_mismatch_rejected(self):
        link = self.make_link({'ack': dict(frequency_millihz=10000000,
            phase_centidegrees=0, amplitude_code=0, mode=0x31)})
        with self.assertRaises(MSPM0LinkError):
            link.set_waveform('sawtooth', 10000, direction='falling')

    def test_remote_error_not_success(self):
        link = self.make_link({'error': 'FPGA确认超时'})
        with self.assertRaisesRegex(MSPM0LinkError, 'FPGA确认超时'):
            link.set_waveform('sine', 10000)

    def test_ssh_exit_diagnostic(self):
        link = RadxaWaveLink(timeout=0.01)
        link.messages.put('Permission denied (publickey).')
        link.messages.put(None)
        with self.assertRaisesRegex(MSPM0LinkError, 'Permission denied'):
            link._receive()

    def test_network_timeout(self):
        with self.assertRaisesRegex(MSPM0LinkError, '超时'):
            RadxaWaveLink(timeout=0.001)._receive()


if __name__ == '__main__':
    unittest.main()
