import struct
import unittest

from msp_wave_link import (
    MSPM0CommandError,
    MSPM0LinkError,
    MSPM0WaveLink,
    encode_target_payload,
    encode_waveform_payload,
)
from protocol import (
    FrameDecoder,
    TYPE_ERROR,
    TYPE_PC_SET_TARGET,
    TYPE_PC_SET_WAVEFORM,
    TYPE_PC_TARGET_ACK,
    encode_frame,
)


class FakeSerial:
    def __init__(self, response_type=TYPE_PC_TARGET_ACK, error_code=0):
        self.response_type = response_type
        self.error_code = error_code
        self.rx = bytearray()
        self.written = b""

    @property
    def in_waiting(self):
        return len(self.rx)

    def reset_input_buffer(self):
        self.rx.clear()

    def write(self, data):
        self.written = data
        request = FrameDecoder().feed(data)[0]
        if self.response_type == TYPE_PC_TARGET_ACK:
            if request.frame_type == TYPE_PC_SET_WAVEFORM:
                waveform, frequency, phase, amplitude = struct.unpack_from(
                    "<BIHB", request.payload
                )
                decay_microseconds = (struct.unpack_from("<H", request.payload, 8)[0]
                                      if len(request.payload) == 10 else 0)
            else:
                waveform = 0
                decay_microseconds = 0
                frequency, phase, amplitude = struct.unpack(
                    "<IHB", request.payload
                )
            mode = 0x11 | (0x20 if waveform in (1, 2) else 0) | (0x40 if waveform in (2, 3) else 0)
            payload = struct.pack(
                "<IHBB", frequency, phase, amplitude, mode
            )
            if waveform == 3:
                payload += struct.pack("<H", decay_microseconds)
        else:
            payload = struct.pack("<H", self.error_code)
        self.rx.extend(encode_frame(self.response_type, request.sequence, payload))
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        data = bytes(self.rx[:size])
        del self.rx[:size]
        return data


