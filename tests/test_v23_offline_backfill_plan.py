"""Pure offline tests: no network calls or database imports."""
import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from v23_offline_backfill_plan import assess, build_plan, main, parse_day, read_csv, save_plan


class OfflineBackfillPlanTests(unittest.TestCase):
    def setUp(self):
        self.calendar = [{"trade_date": "2026-01-02", "source": "TWSE_FMTQIK"},
                         {"trade_date": "2026-03-02", "source": "TWSE_FMTQIK"},
                         {"trade_date": "2026-07-01", "source": "TWSE_FMTQIK"}]
        self.coverage = [
            {"trade_date": "2026-01-02", "twse_unique_codes": "0", "tpex_unique_codes": "0", "has_both_markets": "0"},
            {"trade_date": "2026-03-02", "twse_unique_codes": "473", "tpex_unique_codes": "857", "has_both_markets": "1"},
            {"trade_date": "2026-07-01", "twse_unique_codes": "1084", "tpex_unique_codes": "872", "has_both_markets": "1"},
        ]

    def test_missing_both(self):
        self.assertEqual(assess(0, 0), ("MISSING_BOTH", "P0"))

    def test_partial_twse(self):
        self.assertEqual(assess(473, 857)[0], "TWSE_UNDER_PROVISIONAL_THRESHOLD")

    def test_provisional_both_present(self):
        self.assertEqual(assess(1084, 872)[1], "P2")

    def test_partial_tpex(self):
        self.assertEqual(assess(1098, 600)[1], "P1")

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            assess(-1, 800)

    def test_period_filter(self):
        plan = build_plan(self.calendar, self.coverage, date(2026, 1, 1), date(2026, 6, 30))
        self.assertEqual(len(plan), 2)
        self.assertEqual([r["priority"] for r in plan], ["P0", "P1"])

    def test_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(self.calendar, self.coverage[:-1], date(2026, 1, 1), date(2026, 6, 30))

    def test_inconsistent_flag_rejected(self):
        bad = [dict(x) for x in self.coverage]
        bad[1]["has_both_markets"] = "0"
        with self.assertRaises(ValueError):
            build_plan(self.calendar, bad, date(2026, 1, 1), date(2026, 6, 30))

    def test_date_out_of_range(self):
        with self.assertRaises(ValueError):
            parse_day("2020-12-31")

    def test_bad_period(self):
        with self.assertRaises(ValueError):
            build_plan(self.calendar, self.coverage, date(2026, 6, 1), date(2026, 1, 1))

    def test_csv_duplicate_dates_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dates.csv"
            p.write_text("trade_date,source\n2026-01-02,TWSE\n2026-01-02,TWSE\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_csv(p, {"trade_date", "source"})

    def test_save_does_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "original.csv"
            with self.assertRaises(ValueError):
                save_plan([{"trade_date": "2026-01-02"}], p, (p, Path(tmp) / "other.csv"))

    def test_end_to_end_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cal = Path(tmp) / "calendar.csv"
            cov = Path(tmp) / "coverage.csv"
            out = Path(tmp) / "plan.csv"
            for path, rows in ((cal, self.calendar), (cov, self.coverage)):
                with path.open("w", newline="", encoding="utf-8-sig") as fp:
                    writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
            summary = main(["--calendar", str(cal), "--coverage", str(cov),
                            "--start", "2026-01-01", "--end", "2026-06-30",
                            "--output", str(out)])
            self.assertEqual(summary["MISSING_BOTH"], 1)
            self.assertEqual(summary["TWSE_UNDER_PROVISIONAL_THRESHOLD"], 1)
            self.assertEqual(len(read_csv(out, {"trade_date", "status"})), 2)


if __name__ == "__main__":
    unittest.main()
