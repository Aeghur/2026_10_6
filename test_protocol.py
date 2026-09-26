import struct
import unittest
import numpy as np

from protocol import (
    FrameDecoder,
    TYPE_ERROR,
    TYPE_PC_CAPTURE,
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

    def test_planned_three_tone_frequency_resolution(self) -> None:
        sample_rate = 1_562_500
        count = 4096
        time_axis = np.arange(count) / sample_rate
        signal = (
            60 * np.sin(2 * np.pi * 10_500 * time_axis)
            + 30 * np.sin(2 * np.pi * 31_500 * time_axis)
            + 20 * np.sin(2 * np.pi * 42_000 * time_axis)
        )
        magnitude = np.abs(np.fft.rfft(signal * np.hanning(count)))
        candidates = [
            index
            for index in range(27, 1311)
            if magnitude[index] >= magnitude[index - 1]
            and magnitude[index] > magnitude[index + 1]
        ]
        bins = sorted(candidates, key=lambda index: magnitude[index], reverse=True)[:3]
        measured = []
        for index in bins:
            left, center, right = magnitude[index - 1 : index + 2]
            delta = 0.5 * (left - right) / (left - 2 * center + right)
            measured.append((index + delta) * sample_rate / count)
        measured.sort()
        np.testing.assert_allclose(measured, [10_500, 31_500, 42_000], atol=100)


if __name__ == "__main__":
    unittest.main()
