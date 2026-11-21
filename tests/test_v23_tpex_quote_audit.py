"""V2.3 TPEx audit tests; no network, Supabase, or GitHub access."""
import copy
import tempfile
import unittest
from pathlib import Path
from v23_tpex_quote_audit import audit_payload, field_positions, parse_number, audit_file


FIELDS = [
    "代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價",
    "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)",
    "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價",
    "次日 漲停價", "次日 跌停價",
]


def row(code, close, volume, amount):
    return [code, "NAME", close, "0", "---", "---", "---", "60.97",
            volume, amount, "1", "60", "1", "61", "1", "10000",
            "61.30", "67", "55"]


def sample():
    return {"stat": "ok", "date": "20260102",
            "tables": [
                {"fields": FIELDS,
                 "data": [row("1240", "10.25", "1000", "10250"),
                          row("1259", "---", "380", "23,169"),
                          row("5310", "---", "0", "0"),
                          row("00888", "10", "300", "3000")]},
                {"fields": FIELDS, "data": []},
            ]}


class TPExAuditTests(unittest.TestCase):
    def test_header_indexed_and_spaced(self):
        idx = field_positions(FIELDS)
        self.assertEqual(idx["close"], 2)
        self.assertEqual(idx["amount"], 9)
        self.assertEqual(idx["next_reference"], 16)

    def test_bad_schema(self):
        self.assertIsNone(field_positions(["代號", "收盤"]))

    def test_missing_numeric(self):
        self.assertIsNone(parse_number("---"))
        self.assertIsNone(parse_number("NaN"))
        self.assertEqual(str(parse_number("23,169")), "23169")

    def test_three_states(self):
        records, counts = audit_payload(sample(), "2026-01-02")
        self.assertEqual(counts["FOUR_DIGIT_UNIQUE"], 3)
        self.assertEqual(counts["NON_4_DIGIT_EXCLUDED"], 1)
        self.assertEqual(counts["VALID_CLOSE_WITH_VOLUME"], 1)
        self.assertEqual(counts["MISSING_CLOSE_WITH_VOLUME"], 1)
        self.assertEqual(counts["MISSING_CLOSE_NO_TRADE"], 1)
        missing = next(row for row in records if row["code"] == "1259")
        self.assertEqual(missing["close"], "")
        self.assertEqual(missing["average_price_not_close"], "60.97")
        self.assertEqual(missing["next_reference_not_close"], "61.30")

    def test_wrong_date_rejected(self):
        with self.assertRaisesRegex(ValueError, "date mismatch"):
            audit_payload(sample(), "2026-01-05")

    def test_wrong_stat_rejected(self):
        p = sample()
        p["stat"] = "ERROR"
        with self.assertRaisesRegex(ValueError, "status"):
            audit_payload(p, "2026-01-02")

    def test_duplicate_code_rejected(self):
        p = sample()
        p["tables"][0]["data"].append(row("1240", "12", "200", "2400"))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            audit_payload(p, "2026-01-02")

    def test_negative_volume_rejected(self):
        p = sample()
        p["tables"][0]["data"][0][8] = "-50"
        _, stats = audit_payload(p, "2026-01-02")
        self.assertEqual(stats["INVALID_VOLUME_OR_AMOUNT"], 1)

    def test_close_with_zero_volume(self):
        p = sample()
        p["tables"][0]["data"][0][8] = "0"
        p["tables"][0]["data"][0][9] = "0"
        _, stats = audit_payload(p, "2026-01-02")
        self.assertEqual(stats["VALID_CLOSE_ZERO_VOLUME"], 1)

    def test_malformed_row_halts(self):
        p = sample()
        p["tables"][0]["data"][0] = ["1240"]
        with self.assertRaisesRegex(ValueError, "Malformed"):
            audit_payload(p, "2026-01-02")

    def test_no_matching_table(self):
        p = sample()
        p["tables"][0]["fields"] = ["代號"]
        with self.assertRaisesRegex(ValueError, "uniquely"):
            audit_payload(p, "2026-01-02")

    def test_more_than_one_table_halts(self):
        p = sample()
        p["tables"].append(copy.deepcopy(p["tables"][0]))
        with self.assertRaisesRegex(ValueError, "uniquely"):
            audit_payload(p, "2026-01-02")

    def test_output_exclusive(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "raw.json"
            p.write_text(json.dumps(sample(), ensure_ascii=False), encoding="utf-8")
            outfile, count = audit_file(p, "2026-01-02")
            self.assertTrue(outfile.exists())
            self.assertEqual(count["FOUR_DIGIT_UNIQUE"], 3)
            with self.assertRaises(FileExistsError):
                audit_file(p, "2026-01-02")


if __name__ == "__main__":
    unittest.main()