class WaveLinkTests(unittest.TestCase):
    def test_payload_engineering_units(self):
        payload = encode_target_payload(10_000.0, 90.0, 6)
        self.assertEqual(struct.unpack("<IHB", payload), (10_000_000, 9000, 3))

    def test_target_round_trip(self):
        serial_port = FakeSerial()
        link = MSPM0WaveLink(serial_port=serial_port)
        ack = link.set_target(12_345.0, 45.5, 4)
        request = FrameDecoder().feed(serial_port.written)[0]
        self.assertEqual(request.frame_type, TYPE_PC_SET_TARGET)
        self.assertEqual(ack.frequency_millihz, 12_345_000)
        self.assertEqual(ack.phase_centidegrees, 4550)
        self.assertEqual(ack.amplitude_code, 2)

    def test_sawtooth_round_trip(self):
        serial_port = FakeSerial()
        link = MSPM0WaveLink(serial_port=serial_port)
        ack = link.set_sawtooth(6000.0, 30.0, 6)
        request = FrameDecoder().feed(serial_port.written)[0]
        self.assertEqual(request.frame_type, TYPE_PC_SET_WAVEFORM)
        self.assertEqual(
            struct.unpack("<BIHB", request.payload),
            (1, 6_000_000, 3000, 3),
        )
        self.assertEqual(ack.mode & 0x20, 0x20)

    def test_unconfirmed_waveform_only_writes_command(self):
        serial_port = FakeSerial()
        link = MSPM0WaveLink(serial_port=serial_port)
        self.assertIsNone(link.send_waveform_unconfirmed("sine", 10000.001, 0, 8))
        request = FrameDecoder().feed(serial_port.written)[0]
        self.assertEqual(request.frame_type, TYPE_PC_SET_WAVEFORM)
        self.assertEqual(struct.unpack("<BIHB", request.payload),
                         (0, 10_000_001, 0, 0))

    def test_falling_sawtooth_round_trip(self):
        serial_port = FakeSerial()
        ack = MSPM0WaveLink(serial_port=serial_port).set_sawtooth(
            544.218, 0.0, 4, direction="falling"
        )
        request = FrameDecoder().feed(serial_port.written)[0]
        self.assertEqual(struct.unpack("<BIHB", request.payload),
                         (2, 544218, 0, 2))
        self.assertEqual(ack.mode & 0x60, 0x60)

    def test_decay_pulse_round_trip(self):
        serial_port = FakeSerial()
        ack = MSPM0WaveLink(serial_port=serial_port).set_waveform(
            "decay_pulse", 100.0, 0.0, 8, decay_microseconds=750
        )
        request = FrameDecoder().feed(serial_port.written)[0]
        self.assertEqual(struct.unpack("<BIHBH", request.payload),
                         (3, 100000, 0, 0, 750))
        self.assertEqual(ack.mode & 0x60, 0x40)
        self.assertEqual(ack.decay_microseconds, 750)
        with self.assertRaises(ValueError):
            encode_waveform_payload("decay_pulse", 101.0, 0.0, 8)
        for invalid in (0, 10000):
            with self.assertRaises(ValueError):
                encode_waveform_payload(
                    "decay_pulse", 100.0, 0.0, 8,
                    decay_microseconds=invalid)

    def test_direction_validation(self):
        for waveform, direction in (("sine", "falling"), ("sawtooth", "bad")):
            with self.assertRaises(ValueError):
                encode_waveform_payload(waveform, 1000, 0, 4,
                                        direction=direction)

    def test_direction_ack_mismatch(self):
        class WrongDirectionSerial(FakeSerial):
            def write(self, data):
                result = super().write(data)
                frame = FrameDecoder().feed(bytes(self.rx))[0]
                payload = frame.payload[:-1] + bytes((frame.payload[-1] ^ 0x40,))
                self.rx = bytearray(encode_frame(frame.frame_type,
                                                frame.sequence, payload))
                return result
        with self.assertRaises(MSPM0LinkError):
            MSPM0WaveLink(serial_port=WrongDirectionSerial()).set_sawtooth(
                1000, direction="falling"
            )

    def test_waveform_payload_validation(self):
        self.assertEqual(
            struct.unpack(
                "<BIHB", encode_waveform_payload(
                    "sawtooth", 1000.0, 0.0, 8
                )
            ),
            (1, 1_000_000, 0, 0),
        )
        with self.assertRaises(ValueError):
            encode_waveform_payload("square", 1000.0, 0.0, 8)

    def test_msp_error_is_raised(self):
        link = MSPM0WaveLink(
            serial_port=FakeSerial(TYPE_ERROR, error_code=1)
        )
        with self.assertRaises(MSPM0CommandError) as caught:
            link.set_target(1000.0)
        self.assertEqual(caught.exception.code, 1)

    def test_rejects_bad_parameters(self):
        with self.assertRaises(ValueError):
            encode_target_payload(100.0, 0.0, 8)
        with self.assertRaises(ValueError):
            encode_target_payload(1000.0, 360.0, 8)
        with self.assertRaises(ValueError):
            encode_target_payload(1000.0, 0.0, 5)

    def test_free_run_lower_boundary_and_fractional_probes(self):
        for hz in (250.0, 272.108844, 544.217687, 544.218, 1177.232327,
                   4270.766603, 5442.176871, 8658.008658, 13046.314416,
                   13698.630137, 9090.909091, 5714.285714, 4484.304933,
                   1236.093943, 571.428571, 285.714286,
                   200000.0, 200005.0, 210000.0):
            payload = encode_target_payload(hz, 0.0, 4)
            mhz, phase, amplitude = struct.unpack("<IHB", payload)
            self.assertEqual(mhz, round(hz * 1000))
            step = round(mhz * 2**38 / 25_000_000_000)
            actual = round(step * 25_000_000_000 / 2**38) / 1000
            self.assertLessEqual(abs(actual - hz), max(0.002, hz * 0.5e-6) + 1e-10)
            self.assertEqual((phase, amplitude), (0, 2))
        for hz in (249.999, 210000.001):
            with self.assertRaises(ValueError):
                encode_target_payload(hz, 0.0, 4)

    def test_free_run_frequency_upper_boundary(self):
        payload = encode_target_payload(210_000.0, 0.0, 8)
        self.assertEqual(
            struct.unpack("<IHB", payload), (210_000_000, 0, 0)
        )
        with self.assertRaises(ValueError):
            encode_target_payload(210_000.001, 0.0, 8)


if __name__ == "__main__":
    unittest.main()
