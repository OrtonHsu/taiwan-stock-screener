"""Manually approved, narrowly scoped and transactional 7713 quote repair.

DEFAULT: preview-only. Does not write unless --write AND an exact approval
phrase are supplied. Re-fetches the official TPEx October 2026 report before
each attempt; never extrapolates or forwards-fills a quote.

Only 7713/TPEX on 2026-10-27, 2026-10-29 and 2026-10-30 is eligible.
INSERT ... ON CONFLICT DO NOTHING protects the existing primary key.
Any unexpected conflict, different market label, or mismatched existing value
aborts the entire transaction; no updates/deletes or progress markers.
"""

import argparse
import json
import math
import os

import psycopg
import requests

from tpex_7713_safe_preview import (
    CODE, MARKET, MONTH, TARGET_DATES, check_official_rows, markdown_report,
    prepare_report,
)
from tpex_backfill import get_month, parse_month

CONFIRMATION = "WRITE_7713_OCT2026_THREE_VERIFIED_DATES"
TARGET_SQL = """
    SELECT date, market, code, name, close, volume, amount
    FROM public.prices
    WHERE code = %s AND date = ANY(%s)
    ORDER BY date, market
"""
INSERT_SQL = """
    INSERT INTO public.prices
      (date, market, code, name, close, volume, amount)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (date, market, code) DO NOTHING
    RETURNING date
"""


def matches_official(stored, official):
    """Allow idempotent reruns only if recorded quote equals official values."""
    if (str(stored[0]), str(stored[1]), str(stored[2])) != (
        str(official[0]), str(official[1]), str(official[2])
    ):
        return False
    if str(stored[3]) != str(official[3]):
        return False
    for pos in (4, 5, 6):
        if stored[pos] is None or official[pos] is None:
            return False
        if not math.isclose(float(stored[pos]), float(official[pos]),
                            rel_tol=0, abs_tol=0.000001):
            return False
    return True


def suspicious_amounts(official_rows):
    """Flag extreme average-price mismatch; do not infer a replacement price.

    In ordinary stock trades the daily weighted average should not diverge
    drastically from the last close. This conservative heuristic only
    requests manual review; it never corrects the official source itself.
    """
    warnings = []
    for row in check_official_rows(official_rows).values():
        day, _, _, _, close, volume, amount = row
        if float(volume) > 0:
            implied_avg = float(amount) / float(volume)
            deviation = abs(implied_avg / float(close) - 1)
            if deviation > 0.10:
                warnings.append({
                    "date": str(day),
                    "close": float(close),
                    "implied_avg": round(implied_avg, 4),
                    "deviation_pct": round(deviation * 100, 2),
                    "reason": "成交金額／成交股數與收盤價差異大，需核對來源",
                })
    return warnings


def assess_existing(official_rows, stored_rows):
    """Return candidate insert tuples, refusing conflicting historical data."""
    verified = check_official_rows(official_rows)
    report = prepare_report(official_rows, stored_rows)
    if report["other_market_records"]:
        raise ValueError("目標日期存在其他市場別紀錄，停止修復、改人工核對")
    for row in stored_rows:
        day, market, code = str(row[0]), str(row[1]), str(row[2])
        if (day, market, code) != (day, MARKET, CODE) or day not in verified:
            raise ValueError("資料庫出現不符合目標的行情")
        if not matches_official(row, verified[day]):
            raise ValueError(f"已存在的 {day} 行情與官方不一致，拒絕覆蓋")
    return [verified[day] for day in TARGET_DATES if day in report["missing_dates"]], report


def run_transaction(conn, official_rows, *, write=False):
    """One DB transaction: all pending rows inserted or none."""
    with conn.cursor() as cur:
        if write:
            # Serializes this targeted repair against its own concurrent runs.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (7713202610,))
        cur.execute(
            TARGET_SQL + (" FOR UPDATE" if write else ""),
            (CODE, list(TARGET_DATES)),
        )
        current = cur.fetchall()
        to_insert, before = assess_existing(official_rows, current)
        warnings = suspicious_amounts(official_rows)
        if not write:
            return {
                "mode": "DRY_RUN", "before": before,
                "candidate_inserts": len(to_insert), "inserted": 0,
                "review_warnings": warnings,
                "ready_for_insert": not warnings and before["ready_for_manual_review"],
            }
        if warnings:
            raise RuntimeError(
                "官方成交金額／成交股數存在需人工核對的異常；"
                "本程式禁止寫入，請先確認官方資料與單位"
            )

        for row in to_insert:
            cur.execute(INSERT_SQL, row)
            result = cur.fetchone()
            if result is None or str(result[0]) != str(row[0]):
                raise RuntimeError("交易中發生重複鍵衝突，撤銷本次所有新增行情")
        # Recheck all three using the same transaction; a later failure rolls
        # back all inserts, even if 1 or 2 inserts already succeeded.
        cur.execute(TARGET_SQL + " FOR UPDATE", (CODE, list(TARGET_DATES)))
        _, after = assess_existing(official_rows, cur.fetchall())
        if after["missing_dates"]:
            raise RuntimeError("補存後仍缺官方行情，撤銷整筆交易")
        return {
            "mode": "WRITE", "before": before, "after": after,
            "candidate_inserts": len(to_insert), "inserted": len(to_insert),
            "review_warnings": [],
            "ready_for_insert": True,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args(argv)
    if args.write and args.confirmation != CONFIRMATION:
        raise RuntimeError("未提供完整人工確認字串，禁止寫入")
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("缺少 DATABASE_URL")

    with requests.Session() as session:
        payload = get_month(session, CODE, MONTH)
        official_rows = parse_month(payload, CODE, MONTH.strftime("%Y-%m"))
    check_official_rows(official_rows)

    with psycopg.connect(url, connect_timeout=15) as conn:
        conn.read_only = not args.write
        outcome = run_transaction(conn, official_rows, write=args.write)

    # Close/volume/amount are copied from the official parse, not from
    # previously stored estimates.
    print("TPEX_7713_GUARDED_REPAIR " + json.dumps(
        outcome, ensure_ascii=False, sort_keys=True
    ), flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## 7713 嚴格修復結果\n\n")
            f.write(f"模式：**{outcome['mode']}**；"
                    f"新增：**{outcome['inserted']}**；"
                    f"候選：**{outcome['candidate_inserts']}**。\n\n")
            if outcome.get("review_warnings"):
                f.write("\n**資料合理性待核對，不允許寫入：**\n")
                for warning in outcome["review_warnings"]:
                    f.write(
                        f"- {warning['date']}：成交金額／股數推算平均價 "
                        f"{warning['implied_avg']}，官方收盤價 {warning['close']}，"
                        f"差距 {warning['deviation_pct']}%\n"
                    )
            f.write(markdown_report(outcome.get("after", outcome["before"])))


if __name__ == "__main__":
    main()
