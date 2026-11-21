"""No-network tests for V2.3 official single-day sampler."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from v23_official_sample import request_url,evidence,fetch_one,save_sample,validate_day

class FakeResponse:
    headers={"Content-Type":"application/json;charset=UTF-8"}
    def __init__(self,obj): self.buffer=io.BytesIO(json.dumps(obj).encode())
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self,n): return self.buffer.read(n)

class OfficialSampleTests(unittest.TestCase):
    def setUp(self):
        self.twse={"stat":"OK","tables":[{"title":"115年1月2日 收盤行情",
            "fields":["證券代號","證券名稱","成交股數","成交金額","收盤價"],
            "data":[["2330","台積電","1000","10000","500"]]}]}
        self.tpex={"stat":"ok","date":"115/01/02",
            "tables":[{"fields":["代號","收盤"],"data":[["6488","100"]]}]}

    def test_urls(self):
        self.assertIn("type=ALLBUT0999",request_url("TWSE","2026-01-02"))
        self.assertIn("date=115%2F01%2F02",request_url("TPEX","2026-01-02"))
    def test_twse_valid(self):
        self.assertEqual(evidence("TWSE","2026-01-02",self.twse)["status"],"schema_date_verified")
    def test_twse_wrong_title(self):
        self.twse["tables"][0]["title"]="114年1月2日 收盤行情"
        with self.assertRaises(ValueError): evidence("TWSE","2026-01-02",self.twse)
    def test_twse_missing_field(self):
        self.twse["tables"][0]["fields"].pop()
        with self.assertRaises(ValueError): evidence("TWSE","2026-01-02",self.twse)
    def test_tpex_valid(self):
        self.assertFalse(evidence("TPEX","2026-01-02",self.tpex)["daily_row_granularity_verified"])
    def test_tpex_stale(self):
        self.tpex["date"]="114/01/02"
        with self.assertRaises(ValueError): evidence("TPEX","2026-01-02",self.tpex)
    def test_bad_stat(self):
        self.tpex["stat"]="ERROR"
        with self.assertRaises(ValueError): evidence("TPEX","2026-01-02",self.tpex)
    def test_only_get(self):
        def opener(req,timeout):
            self.assertEqual(req.get_method(),"GET")
            self.assertEqual(timeout,25)
            return FakeResponse(self.twse)
        self.assertIn(b'"stat"',fetch_one("https://example.com",opener=opener))
    def test_calendar_gate(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"calendar.csv"
            path.write_text("trade_date,source\n2026-01-02,TWSE_FMTQIK\n")
            validate_day("2026-01-02",path)
            with self.assertRaises(ValueError): validate_day("2026-01-03",path)
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            save_sample(d,"TWSE","2026-01-02",b"{}",{},"https://example.com")
            with self.assertRaises(FileExistsError):
                save_sample(d,"TWSE","2026-01-02",b"{}",{},"https://example.com")

if __name__=="__main__":
    unittest.main()
