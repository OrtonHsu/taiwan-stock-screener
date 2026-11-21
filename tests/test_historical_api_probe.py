"""Offline tests for the read-only historical API probe."""
import io
import json
import unittest
from historical_api_probe import DATES, SOURCES, inspect_payload, make_url, probe, roc_day

class FakeResponse:
    def __init__(self, obj):
        self.body = io.BytesIO(json.dumps(obj).encode())
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, n):
        return self.body.read(n)

class HistoricalProbeTests(unittest.TestCase):
    def test_four_research_years(self):
        self.assertEqual([x[:4] for x in DATES], ["2021", "2023", "2025", "2026"])
    def test_roc_date(self):
        self.assertEqual(roc_day("2021-11-19"), "110/11/19")
    def test_source_url_is_historical_request(self):
        self.assertIn("date=20211119", make_url("TWSE_stock", DATES[0]))
        self.assertIn("date=110%2F11%2F19", make_url("TPEX_quotes", DATES[0]))
    def test_invalid_source_and_day(self):
        with self.assertRaises(ValueError):
            make_url("unknown", DATES[0])
        with self.assertRaises(ValueError):
            make_url("TWSE_stock", "2021-02-30")
    def test_empty_response_is_unverified(self):
        self.assertEqual(inspect_payload({"data": []}, DATES[0])["status"], "unverified")
    def test_date_mismatch_is_unverified(self):
        self.assertEqual(inspect_payload({"data": [["2023/11/20", "100"]]}, DATES[0])["status"], "unverified")
    def test_matching_date_is_confirmed(self):
        self.assertEqual(inspect_payload({"data": [["110/11/19", "100"]]}, DATES[0])["status"], "date_confirmed")
    def test_probe_uses_only_get_and_no_storage(self):
        def opener(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(timeout, 15)
            return FakeResponse({"fields": ["日期", "收盤"], "data": [["110/11/19", "100"]]})
        result = probe("TWSE_stock", DATES[0], opener=opener)
        self.assertEqual(result["status"], "date_confirmed")
    def test_transport_error_does_not_fake_success(self):
        def opener(request, timeout):
            raise TimeoutError("timeout")
        self.assertEqual(probe("TWSE_stock", DATES[0], opener=opener)["status"], "unverified")

if __name__ == "__main__":
    unittest.main()
