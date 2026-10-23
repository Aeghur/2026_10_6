import struct
import unittest

from msp_wave_link import (
    MSPM0CommandError,
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
                waveform, frequency, phase, amplitude = struct.unpack(
                    "<BIHB", request.payload
                )
            else:
                waveform = 0
                frequency, phase, amplitude = struct.unpack(
                    "<IHB", request.payload
                )
            mode = 0x11 | (0x20 if waveform == 1 else 0)
            payload = struct.pack(
                "<IHBB", frequency, phase, amplitude, mode
            )
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


if __name__ == "__main__":
    unittest.main()
