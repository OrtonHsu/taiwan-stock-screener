"""Offline tests for V2.3 resume orchestration; no network calls."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v23_resume_batches import (
    groups_of_five, main, save_summary_atomic, select_calendar,
    summary_file, verify_complete_batch,
)


def csv_calendar(path, days):
    path.write_text("trade_date,source\n" + "\n".join(d + ",TWSE_FMTQIK" for d in days) + "\n", encoding="utf-8")


DAYS = ("2026-01-16", "2026-01-19", "2026-01-20", "2026-01-21",
        "2026-01-22", "2026-01-23", "2026-01-26", "2026-01-27",
        "2026-01-28", "2026-01-29", "2026-01-30")


class ResumeBatchTests(unittest.TestCase):
    def test_select_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.csv"
            csv_calendar(path, DAYS)
            self.assertEqual(len(select_calendar(path, "2026-01-16", "2026-01-29")), 10)

    def test_groups_five_and_last_partial(self):
        groups = groups_of_five(list(DAYS))
        self.assertEqual([len(x) for x in groups], [5, 5, 1])

    def test_unique_summary_name(self):
        path = summary_file(Path("data"), list(DAYS[:5]))
        self.assertEqual(path.name, "v23_resume_20260116_20260122.csv")

    def test_reject_more_than_five_in_one_batch(self):
        with self.assertRaises(ValueError):
            summary_file(Path("data"), list(DAYS))

    def test_reject_duplicate_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.csv"
            csv_calendar(path, DAYS[:2] + DAYS[:1])
            with self.assertRaises(ValueError):
                select_calendar(path, "2026-01-16", "2026-01-20")

    def test_reject_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.csv"
            csv_calendar(path, DAYS)
            with self.assertRaises(ValueError):
                select_calendar(path, "2020-01-01", "2026-01-20")

    def test_dry_run_no_downloads_or_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            calendar = Path(tmp) / "calendar.csv"
            output = Path(tmp) / "samples"
            csv_calendar(calendar, DAYS)
            result = main(["--calendar", str(calendar), "--output-dir", str(output),
                           "--start", DAYS[0], "--end", DAYS[-1]])
            self.assertEqual(result, {"previously_completed": 0, "new": 0, "remaining": 3})
            self.assertFalse(output.exists())

    def test_atomic_summary_protects_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.csv"
            save_summary_atomic(path, [{"date": "2026-01-16", "n": 10}])
            contents = path.read_text(encoding="utf-8-sig")
            with self.assertRaises(FileExistsError):
                save_summary_atomic(path, [{"date": "2026-01-17", "n": 11}])
            self.assertEqual(path.read_text(encoding="utf-8-sig"), contents)

    def test_incomplete_summary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.csv"
            path.write_text("trade_date,market,date_validation,four_digit_unique,valid_close_with_volume,missing_close\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_complete_batch(Path(tmp), list(DAYS[:2]), path)

    def test_no_network_called_for_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            calendar = Path(tmp) / "calendar.csv"
            csv_calendar(calendar, DAYS[:3])
            with patch("v23_resume_batches.run_new_batch", side_effect=AssertionError("must not call")):
                main(["--calendar", str(calendar), "--output-dir", str(Path(tmp) / "sample"),
                      "--start", DAYS[0], "--end", DAYS[2]])


if __name__ == "__main__":
    unittest.main()
