"""V2.3 historical backfill planning ONLY: offline CSV reconciliation.

No database connection, no HTTP requests, no changes to live stock systems.

Example:
  python v23_offline_backfill_plan.py --calendar v23_trading_calendar_2021_2026.csv --coverage v23_daily_coverage_2021_2026.csv --start 2026-01-01 --end 2026-06-30 --output v23_backfill_plan_2026H1.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date
from pathlib import Path

START_LIMIT = date(2021, 1, 4)
END_LIMIT = date(2026, 11, 20)


def parse_day(value: str) -> date:
    result = date.fromisoformat(value)
    if not START_LIMIT <= result <= END_LIMIT:
        raise ValueError(f"Outside validated calendar window: {value}")
    return result


def read_csv(path: str | Path, expected: set[str]) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as fp:
        reader = csv.DictReader(fp)
        if not reader.fieldnames or not expected.issubset(reader.fieldnames):
            raise ValueError(f"CSV missing columns {sorted(expected)}: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV empty: {path}")
    dates = [parse_day(r["trade_date"]) for r in rows]
    if dates != sorted(set(dates)):
        raise ValueError(f"Trade dates must be unique and increasing: {path}")
    if any(d.weekday() >= 5 for d in dates):
        raise ValueError(f"Weekend detected in market-calendar file: {path}")
    return rows


def assess(twse: int, tpex: int, *, twse_limit=900, tpex_limit=700) -> tuple[str, str]:
    """Provisional 2026 research thresholds, NOT certified historical listing counts."""
    if twse < 0 or tpex < 0:
        raise ValueError("Negative symbol count")
    if twse == 0 and tpex == 0:
        return "MISSING_BOTH", "P0"
    if twse == 0:
        return "MISSING_TWSE", "P0"
    if tpex == 0:
        return "MISSING_TPEX", "P0"
    if twse < twse_limit and tpex < tpex_limit:
        return "BOTH_UNDER_PROVISIONAL_THRESHOLDS", "P1"
    if twse < twse_limit:
        return "TWSE_UNDER_PROVISIONAL_THRESHOLD", "P1"
    if tpex < tpex_limit:
        return "TPEX_UNDER_PROVISIONAL_THRESHOLD", "P1"
    return "PROVISIONAL_ONLY_NEEDS_OFFICIAL_VERIFICATION", "P2"


def build_plan(calendar: list[dict[str, str]], coverage: list[dict[str, str]],
               start: date, end: date) -> list[dict[str, object]]:
    if start > end:
        raise ValueError("Start is after end")
    calendar_dates = {r["trade_date"] for r in calendar}
    coverage_dates = {r["trade_date"] for r in coverage}
    if calendar_dates != coverage_dates:
        raise ValueError("Coverage calendar differs from official dates; do not infer missing data")
    by_day = {r["trade_date"]: r for r in coverage}
    output = []
    for row in calendar:
        day = parse_day(row["trade_date"])
        if not start <= day <= end:
            continue
        entry = by_day[row["trade_date"]]
        twse = int(entry["twse_unique_codes"])
        tpex = int(entry["tpex_unique_codes"])
        has_both = int(entry["has_both_markets"])
        if has_both not in (0, 1) or has_both != int(twse > 0 and tpex > 0):
            raise ValueError(f"Coverage flag inconsistent with symbol counts: {day}")
        status, priority = assess(twse, tpex)
        output.append({
            "trade_date": day.isoformat(),
            "twse_unique_codes": twse,
            "tpex_unique_codes": tpex,
            "status": status,
            "priority": priority,
            "official_calendar_source": row.get("source", "TWSE_FMTQIK"),
            "next_action": (
                "Probe official TWSE and TPEx dates; do not persist"
                if priority == "P0" else
                "Compare per-code official TWSE/TPEx coverage; do not overwrite"
                if priority == "P1" else
                "Audit duplicate keys and official prices before acceptance"
            ),
        })
    if not output:
        raise ValueError("No official trading dates in selected period")
    return output


def save_plan(rows: list[dict[str, object]], output: str | Path,
              source_files: tuple[str | Path, str | Path]) -> None:
    dest = Path(output).resolve()
    if dest in {Path(p).resolve() for p in source_files}:
        raise ValueError("Output path must not replace an input CSV")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> dict[str, int]:
    cli = argparse.ArgumentParser(description="V2.3 offline-only historical backfill planner")
    cli.add_argument("--calendar", default="v23_trading_calendar_2021_2026.csv")
    cli.add_argument("--coverage", default="v23_daily_coverage_2021_2026.csv")
    cli.add_argument("--start", default="2026-01-01")
    cli.add_argument("--end", default="2026-06-30")
    cli.add_argument("--output", default="v23_backfill_plan_2026H1.csv")
    args = cli.parse_args(argv)
    start, end = parse_day(args.start), parse_day(args.end)
    calendar = read_csv(args.calendar, {"trade_date", "source"})
    coverage = read_csv(args.coverage, {"trade_date", "has_both_markets",
                                       "twse_unique_codes", "tpex_unique_codes"})
    rows = build_plan(calendar, coverage, start, end)
    save_plan(rows, args.output, (args.calendar, args.coverage))
    counts = Counter(row["status"] for row in rows)
    print("OFFLINE_ONLY: True; Supabase/GitHub unaffected")
    print("OFFICIAL_DAYS:", len(rows))
    for status, count in sorted(counts.items()):
        print(status + ":", count)
    print("OUTPUT:", Path(args.output).resolve())
    return dict(counts)


if __name__ == "__main__":
    main()
