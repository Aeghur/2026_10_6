import struct
import unittest
from unittest.mock import Mock, patch
import io

from msp_wave_link import MSPM0WaveLink, MSPM0LinkError, MSPM0TimeoutError, MSPM0CommandError
from protocol import (DoubleAck, FrameDecoder, TYPE_PC_ARM_DOUBLE, TYPE_PC_DOUBLE_ACK,
                      TYPE_PC_TARGET_ACK, TYPE_ERROR, decode_double_ack, encode_frame)


def sample_ack(status=0, counter=1234567):
    old = round(35300.2603*(1 << 38)/25_000_000)
    return DoubleAck(status, counter, old, old*2, 0x7ffffff0)


class Serial:
    def __init__(self, reply=None):
        self.reply = reply
        self.buffer = bytearray()
        self.writes = []
    def reset_input_buffer(self): self.buffer.clear()
    def write(self, data):
        self.writes.append(data)
        frame = FrameDecoder().feed(data)[0]
        if self.reply:
            self.buffer.extend(self.reply(frame))
        return len(data)
    def flush(self): pass
    @property
    def in_waiting(self): return len(self.buffer)
    def read(self, count):
        data = bytes(self.buffer[:min(count, 3)])  # Force fragmented frames.
        del self.buffer[:len(data)]
        return data


def packed(ack):
    return struct.pack('<BIIII', ack.status, ack.applied_sample_counter,
                       ack.old_phase_step, ack.new_phase_step, ack.commit_phase)


class DoubleProtocolTests(unittest.TestCase):
    def test_cli_arm_requires_no_frequency_and_does_not_set_waveform(self):
        import wave_control
        link = Mock()
        link.arm_double_at_falling_zero.return_value = sample_ack()
        context = Mock()
        context.__enter__ = Mock(return_value=link)
        context.__exit__ = Mock(return_value=False)
        with patch.object(wave_control, 'MSPM0WaveLink', return_value=context), \
             patch('sys.argv', ['wave_control.py', '--port', 'test', '--arm-double']), \
             patch('sys.stdout', io.StringIO()):
            self.assertEqual(wave_control.main(), 0)
        link.arm_double_at_falling_zero.assert_called_once_with()
        link.set_waveform.assert_not_called()

    def test_exact_layout_and_q38_frequency_conversion(self):
        ack = sample_ack()
        self.assertEqual(len(packed(ack)), 17)
        self.assertEqual(decode_double_ack(packed(ack)), ack)
        self.assertEqual(ack.old_frequency_millihz, 35300260)
        self.assertEqual(ack.new_frequency_millihz, 70600521)
        self.assertEqual(ack.applied_sample_counter, 1234567)
        with self.assertRaises(ValueError): decode_double_ack(b'\0'*16)

    def test_only_matching_special_ack_succeeds_after_noise(self):
        def reply(frame):
            return (b'noise'+encode_frame(TYPE_PC_TARGET_ACK, frame.sequence, b'\0'*8)
                    +encode_frame(TYPE_PC_DOUBLE_ACK, frame.sequence+1, packed(sample_ack()))
                    +encode_frame(TYPE_PC_DOUBLE_ACK, frame.sequence, packed(sample_ack())))
        serial = Serial(reply)
        link = MSPM0WaveLink(serial_port=serial, timeout=.05).open()
        ack = link.arm_double_at_falling_zero()
        request = FrameDecoder().feed(serial.writes[0])[0]
        self.assertEqual((request.frame_type, request.payload), (TYPE_PC_ARM_DOUBLE, b''))
        self.assertEqual(ack, sample_ack())
        self.assertEqual(len(serial.writes), 1)

    def test_status_ratio_and_payload_errors_fail_without_retry(self):
        good = sample_ack()
        bad_ratio = DoubleAck(0, 10, good.old_phase_step, good.new_phase_step+1, good.commit_phase)
        for payload in (packed(sample_ack(status=1)), packed(bad_ratio), b'\0'*16):
            with self.subTest(payload=payload):
                serial = Serial(lambda frame: encode_frame(TYPE_PC_DOUBLE_ACK, frame.sequence, payload))
                link = MSPM0WaveLink(serial_port=serial, timeout=.01).open()
                with self.assertRaises(MSPM0LinkError): link.arm_double_at_falling_zero()
                self.assertEqual(len(serial.writes), 1)

    def test_error_codes_5_6_7_are_reported_without_retry(self):
        for code in (5, 6, 7):
            serial = Serial(lambda frame: encode_frame(TYPE_ERROR, frame.sequence, struct.pack('<H', code)))
            link = MSPM0WaveLink(serial_port=serial, timeout=.01).open()
            with self.assertRaises(MSPM0CommandError) as caught:
                link.arm_double_at_falling_zero()
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(serial.writes), 1)

    def test_timeout_or_crc_error_does_not_repeat_arm(self):
        def corrupt(frame):
            response = bytearray(encode_frame(TYPE_PC_DOUBLE_ACK, frame.sequence, packed(sample_ack())))
            response[-1] ^= 1
            return response
        for reply in (None, corrupt, lambda frame: encode_frame(TYPE_PC_TARGET_ACK, frame.sequence, b'\0'*8)):
            serial = Serial(reply)
            link = MSPM0WaveLink(serial_port=serial, timeout=.005).open()
            with self.assertRaises(MSPM0TimeoutError): link.arm_double_at_falling_zero()
            self.assertTrue(link.double_state_verification_required)
            with self.assertRaises(MSPM0LinkError): link.arm_double_at_falling_zero()
            self.assertEqual(len(serial.writes), 1)


if __name__ == '__main__': unittest.main()
