import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from frequency_calibration import (
    CalibrationRecord,
    append_record,
    parse_frequency,
    stepped_frequency,
)


class FrequencyCalibrationTests(unittest.TestCase):
    def test_frequency_is_quantized_to_protocol_resolution(self) -> None:
        self.assertEqual(parse_frequency("10000.0005"), Decimal("10000.001"))
        self.assertEqual(parse_frequency("10000.0004"), Decimal("10000.000"))

    def test_step_does_not_accumulate_float_error(self) -> None:
        frequency = Decimal("10000.000")
        for _ in range(10):
            frequency = stepped_frequency(frequency, Decimal("0.001"), 1)
        self.assertEqual(frequency, Decimal("10000.010"))
        self.assertEqual(
            stepped_frequency(frequency, Decimal("0.010"), -1),
            Decimal("10000.000"),
        )

    def test_frequency_range_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "250.000"):
            parse_frequency("249.999")
        with self.assertRaises(ValueError):
            stepped_frequency(Decimal("210000.000"), Decimal("0.001"), 1)

    def test_record_error_and_csv_append(self) -> None:
        record = CalibrationRecord(
            confirmed_at="2026-11-07T15:00:00+08:00",
            source_frequency_hz=Decimal("10000.000"),
            calibrated_frequency_hz=Decimal("10000.010"),
            phase_degrees=Decimal("0.00"),
            amplitude=8,
        )
        self.assertEqual(record.frequency_error_hz, Decimal("0.010"))
        self.assertEqual(record.frequency_error_ppm, Decimal("1.000"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "calibration.csv"
            append_record(path, record)
            append_record(path, record)
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["frequency_error_hz"], "+0.010")
        self.assertEqual(rows[0]["frequency_error_ppm"], "+1.000000")


if __name__ == "__main__":
    unittest.main()
