"""Manual-only, read-only official quote audit for a few suspected missing days.

Compare verified TWSE/TPEx monthly closing records with already stored days.
An official valid closing quote missing from the database is a *confirmed gap
candidate*, not permission to write. Unknown/no-price dates remain unresolved.
Never inserts, updates, deletes, or fabricates prices.
"""

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta
import json
import os
import time
from typing import NamedTuple
from zoneinfo import ZoneInfo

import psycopg
import requests

from daily_gap_audit import MARKETS, MIN_RECORDS
from market_backfill import fetch_month as get_twse_month
from market_backfill import parse_month as parse_twse_month
from tpex_backfill import get_month as get_tpex_month
from tpex_backfill import parse_month as parse_tpex_month
from tpex_backfill import parse_roc_date
from tw_market_calendar import expected_market_dates, latest_due_market_date


def choose_batches(rows, official_dates, *, max_requests=6, batch_offset=0, thresholds=None):
    """Prioritize up to N code/month groups, fairly alternating markets.

    A day is a comparison day only if both markets reach coverage thresholds.
    A stock must have observations both before and after any suspicious day.
    Only codes active on the latest complete day are considered.
    """
    if not 1 <= max_requests <= 10:
        raise ValueError("max_requests 必須介於 1 與 10")
    if not 0 <= batch_offset <= 1000:
        raise ValueError("batch_offset 必須介於 0 與 1000")
    thresholds = thresholds or MIN_RECORDS
    official_set = set(official_dates)
    by_date = defaultdict(lambda: {m: set() for m in MARKETS})
    by_stock = defaultdict(set)
    for day, market, code in rows:
        if market not in MARKETS or str(day) not in official_set:
            continue
        day = str(day)
        code = str(code)
        by_date[day][market].add(code)
        by_stock[(market, code)].add(day)

    good_days = sorted(
        d for d, markets in by_date.items()
        if all(len(markets[m]) >= thresholds[m] for m in MARKETS)
    )
    if not good_days:
        raise ValueError("沒有雙市場均達安全門檻的比較交易日")

    latest = good_days[-1]
    queues = {market: [] for market in MARKETS}
    for market in MARKETS:
        for code in by_date[latest][market]:
            observed = by_stock[(market, code)]
            if len(observed) < 2:
                continue
            begin, end = min(observed), max(observed)
            missing_by_month = defaultdict(list)
            for day in good_days:
                if begin < day < end and day not in observed:
                    missing_by_month[day[:7]].append(day)
            for month, dates in missing_by_month.items():
                queues[market].append({
                    "market": market,
                    "code": code,
                    "month": month,
                    "suspected_dates": dates,
                })
        queues[market].sort(
            key=lambda c: (-len(c["suspected_dates"]), c["code"], c["month"])
        )

    selected = []
    # Every run should investigate both listed and OTC data if available,
    # instead of using up the batch on higher-volume OTC gaps alone.
    limit = batch_offset + max_requests
    while len(selected) < limit and any(queues.values()):
        for market in ("TPEX", "TWSE"):
            if queues[market] and len(selected) < limit:
                selected.append(queues[market].pop(0))
    return latest, selected[batch_offset:]


class VerifiedOfficialMonth(NamedTuple):
    """Both validated quotes and unfiltered official table dates."""

    valid_rows: list
    raw_dates: set


def raw_official_dates(market, payload, expected_month):
    """Get *all* source dates, including lines with no valid close.

    Call the existing strict official parser first. This function only
    describes whether a date is present in its original table; a listed
    but invalid close does NOT establish that there was no trading.
    """
    if not isinstance(payload, dict):
        raise ValueError("官方來源不是 JSON 物件")
    if market == "TWSE":
        if payload.get("stat") != "OK":
            raise ValueError("上市官方來源狀態不為 OK")
        fields = payload.get("fields")
        records = payload.get("data")
        date_field = "日期"
    elif market == "TPEX":
        if str(payload.get("stat", "")).lower() != "ok":
            raise ValueError("上櫃官方來源狀態不為 OK")
        tables = payload.get("tables")
        if not isinstance(tables, list):
            raise ValueError("上櫃官方表格格式異常")
        if not tables:
            return set()
        table = tables[0]
        if not isinstance(table, dict):
            raise ValueError("上櫃官方表格內容異常")
        fields = table.get("fields")
        records = table.get("data")
        date_field = "日 期"
    else:
        raise ValueError(f"不支援市場：{market}")
    if not isinstance(fields, list) or date_field not in fields:
        raise ValueError("官方日期欄位異常")
    if not isinstance(records, list):
        raise ValueError("官方每日資料格式異常")
    index = fields.index(date_field)
    seen = set()
    for row in records:
        if not isinstance(row, (list, tuple)) or len(row) <= index:
            raise ValueError("官方原始日期列格式異常")
        day = parse_roc_date(row[index], expected_month).isoformat()
        if day in seen:
            raise ValueError(f"官方原始日期重複：{day}")
        seen.add(day)
    return seen


