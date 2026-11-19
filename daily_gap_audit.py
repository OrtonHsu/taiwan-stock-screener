"""Read-only audit of suspicious missing TWSE/TPEX trading-day quotes.

A date is usable as a comparison day only when BOTH markets have a
sufficiently broad snapshot. Missing individual quotes are *suspicions*,
not proven bad data: halts and no-trade days require manual verification.
This audit never downloads, repairs, or changes prices.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from tw_market_calendar import (
    UnsupportedCalendarYear, expected_market_dates, latest_due_market_date,
)
import json
import os
from zoneinfo import ZoneInfo

MARKETS = ("TWSE", "TPEX")
MIN_RECORDS = {"TWSE": 900, "TPEX": 700}


def audit_rows(rows, *, thresholds=None, max_examples=20, official_dates=None):
    """Audit (ISO date, market, stock code) rows without guessing holidays.

    Only stocks present on the latest two-market complete date are checked.
    For each stock, inspect the *interior* of its observed trading-day range;
    this avoids assuming pre-listing or post-delisting quotes should exist.
    """
    thresholds = thresholds or MIN_RECORDS
    by_date = defaultdict(lambda: {market: set() for market in MARKETS})
    by_stock = defaultdict(set)
    for day, market, code in rows:
        if market not in MARKETS:
            continue  # Ignore legacy 'TPEx' records, do not double count.
        day, code = str(day), str(code)
        by_date[day][market].add(code)
        by_stock[(market, code)].add(day)

    official_set = set(official_dates) if official_dates is not None else None
    complete_dates = sorted(
        day for day, markets in by_date.items()
        if all(len(markets[m]) >= thresholds[m] for m in MARKETS)
        and (official_set is None or day in official_set)
    )
    if not complete_dates:
        raise ValueError("找不到上市及上櫃同時達安全門檻的交易日；無法進行缺日稽核")

    latest = complete_dates[-1]
    complete_set = set(complete_dates)
    partial = [
        {"date": day, **{m: len(markets[m]) for m in MARKETS}}
        for day, markets in by_date.items()
        if not all(len(markets[m]) >= thresholds[m] for m in MARKETS)
    ]
    partial.sort(key=lambda x: x["date"], reverse=True)

    stats = {}
    suspected = []
    for market in MARKETS:
        current_codes = by_date[latest][market]
        missing_stocks = 0
        missing_rows = 0
        for code in sorted(current_codes):
            observed = by_stock[(market, code)] & complete_set
            if len(observed) < 2:
                continue
            start, end = min(observed), max(observed)
            gaps = [day for day in complete_dates
                    if start < day < end and day not in observed]
            if not gaps:
                continue
            missing_stocks += 1
            missing_rows += len(gaps)
            suspected.append({"market": market, "code": code,
                              "missing_count": len(gaps),
                              "sample_dates": gaps[:5]})
        stats[market] = {
            "latest_codes": len(current_codes),
            "suspected_missing_stocks": missing_stocks,
            "suspected_missing_quotes": missing_rows,
        }

    suspected.sort(key=lambda x: (-x["missing_count"], x["market"], x["code"]))
    report = {
        "latest_complete_date": latest,
        "complete_market_days": len(complete_dates),
        "partial_market_days": len(partial),
        "partial_day_samples": partial[:max_examples],
        "market_stats": stats,
        "suspected_stock_samples": suspected[:max_examples],
        "suspected_stocks_total": len(suspected),
    }
    if official_set is not None:
        if not official_set:
            raise ValueError("官方日曆沒有可比較交易日")
        # Avoid flagging pre-history dates: start at the earliest observed
        # daily snapshot, but extend to the latest session due by 16:00.
        last_due = max(official_set)
        observed = {d for d in by_date if d <= last_due}
        if not observed:
            raise ValueError("官方交易日範圍內無任何已儲存行情")
        first_observed = min(observed)
        full_day_gaps = sorted(
            d for d in official_set if first_observed <= d <= last_due
            and d not in by_date
        )
        closed_day_quotes = sorted(
            d for d in observed if first_observed <= d <= last_due
            and d not in official_set
        )
        report["official_calendar"] = {
            "status": "verified_2026_reference",
            "first_observed_date": first_observed,
            "last_due_date": last_due,
            "expected_days": sum(first_observed <= d <= last_due
                                 for d in official_set),
            "fully_missing_market_days": len(full_day_gaps),
            "fully_missing_date_samples": full_day_gaps[:max_examples],
            "quotes_on_closed_days": len(closed_day_quotes),
            "closed_day_quote_samples": closed_day_quotes[:max_examples],
        }
    return report


def markdown_summary(report):
    lines = [
        "## V2 逐交易日缺漏稽核（唯讀）",
        f"雙市場完整最新日期：**{report['latest_complete_date']}**；",
        f"可比較交易日：**{report['complete_market_days']}**；"
        f"資料未達雙市場門檻日期：**{report['partial_market_days']}**。",
        "",
        "| 市場 | 最新日股票數 | 疑似缺日股票 | 疑似缺少行情筆數 |",
        "|---|---:|---:|---:|",
    ]
    for market, s in report["market_stats"].items():
        lines.append(f"| {market} | {s['latest_codes']} | "
                     f"{s['suspected_missing_stocks']} | {s['suspected_missing_quotes']} |")
    cal = report.get("official_calendar")
    if cal and cal.get("status") == "verified_2026_reference":
        lines.extend([
            "",
            "### 官方交易日曆核對（2026 年，含已確認的 10/23 颱風休市）",
            f"已觀察期間預期交易日：**{cal['expected_days']}**；"
            f"整日完全缺行情的疑似日期：**{cal['fully_missing_market_days']}**；"
            f"休市日期卻有行情的疑似日期：**{cal['quotes_on_closed_days']}**。",
        ])
        if cal["fully_missing_date_samples"]:
            lines.append("疑似整日缺漏：" + ", ".join(cal["fully_missing_date_samples"]))
        if cal["closed_day_quote_samples"]:
            lines.append("疑似休市日誤記：" + ", ".join(cal["closed_day_quote_samples"]))
    elif cal:
        lines.append("\n**官方日曆未驗證：** " + cal.get("reason", "不支援此年份"))
    lines.append("\n**注意：** 個股無交易、停牌、上市及轉板仍需人工核對；"
                 "官方年度日曆不包含未知未來臨時休市，整日缺漏只是待核實警示。"
                 "不會寫入或自動回補。")
    if report["partial_day_samples"]:
        lines.extend(["", "### 未達雙市場門檻的日期（樣本）"])
        for day in report["partial_day_samples"]:
            lines.append(f"- {day['date']}: TWSE {day['TWSE']} / TPEX {day['TPEX']}")
    if report["suspected_stock_samples"]:
        lines.extend(["", "### 個股疑似缺日（樣本）"])
        for stock in report["suspected_stock_samples"]:
            lines.append(f"- {stock['market']} {stock['code']}: "
                         f"{stock['missing_count']} 日；例：{', '.join(stock['sample_dates'])}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--max-examples", type=int, default=20)
    args = parser.parse_args(argv)
    if not 7 <= args.lookback_days <= 365:
        parser.error("--lookback-days 必須介於 7 至 365")
    if not 1 <= args.max_examples <= 100:
        parser.error("--max-examples 必須介於 1 至 100")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("缺少 DATABASE_URL")
    import psycopg

    now = datetime.now(ZoneInfo("Asia/Taipei"))
    today = now.date()
    begin = (today - timedelta(days=args.lookback_days)).isoformat()
    calendar_warning = None
    try:
        end = latest_due_market_date(now)
        official_dates = expected_market_dates(begin, end)
    except UnsupportedCalendarYear as exc:
        official_dates = None
        calendar_warning = str(exc)
        print(f"CALENDAR_UNVERIFIED {calendar_warning}", flush=True)
    # The query is read-only, including the Postgres transaction flag.
    with psycopg.connect(db_url, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, market, code
                FROM public.prices
                WHERE date >= %s AND date <= %s
                  AND market IN ('TWSE', 'TPEX')
            """, (begin, today.isoformat()))
            rows = cur.fetchall()

    report = audit_rows(
        rows, max_examples=args.max_examples, official_dates=official_dates
    )
    if calendar_warning is not None:
        report["official_calendar"] = {
            "status": "unverified_year", "reason": calendar_warning
        }
    report["lookback_since"] = begin
    print("DAILY_GAP_AUDIT " + json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as output:
            output.write(markdown_summary(report))


if __name__ == "__main__":
    main()
