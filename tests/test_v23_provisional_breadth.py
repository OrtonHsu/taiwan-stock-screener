"""Pure offline, no connection tests for V2.3 provisional market breadth."""
import unittest
from decimal import Decimal
from v23_provisional_breadth import advance_decline, eligible_quotes, twse_table


def tpex_fixture():
    columns=["代號","名稱","收盤","漲跌","開盤","最高","最低","均價","成交股數","成交金額(元)","成交筆數","最後買價","最後買量(張數)","最後賣價","最後賣量(張數)","發行股數","次日 參考價","次日 漲停價","次日 跌停價"]
    def row(c,p,v,a):
        return [c,"NAME",p,"0","---","---","---","60",v,a,"1","60","1","61","1","100","61.30","67","55"]
    return {"stat":"OK","date":"20260102","tables":[
      {"fields":columns,"data":[row("1240","10.50","1000","10500"),row("1259","---","380","23169"),row("5310","---","0","0"),row("0050","10","1000","10000")]}]}


def twse_fixture():
    return {"tables":[{"title":"115年1月2日","fields":["證券代號","證券名稱","成交股數","成交金額","收盤價"],
                       "data":[["1101","ABC","1,000","10,500","10.5"],
                               ["1102","DEF","0","0","---"],
                               ["0050","ETF","100","10000","100"]]}]}


class BreadthTests(unittest.TestCase):
    def test_twse_parse(self):
        out,counts=eligible_quotes("TWSE","2026-01-02",twse_fixture())
        self.assertEqual(counts,{"candidate_codes":2,"eligible_codes":1,
                                 "invalid_or_unpriced":1,"excluded_00":1})
        self.assertEqual(out["1101"],Decimal("10.5"))

    def test_tpex_parse_missing_close_no_imputation(self):
        out,counts=eligible_quotes("TPEX","2026-01-02",tpex_fixture())
        self.assertEqual(set(out),{"1240"})
        self.assertEqual(counts["invalid_or_unpriced"],2)
        self.assertEqual(counts["excluded_00"],1)

    def test_advance_decline(self):
        prev={"1101":Decimal(10),"1102":Decimal(20),"1103":Decimal(15)}
        curr={"1101":Decimal(11),"1102":Decimal(19),"1103":Decimal(15)}
        result=advance_decline(prev,curr)
        self.assertEqual((result["advancing"],result["declining"],result["unchanged"]),(1,1,1))
        self.assertEqual(result["breadth_ratio"],"0.333333")

    def test_no_previous_day_never_forward_fill(self):
        result=advance_decline(None,{"1101":Decimal(10)})
        self.assertEqual(result["breadth_ratio"],"")
        self.assertEqual(result["comparable"],0)

    def test_missing_current_only_excluded_from_denominator(self):
        prev={"1101":Decimal(10),"1102":Decimal(20)}
        curr={"1101":Decimal(11),"1103":Decimal(30)}
        result=advance_decline(prev,curr)
        self.assertEqual(result["comparable"],1)
        self.assertEqual(result["new_or_uncomparable"],1)
        self.assertEqual(result["breadth_ratio"],"1.000000")

    def test_duplicate_rejected(self):
        p=twse_fixture()
        p["tables"][0]["data"].append(["1101","XYZ","1","10","10"])
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            eligible_quotes("TWSE","2026-01-02",p)

    def test_unsupported_market(self):
        with self.assertRaises(ValueError):
            eligible_quotes("OTHER","2026-01-02",{})

    def test_ambiguous_twse_table_rejected(self):
        p=twse_fixture()
        p["tables"].append(p["tables"][0].copy())
        with self.assertRaises(ValueError):
            twse_table(p)


if __name__=="__main__":
    unittest.main()