def compare_official_month(batch, official_rows, raw_reported_dates=None):
    """Classify only dates that the parser returned with *valid* close."""
    market, code, month = batch["market"], batch["code"], batch["month"]
    valid_dates = set()
    for row in official_rows:
        day, row_market, row_code = (str(row[0]), str(row[1]), str(row[2]))
        if row_market != market or row_code != code or day[:7] != month:
            raise ValueError(
                f"官方回傳不符合要求的股票或月份：{row_market} {row_code} {day}"
            )
        if day in valid_dates:
            raise ValueError(f"官方資料重複日期：{day}")
        valid_dates.add(day)
    suspected = set(batch["suspected_dates"])
    unverified = suspected - valid_dates
    # Without the unfiltered official table, we cannot distinguish
    # "date listed without a valid close" from "date absent".
    no_valid_close = []
    not_listed = []
    if raw_reported_dates is not None:
        raw_dates = set(raw_reported_dates)
        if not valid_dates.issubset(raw_dates):
            raise ValueError("解析後的有效收盤價日期未出現在官方原始表格")
        no_valid_close = sorted(unverified & raw_dates)
        not_listed = sorted(unverified - raw_dates)
    return {
        "market": market,
        "code": code,
        "month": month,
        "suspected_count": len(suspected),
        "official_valid_quotes": len(valid_dates),
        "confirmed_missing_quote_dates": sorted(suspected & valid_dates),
        # A missing valid close may mean zero trading, suspension or no listing.
        # Do not present it as verified "no trade" without an explicit source.
        "unresolved_dates": sorted(unverified),
        "official_listed_no_valid_close_dates": no_valid_close,
        "official_date_not_listed_dates": not_listed,
    }


def compare_batches(batches, fetcher, *, delay_seconds=2.5):
    """Execute bounded official requests; return results without writing."""
    if not 1.5 <= delay_seconds <= 30:
        raise ValueError("delay_seconds 必須介於 1.5～30")
    results = []
    errors = []
    with requests.Session() as session:
        for index, batch in enumerate(batches):
            market, code, month = (
                batch["market"], batch["code"], batch["month"]
            )
            try:
                fetched = fetcher(session, market, code, date.fromisoformat(month + "-01"))
                if isinstance(fetched, VerifiedOfficialMonth):
                    result = compare_official_month(
                        batch, fetched.valid_rows, fetched.raw_dates
                    )
                else:
                    # Legacy test/client without unfiltered rows: leave
                    # all no-price dates unresolved, not falsely classified.
                    result = compare_official_month(batch, fetched)
                results.append(result)
                print(
                    f"OFFICIAL_GAP_CHECK {market} {code} {month} "
                    f"suspected={result['suspected_count']} "
                    f"confirmed={len(result['confirmed_missing_quote_dates'])} "
                    f"unresolved={len(result['unresolved_dates'])} "
                    f"raw_no_close={len(result['official_listed_no_valid_close_dates'])} "
                    f"raw_absent={len(result['official_date_not_listed_dates'])}",
                    flush=True,
                )
            except Exception as exc:
                msg = f"{market} {code} {month}: {type(exc).__name__}: {exc}"
                errors.append(msg)
                print("OFFICIAL_GAP_FAILED " + msg, flush=True)
            if index < len(batches) - 1:
                time.sleep(delay_seconds)
    return results, errors


def fetch_verified_month(session, market, code, first_day):
    """Reuse the existing, date-checking monthly exchange parsers."""
    month = first_day.strftime("%Y-%m")
    if market == "TWSE":
        payload = get_twse_month(session, code, first_day)
        rows = parse_twse_month(payload, code, month)
        return VerifiedOfficialMonth(rows, raw_official_dates(market, payload, month))
    if market == "TPEX":
        payload = get_tpex_month(session, code, first_day)
        rows = parse_tpex_month(payload, code, month)
        return VerifiedOfficialMonth(rows, raw_official_dates(market, payload, month))
    raise ValueError(f"不支援市場：{market}")


