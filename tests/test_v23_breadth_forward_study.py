"""Offline tests for V2.3 exploratory breadth-forward study."""
import unittest
from datetime import date,timedelta
from v23_breadth_forward_study import parse_fmtqik, category, forward_outcomes, LABELS, analyze

FIELDS=["日期","發行量加權股價指數"]
def fixture(day="115/03/04", value="32,828.88"):
    return {"stat":"OK","fields":FIELDS,"data":[[day,value]]}

class ForwardStudyTests(unittest.TestCase):
    def test_month_parsing(self):
        d=parse_fmtqik(fixture(),"202603")
        self.assertEqual(d[date(2026,3,4)],32828.88)

    def test_stale_month_rejected(self):
        with self.assertRaisesRegex(ValueError,"Stale"):
            parse_fmtqik(fixture(),"202604")

    def test_non_numeric_rejected(self):
        with self.assertRaises(ValueError):
            parse_fmtqik(fixture(value="--"),"202603")

    def test_duplicate_day_rejected(self):
        p=fixture()
        p["data"].append(p["data"][0])
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            parse_fmtqik(p,"202603")

    def test_stress_category(self):
        self.assertEqual(category({"TWSE":.08,"TPEX":.09}),LABELS[0])

    def test_high_breadth_category(self):
        self.assertEqual(category({"TWSE":.50,"TPEX":.40}),LABELS[1])

    def test_mixed_category(self):
        self.assertEqual(category({"TWSE":.60,"TPEX":.20}),LABELS[2])

    def test_positive_worst_but_negative_drawdown(self):
        data=[100,103,110,101,105,106]
        out=forward_outcomes(data,0,5)
        self.assertGreater(out["worst_from_signal_pct"],0)
        self.assertLess(out["max_drawdown_pct"],0)
        self.assertAlmostEqual(out["max_drawdown_pct"],(101/110-1)*100)

    def test_next_day_only_not_signal_loss(self):
        out=forward_outcomes([100,80,85,90,95,105],0,5)
        self.assertEqual(out["worst_from_signal_pct"],-20)
        self.assertEqual(out["end_return_pct"],5)

    def test_horizon_insufficient(self):
        with self.assertRaises(ValueError):
            forward_outcomes([100,105],0,5)

    def test_observation_count(self):
        start=date(2026,1,2)
        idx={start+timedelta(days=i):float(100+i) for i in range(20)}
        ratios={start.isoformat():{"TWSE":0.1,"TPEX":0.09}}
        rows=analyze(ratios,idx)
        self.assertEqual(len(rows),2)
        self.assertEqual(set(x["horizon_days"] for x in rows),{5,10})


if __name__=="__main__":
    unittest.main()
