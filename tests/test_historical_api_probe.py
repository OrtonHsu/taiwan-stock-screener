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
    def institutional_fixture(self, day="2021-11-19"):
        from datetime import date
        d = date.fromisoformat(day)
        return {"stat": "OK", "date": day.replace("-", ""),
                "title": f"{d.year-1911}年{d.month}月{d.day}日 三大法人買賣金額統計表",
                "fields": ["單位名稱", "買進金額", "賣出金額", "買賣差額"],
                "data": [["外資及陸資(不含外資自營商)", "1,000", "900", "100"],
                         ["投信", "500", "550", "-50"], ["自營商", "300", "280", "20"]]}
    def test_institutional_metadata_date(self):
        result = inspect_payload(self.institutional_fixture(), DATES[0], "TWSE_institutional")
        self.assertEqual(result["status"], "date_and_fields_confirmed")
        self.assertEqual(result["foreign_category"], "外資及陸資(不含外資自營商)")
    def test_institutional_url_daydate(self):
        url = make_url("TWSE_institutional", DATES[0])
        self.assertIn("dayDate=20211119", url)
        self.assertNotIn("&day=", url)
    def test_institutional_four_years(self):
        for day in DATES:
            self.assertEqual(inspect_payload(self.institutional_fixture(day), day, "TWSE_institutional")["status"], "date_and_fields_confirmed")
    def test_institutional_wrong_date(self):
        obj = self.institutional_fixture()
        obj["date"] = "20261120"
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "unverified")
    def test_institutional_wrong_title(self):
        obj = self.institutional_fixture()
        obj["title"] = "115年11月20日 三大法人買賣金額統計表"
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "unverified")
    def test_institutional_missing_field(self):
        obj = self.institutional_fixture()
        obj["fields"].pop()
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "unverified")
    def test_institutional_invalid_amount(self):
        obj = self.institutional_fixture()
        obj["data"][0][1] = "--"
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["reason"], "institutional invalid amount")
    def test_institutional_mismatched_net(self):
        obj = self.institutional_fixture()
        obj["data"][0][3] = "999"
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["reason"], "institutional amount mismatch")
    def test_institutional_missing_foreign(self):
        obj = self.institutional_fixture()
        obj["data"][0][0] = "其他法人"
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "unverified")
    def test_institutional_duplicate_category(self):
        obj = self.institutional_fixture()
        obj["data"][1][0] = obj["data"][0][0]
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_institutional")["status"], "unverified")
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
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_index")["status"], "unverified")
    def test_index_url(self):
        url = make_url("TWSE_index", DATES[0])
        self.assertIn("/afterTrading/MI_INDEX?", url)
        self.assertIn("type=IND", url)
    def test_index_valid_nested_roc_title(self):
        obj = {"stat": "OK", "tables": [{"title": "110年11月19日 價格指數(臺灣證券交易所)",
            "fields": ["指數", "收盤指數", "漲跌(+-)"],
            "data": [["發行量加權股價指數", "17,818.31", "+"]]}]}
        result = inspect_payload(obj, DATES[0], "TWSE_index")
        self.assertEqual(result["status"], "date_and_fields_confirmed")
        self.assertEqual(result["index_close"], 17818.31)
    def test_index_wrong_title_date(self):
        obj = {"tables": [{"title": "115年11月20日 價格指數",
            "fields": ["指數", "收盤指數"],
            "data": [["發行量加權股價指數", "20,000"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_index")["status"], "unverified")
    def test_index_missing_target(self):
        obj = {"tables": [{"title": "110年11月19日 價格指數",
            "fields": ["指數", "收盤指數"], "data": [["寶島股價指數", "20,000"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_index")["status"], "unverified")
    def test_index_invalid_close(self):
        obj = {"tables": [{"title": "110年11月19日 價格指數",
            "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "--"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_index")["reason"], "invalid index close")
    def test_index_missing_close_column(self):
        obj = {"tables": [{"title": "110年11月19日 價格指數",
            "fields": ["指數", "漲跌"], "data": [["發行量加權股價指數", "5"]]}]}
        self.assertEqual(inspect_payload(obj, DATES[0], "TWSE_index")["status"], "unverified")
    def test_institutional_stale_title(self):
        obj = self.institutional_fixture("2026-11-20")
        self.assertEqual(inspect_payload(obj, "2025-11-20", "TWSE_institutional")["status"], "unverified")
    def test_index_multiple_matches_rejected(self):
        table = {"title": "110年11月19日 價格指數",
                 "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "17818.31"]]}
        self.assertEqual(inspect_payload({"tables": [table, table]}, DATES[0], "TWSE_index")["status"], "unverified")
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