def markdown_report(report):
    lines = [
        "## V2 官方月行情疑似缺漏核對（手動、唯讀）",
        f"最新完整行情日：**{report['latest_complete_date']}**；"
        f"批次起點（略過前幾組）：**{report.get('batch_offset', 0)}**；"
        f"本次檢查月份數：**{report['checked_groups']}**；"
        f"官方下載失敗：**{report['failed_groups']}**。",
        f"已比對出有官方有效收盤價、但資料庫無紀錄："
        f"**{report['confirmed_missing_quotes']} 筆**。",
        f"官方未提供可解析有效收盤價、尚不能確認："
        f"**{report['unresolved_quotes']} 筆**。",
        f"其中官方有列日期但無有效收盤價："
        f"**{report.get('official_listed_no_valid_close', 0)} 筆**；"
        f"官方未列日期：**{report.get('official_date_not_listed', 0)} 筆**。",
        "",
        "**這不是補檔工作。** 有效收盤價確認也不代表資料可直接覆寫；"
        "只產生待修復清單，不修改 Supabase，不寫入進度標記。",
    ]
    for r in report["results"]:
        lines.append(
            f"- {r['market']} {r['code']} {r['month']}: "
            f"官方有行情但本地缺少 {len(r['confirmed_missing_quote_dates'])} 筆，"
            f"仍需查核 {len(r['unresolved_dates'])} 筆"
        )
        if r["confirmed_missing_quote_dates"]:
            lines.append(
                "  - 候選日期：" + ", ".join(r["confirmed_missing_quote_dates"][:15])
            )
        if r.get("official_listed_no_valid_close_dates"):
            lines.append(
                "  - 官方列出但沒有有效收盤價：" +
                ", ".join(r["official_listed_no_valid_close_dates"][:15])
            )
        if r.get("official_date_not_listed_dates"):
            lines.append(
                "  - 官方原始表格未列日期：" +
                ", ".join(r["official_date_not_listed_dates"][:15])
            )
    for error in report["errors"][:10]:
        lines.append("- 核對失敗：" + error)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--max-requests", type=int, default=6)
    parser.add_argument("--batch-offset", type=int, default=0)
    args = parser.parse_args(argv)
    if not 14 <= args.lookback_days <= 180:
        parser.error("--lookback-days 必須介於 14 與 180")
    if not 1 <= args.max_requests <= 10:
        parser.error("--max-requests 必須介於 1 與 10")
    if not 0 <= args.batch_offset <= 1000:
        parser.error("--batch-offset 必須介於 0 與 1000")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("缺少 DATABASE_URL")

    now = datetime.now(ZoneInfo("Asia/Taipei"))
    start = (now.date() - timedelta(days=args.lookback_days)).isoformat()
    end = latest_due_market_date(now)
    official_dates = expected_market_dates(start, end)

    with psycopg.connect(database_url, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                """SELECT date, market, code FROM public.prices
                   WHERE date >= %s AND date <= %s
                     AND market IN ('TWSE','TPEX')""",
                (start, end),
            )
            stored_rows = cur.fetchall()

    latest, batches = choose_batches(
        stored_rows, official_dates, max_requests=args.max_requests,
        batch_offset=args.batch_offset,
    )
    print("OFFICIAL_GAP_SELECTION " + json.dumps({
        "latest_complete_date": latest,
        "batch_offset": args.batch_offset,
        "selected": [{k: (len(v) if k == "suspected_dates" else v)
                      for k, v in b.items()} for b in batches],
        "mode": "READ_ONLY",
    }, ensure_ascii=False, sort_keys=True), flush=True)

    results, errors = compare_batches(batches, fetch_verified_month)
    report = {
        "latest_complete_date": latest,
        "batch_offset": args.batch_offset,
        "checked_groups": len(results),
        "failed_groups": len(errors),
        "confirmed_missing_quotes": sum(
            len(x["confirmed_missing_quote_dates"]) for x in results
        ),
        "unresolved_quotes": sum(len(x["unresolved_dates"]) for x in results),
        "official_listed_no_valid_close": sum(
            len(x["official_listed_no_valid_close_dates"]) for x in results
        ),
        "official_date_not_listed": sum(
            len(x["official_date_not_listed_dates"]) for x in results
        ),
        "results": results,
        "errors": errors,
        "mode": "READ_ONLY",
    }
    print("OFFICIAL_GAP_SUMMARY " + json.dumps(
        report, ensure_ascii=False, sort_keys=True
    ), flush=True)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as output:
            output.write(markdown_report(report))
    if batches and not results:
        raise RuntimeError("全部官方月行情核對失敗；不應標記任何缺漏為已確認")


if __name__ == "__main__":
    main()
