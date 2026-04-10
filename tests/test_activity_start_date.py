"""Tests for ACTIVITY_START_DATE → epoch parsing."""

import unittest
from datetime import datetime, timezone

from athletic_elf.config import parse_activity_start_epoch


class TestParseActivityStartEpoch(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertIsNone(parse_activity_start_epoch(None))
        self.assertIsNone(parse_activity_start_epoch(""))
        self.assertIsNone(parse_activity_start_epoch("   "))

    def test_z_suffix_utc(self):
        epoch = parse_activity_start_epoch("2025-06-01T00:00:00Z")
        self.assertEqual(
            epoch,
            int(datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
        )

    def test_explicit_offset(self):
        epoch = parse_activity_start_epoch("2025-06-01T00:00:00+00:00")
        self.assertEqual(
            epoch,
            int(datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
        )

    def test_date_only_midnight_utc(self):
        epoch = parse_activity_start_epoch("2025-06-15")
        self.assertEqual(
            epoch,
            int(datetime(2025, 6, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
        )

    def test_naive_datetime_is_utc(self):
        epoch = parse_activity_start_epoch("2025-01-02T12:30:00")
        self.assertEqual(
            epoch,
            int(datetime(2025, 1, 2, 12, 30, 0, tzinfo=timezone.utc).timestamp()),
        )

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_activity_start_epoch("not-a-date"))


if __name__ == "__main__":
    unittest.main()
