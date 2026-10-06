import struct
import unittest
import numpy as np

from protocol import (
    FrameDecoder,
    TYPE_ERROR,
    TYPE_PC_CAPTURE,
    TYPE_PC_SET_PHASE,
    TYPE_PC_SET_MODE,
    TYPE_PC_RESULT,
    crc16_ccitt,
    decode_error,
    decode_result,
    encode_frame,
)


class ProtocolTests(unittest.TestCase):
    def test_known_crc_vector(self) -> None:
        self.assertEqual(crc16_ccitt(b"123456789"), 0x29B1)

    def test_fragmented_frame_and_noise(self) -> None:
        encoded = encode_frame(TYPE_PC_CAPTURE, 7)
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(b"\x00\xA5"), [])
        frames = decoder.feed(encoded[1:4])
        self.assertEqual(frames, [])
        frames = decoder.feed(encoded[4:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].sequence, 7)

    def test_bad_crc_resynchronizes(self) -> None:
        bad = bytearray(encode_frame(TYPE_PC_CAPTURE, 1))
        bad[-1] ^= 1
        good = encode_frame(TYPE_PC_CAPTURE, 2)
        decoder = FrameDecoder()
        frames = decoder.feed(bytes(bad) + good)
        self.assertEqual(decoder.crc_errors, 1)
        self.assertEqual([frame.sequence for frame in frames], [2])

    def test_phase_configuration_layout(self) -> None:
        encoded = encode_frame(
            TYPE_PC_SET_PHASE, 3, (9000).to_bytes(2, "little")
        )
        frame = FrameDecoder().feed(encoded)[0]
        self.assertEqual(frame.frame_type, TYPE_PC_SET_PHASE)
        self.assertEqual(int.from_bytes(frame.payload, "little"), 9000)

    def test_mode_configuration_layout(self) -> None:
        encoded = encode_frame(TYPE_PC_SET_MODE, 4, b"\x01")
        frame = FrameDecoder().feed(encoded)[0]
        self.assertEqual(frame.frame_type, TYPE_PC_SET_MODE)
        self.assertEqual(frame.payload, b"\x01")

    def test_result_layout(self) -> None:
        samples = (2000, 2048, 2100, 2048)
        spectrum = (0, 12, 4)
        payload = struct.pack("<IHHH", 1_562_500, 4, 3, 1)
        for frequency, magnitude in ((10_500_000, 50), (31_500_000, 30), (42_000_000, 20)):
            payload += struct.pack("<IH", frequency, magnitude)
        payload += struct.pack("<4H", *samples)
        payload += struct.pack("<3H", *spectrum)
        frame = encode_frame(TYPE_PC_RESULT, 9, payload)
        decoded = FrameDecoder().feed(frame)[0]
        result = decode_result(decoded.payload)
        self.assertEqual(result.sample_rate, 1_562_500)
        self.assertEqual(result.samples, samples)
        self.assertAlmostEqual(result.peaks[0].frequency_hz, 10_500.0)

    def test_mspm0_error_layout(self) -> None:
        frame = encode_frame(TYPE_ERROR, 4, struct.pack("<H", 3))
        decoded = FrameDecoder().feed(frame)[0]
        self.assertEqual(decode_error(decoded.payload), 3)

    def test_single_tone_frequency_resolution(self) -> None:
        sample_rate = 390_625
        count = 4096
        time_axis = np.arange(count) / sample_rate
        expected = 10_537.0
        signal = 60 * np.sin(2 * np.pi * expected * time_axis)
        magnitude = np.abs(np.fft.rfft(signal * np.hanning(count)))
        candidates = [
            index
            for index in range(10, 1050)
            if magnitude[index] >= magnitude[index - 1]
            and magnitude[index] > magnitude[index + 1]
        ]
        index = max(candidates, key=lambda item: magnitude[item])
        left, center, right = magnitude[index - 1 : index + 2]
        delta = 0.5 * (left - right) / (left - 2 * center + right)
        measured = (index + delta) * sample_rate / count
        self.assertAlmostEqual(measured, expected, delta=10.0)


if __name__ == "__main__":
    unittest.main()
