"""No-network tests for bounded V2.3 market sample batch."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from v23_research_batch import calendar_days, positive_number, sample_counts, twse_counts, load_or_get
import v23_official_sample as sampler
def tpex_sample():
    fields = ["代號","名稱","收盤","漲跌","開盤","最高","最低","均價","成交股數","成交金額(元)", "成交筆數","最後買價","最後買量(張數)","最後賣價","最後賣量(張數)", "發行股數","次日 參考價","次日 漲停價","次日 跌停價"]
    def row(code, close, volume, amount):
        return [code,"NAME",close,"0","---","---","---","60.97",volume,amount,"1","60","1","61","1","10000","61.30","67","55"]
    return {"stat":"ok","date":"20260102","tables":[{"fields":fields,"data":[row("1240","10.25","1000","10250"),row("1259","---","380","23169"),row("5310","---","0","0"),row("00888","10","300","3000")]},{"fields":fields,"data":[]}]}


class ResearchBatchTests(unittest.TestCase):
    def test_calendar_five_day_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"calendar.csv"
            path.write_text("trade_date\n2026-01-02\n2026-01-05\n2026-01-06\n2026-01-07\n2026-01-08\n2026-01-09\n", encoding="utf-8")
            self.assertEqual(len(calendar_days(path,"2026-01-02","2026-01-08")),5)
            with self.assertRaises(ValueError):
                calendar_days(path,"2026-01-02","2026-01-09")

    def test_reject_out_of_order_calendar(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"calendar.csv"
            path.write_text("trade_date\n2026-01-05\n2026-01-02\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                calendar_days(path,"2026-01-02","2026-01-05")

    def test_decimal_filter(self):
        self.assertIsNone(positive_number("---"))
        self.assertIsNone(positive_number("NaN"))
        self.assertEqual(positive_number("1,074"),1074)

    def test_twse_exact_counts(self):
        payload={"tables":[{"fields":["證券代號","收盤價","成交股數","成交金額"],
                            "data":[["1101","10.5","1000","10500"],
                                    ["0050","100","500","50000"],
                                    ["1102","---","0","0"],
                                    ["WARRANT","5","100","500"]]}]}
        counts=twse_counts(payload,"2026-01-02",4)
        self.assertEqual(counts,{"four_digit_unique":3,"valid_close_with_volume":2,"missing_close":1})

    def test_twse_duplicate_rejected(self):
        payload={"tables":[{"fields":["證券代號","收盤價","成交股數","成交金額"],
                            "data":[["1101","10","1","10"],["1101","11","1","11"]]}]}
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            twse_counts(payload,"2026-01-02",2)

    def test_tpex_metrics(self):
        payload=tpex_sample()
        ev={"status":"metadata_schema_verified_only"}
        counts=sample_counts("TPEX","2026-01-02",payload,ev)
        self.assertEqual(counts,{"four_digit_unique":3,"valid_close_with_volume":1,"missing_close":2})

    def test_reject_unverified_evidence(self):
        with self.assertRaises(ValueError):
            sample_counts("TPEX","2026-01-02",{},{"status":"unverified"})

    def test_verified_reuse_and_no_fetch(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            market="TPEX"
            day="2026-01-02"
            payload=tpex_sample()
            ev=sampler.evidence(market,day,payload)
            raw=json.dumps(payload,ensure_ascii=False).encode()
            sampler.save_sample(folder,market,day,raw,ev,sampler.request_url(market,day))
            decoded,info,origin=load_or_get(folder,market,day,fetch=False)
            self.assertEqual(origin,"VERIFIED_REUSE")
            self.assertEqual(decoded["date"],"20260102")
            self.assertEqual(info["status"],"metadata_schema_verified_only")

    def test_bad_hash_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            market="TPEX"
            day="2026-01-02"
            payload=tpex_sample()
            ev=sampler.evidence(market,day,payload)
            sampler.save_sample(folder,market,day,json.dumps(payload,ensure_ascii=False).encode(),
                                ev,sampler.request_url(market,day))
            (folder/"TPEX_20260102.json").write_text("{}",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"manifest mismatch"):
                load_or_get(folder,market,day,fetch=False)


if __name__ == "__main__":
    unittest.main()
