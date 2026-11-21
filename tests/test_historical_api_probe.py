"""Offline, no-network tests for the V2.3 read-only historical probe."""
import io
import json
import unittest
from historical_api_probe import DATES, inspect_payload, inspect_response, make_url, probe, roc_day

class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}
    def __init__(self, obj):
        self.body = io.BytesIO(json.dumps(obj).encode())
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, n): return self.body.read(n)

class HistoricalProbeTests(unittest.TestCase):
    def test_years(self):
        self.assertEqual([d[:4] for d in DATES], ["2021", "2023", "2025", "2026"])
    def test_roc_day(self):
        self.assertEqual(roc_day("2021-11-19"), "110/11/19")
    def test_urls(self):
        self.assertIn("date=20211119", make_url("TWSE_stock", DATES[0]))
        self.assertIn("date=110%2F11%2F19", make_url("TPEX_quotes", DATES[0]))
    def test_invalid_date_and_source(self):
        with self.assertRaises(ValueError): make_url("invalid", DATES[0])
        with self.assertRaises(ValueError): make_url("TWSE_stock", "2021-02-30")
    def test_stock_fields_and_date(self):
        obj = {"stat": "OK", "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
               "data": [["110/11/19", "1", "2", "3", "4", "2", "3"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_stock")["status"], "date_and_fields_confirmed")
    def test_stock_missing_field(self):
        obj = {"fields": ["日期", "收盤價"], "data": [["110/11/19", "3"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_stock")["reason"], "required fields missing")
    def test_date_mismatch(self):
        obj = {"fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
               "data": [["112/11/20", "1", "2", "3", "4", "2", "3"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_stock")["status"], "unverified")
    def test_turnover(self):
        obj = {"fields": ["日期", "成交金額", "發行量加權股價指數"], "data": [["110/11/19", "123", "17000"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_turnover")["status"], "date_and_fields_confirmed")
    def test_tpex_tables_metadata_date(self):
        obj = {"date": "110/11/19", "stat": "ok",
               "tables": [{"title": "上櫃每日行情", "fields": ["代號", "收盤"], "data": [["1234", "12"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TPEX_quotes")["status"], "date_and_fields_confirmed")
    def test_tpex_tables_wrong_date(self):
        obj = {"date": "112/11/20", "tables": [{"fields": ["代號", "收盤"], "data": [["1234", "12"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TPEX_quotes")["status"], "unverified")
    def test_tpex_missing_fields(self):
        obj = {"date": "110/11/19", "tables": [{"fields": ["名稱"], "data": [["ABC"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TPEX_quotes")["reason"], "required fields missing")
    def test_institutional_metadata_date(self):
        obj = {"date": "110/11/19", "fields": ["單位名稱", "買進金額", "賣出金額"],
               "data": [["外資", "100", "90"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "date_and_fields_confirmed")
    def test_institutional_no_date_unverified(self):
        obj = {"stat": "OK", "fields": ["單位名稱", "買進金額", "賣出金額"],
               "data": [["外資", "100", "90"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "unverified")
    def test_index_html_diagnostic(self):
        obj = inspect_response(b"<html>not json</html>", DATES[0], "TWSE_index",
                               http_status=200, content_type="text/html")
        self.assertEqual(obj["reason"], "non-JSON response")
        self.assertEqual(obj["http_status"], 200)
    def test_index_json_requires_date(self):
        obj = {"fields": ["日期"], "data": [["110/11/19"]]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_index")["status"], "date_and_fields_confirmed")
    def test_get_only(self):
        def opener(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(timeout, 15)
            return FakeResponse({"fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                                 "data": [["110/11/19", "1", "2", "3", "4", "2", "3"]]})
        self.assertEqual(probe("TWSE_stock", DATES[0], opener=opener)["status"], "date_and_fields_confirmed")
    def test_transport_failure(self):
        def opener(request, timeout): raise TimeoutError("timeout")
        self.assertEqual(probe("TWSE_stock", DATES[0], opener=opener)["status"], "unverified")

if __name__ == "__main__":
    unittest.main()
