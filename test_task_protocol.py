import unittest

from protocol import FrameDecoder
from task_protocol import (
    MODE_INFINITY,
    STATE_READY,
    STATE_SUCCEEDED,
    TYPE_TASK_QUERY,
    TYPE_TASK_START,
    TYPE_TASK_STATUS,
    decode_task_query,
    decode_task_start,
    decode_task_status,
    encode_task_query,
    encode_task_start,
)


class TaskProtocolTests(unittest.TestCase):
    def decode_one(self, encoded: bytes):
        frames = FrameDecoder().feed(encoded)
        self.assertEqual(len(frames), 1)
        return frames[0]

    def test_service_query_matches_contract_vector(self) -> None:
        encoded = encode_task_query(5, 0, 0)
        self.assertEqual(
            encoded.hex(" ").upper(),
            "A5 5A 21 05 09 00 01 00 00 00 00 00 00 00 00 42 DF",
        )
        frame = self.decode_one(encoded)
        self.assertEqual(frame.frame_type, TYPE_TASK_QUERY)
        self.assertEqual(decode_task_query(frame).request_id, 0)

    def test_infinity_start_matches_contract_vector(self) -> None:
        encoded = encode_task_start(7, MODE_INFINITY, 42, 0x12345678)
        self.assertEqual(
            encoded.hex(" ").upper(),
            "A5 5A 20 07 0A 00 01 03 2A 00 00 00 78 56 34 12 00 9B",
        )
        frame = self.decode_one(encoded)
        self.assertEqual(frame.frame_type, TYPE_TASK_START)
        self.assertEqual(decode_task_start(frame).request_id, 42)

    def test_ready_status_contract_vector(self) -> None:
        encoded = bytes.fromhex(
            "A5 5A A0 00 18 00 01 00 00 00 00 00 01 00 00 00 00 00 "
            "00 00 00 00 FF FF FF FF 78 56 34 12 F9 7A"
        )
        frame = self.decode_one(encoded)
        self.assertEqual(frame.frame_type, TYPE_TASK_STATUS)
        status = decode_task_status(frame)
        self.assertEqual(status.state, STATE_READY)
        self.assertEqual(status.session_id, 0x12345678)

    def test_success_status_contract_vector(self) -> None:
        encoded = bytes.fromhex(
            "A5 5A A0 07 18 00 01 03 2A 00 00 00 04 00 E0 2E 00 00 "
            "88 13 00 00 58 1B 00 00 78 56 34 12 83 F4"
        )
        status = decode_task_status(self.decode_one(encoded))
        self.assertEqual(status.state, STATE_SUCCEEDED)
        self.assertEqual(status.elapsed_ms, 12000)
        self.assertEqual(status.stable_ms, 5000)
        self.assertEqual(status.control_ms, 7000)

    def test_invalid_start_and_query_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_task_start(1, 0, 1, 1)
        with self.assertRaises(ValueError):
            encode_task_start(1, 1, 0, 1)
        with self.assertRaises(ValueError):
            encode_task_query(1, 5, 0)
        self.assertEqual(
            decode_task_query(self.decode_one(encode_task_query(1, 0, 9))).session_id,
            9,
        )


if __name__ == "__main__":
    unittest.main()
