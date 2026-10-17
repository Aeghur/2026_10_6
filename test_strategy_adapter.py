"""Hardware-boundary tests for the collaborator strategy adapter."""

import unittest

from lissajous_control import Signal

from strategy_control import BoardAdapter


class FakeLink:
    def __init__(self):
        self.calls = []

    def set_waveform(self, *args):
        self.calls.append(args)


class StrategyAdapterTests(unittest.TestCase):
    def adapter(self):
        adapter = BoardAdapter.__new__(BoardAdapter)
        adapter.link = FakeLink()
        adapter.signal = Signal()
        return adapter

    def test_phase_lead_is_converted_to_board_lag(self):
        adapter = self.adapter()
        requested = Signal("sine", 12_000.0, 2.0, 90.0)
        actual = adapter.apply(requested)
        self.assertEqual(actual, requested)
        self.assertEqual(adapter.link.calls, [("sine", 12_000.0, 270.0, 4)])

    def test_ramp_maps_to_sawtooth_and_nominal_amplitude_code(self):
        adapter = self.adapter()
        requested = Signal("ramp", 1e6 / (175 * 1.05), 4.0, 0.0, 175.0, 0.05)
        adapter.apply(requested)
        waveform, frequency, lag, amplitude = adapter.link.calls[0]
        self.assertEqual(waveform, "sawtooth")
        self.assertAlmostEqual(frequency, requested.frequency_hz)
        self.assertEqual((lag, amplitude), (0.0, 8))

    def test_unsupported_amplitude_and_frequency_are_rejected(self):
        adapter = self.adapter()
        with self.assertRaises(RuntimeError):
            adapter.apply(Signal("sine", 1000.0, 2.9, 0.0))
        with self.assertRaises(RuntimeError):
            adapter.apply(Signal("sine", 120_000.0, 2.0, 0.0))


if __name__ == "__main__":
    unittest.main()
