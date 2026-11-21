"""V2.3 resumable historical sampling: bounded, research-only, no database access.

Reads official calendar CSV and reuses existing v23_research_batch /
v23_official_sample parsing. Splits requested dates into 5-day batches;
a single invocation fetches at most two NEW batches (20 source-date GETs).

Default DRY RUN. Use --fetch --windows-trust for a bounded local download.
Re-running exactly the same command validates completed batches and
continues with the next uncompleted pair of 5-day batches.

All output stays in v23_research_samples/. NO Supabase or GitHub writes.
"""
from __future__ import annotations

import argparse
import csv
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

from v23_research_batch import MARKETS, load_or_get, sample_counts

MAX_BATCH_DAYS = 5
MAX_NEW_BATCHES = 2
MAX_RETRIES = 3
FIRST_ALLOWED = date(2021, 1, 4)
LAST_ALLOWED = date(2026, 11, 20)


def select_calendar(path: str | Path, start: str, end: str) -> list[str]:
    left = date.fromisoformat(start)
    right = date.fromisoformat(end)
    if left > right or left < FIRST_ALLOWED or right > LAST_ALLOWED:
        raise ValueError("Requested date range outside verified study window")
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "trade_date" not in reader.fieldnames:
            raise ValueError("Official calendar is missing trade_date column")
        all_days = [row["trade_date"] for row in reader]
    if not all_days or all_days != sorted(set(all_days)):
        raise ValueError("Official calendar requires sorted unique trade dates")
    parsed = [date.fromisoformat(day) for day in all_days]
    if any(d.weekday() >= 5 for d in parsed):
        raise ValueError("Weekend found in official trading day calendar")
    selected = [d.isoformat() for d in parsed if left <= d <= right]
    if not selected:
        raise ValueError("No official trading days in selected period")
    return selected


def groups_of_five(days: list[str]) -> list[list[str]]:
    return [days[i:i + MAX_BATCH_DAYS] for i in range(0, len(days), MAX_BATCH_DAYS)]


def summary_file(folder: Path, days: list[str]) -> Path:
    if not days or len(days) > MAX_BATCH_DAYS:
        raise ValueError("A batch requires 1 to 5 trade dates")
    return folder / f"v23_resume_{days[0].replace('-', '')}_{days[-1].replace('-', '')}.csv"


def verify_complete_batch(folder: Path, days: list[str], summary: Path) -> None:
    """Refuse to skip a batch unless all its raw JSON files and checksums agree."""
    expected = {(day, market) for day in days for market in MARKETS}
    with summary.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        needed = {"trade_date", "market", "date_validation", "four_digit_unique",
                  "valid_close_with_volume", "missing_close"}
        if not reader.fieldnames or not needed.issubset(reader.fieldnames):
            raise ValueError(f"Summary missing required columns: {summary}")
        entries = list(reader)
    actual = [(r["trade_date"], r["market"]) for r in entries]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError(f"Summary is incomplete or contains duplicates: {summary}")
    for row in entries:
        day, market = row["trade_date"], row["market"]
        payload, evidence, _ = load_or_get(folder, market, day, fetch=False)
        if evidence["status"] != row["date_validation"]:
            raise ValueError(f"Date verification changed: {day} {market}")
        values = sample_counts(market, day, payload, evidence)
        if any(str(values[key]) != str(row[key]) for key in values):
            raise ValueError(f"Summary differs from checked official JSON: {day} {market}")


def get_pair_with_retries(folder: Path, market: str, day: str):
    """Retry only transient network failures; never retry malformed market data."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            payload, evidence, source = load_or_get(folder, market, day, fetch=True)
            counts = sample_counts(market, day, payload, evidence)
            return evidence, source, counts
        except HTTPError as exc:
            # Rate limits and authorization errors must not be hammered.
            if exc.code not in (408, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                raise
            print(f"RETRY_HTTP_{exc.code}: {day} {market} attempt={attempt}", flush=True)
        except (URLError, TimeoutError, ConnectionResetError) as exc:
            if attempt == MAX_RETRIES:
                raise
            print(f"RETRY_NETWORK: {day} {market} attempt={attempt} {type(exc).__name__}", flush=True)
        time.sleep(4 * attempt)
    raise RuntimeError("Retries unexpectedly exhausted")


def save_summary_atomic(path: Path, rows: list[dict]) -> None:
    """Write to a temporary file then rename; do not replace an existing report."""
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite batch report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8-sig",
            dir=path.parent, prefix=path.stem + "_", suffix=".tmp",
            delete=False,
        ) as stream:
            tmp_path = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise FileExistsError(f"Report appeared during write: {path}")
        tmp_path.rename(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def run_new_batch(folder: Path, days: list[str]) -> Path:
    report = summary_file(folder, days)
    if report.exists():
        raise FileExistsError(f"Batch already exists; inspect first: {report}")
    rows = []
    for day in days:
        for market in MARKETS:
            evidence, source, counts = get_pair_with_retries(folder, market, day)
            rows.append({
                "trade_date": day, "market": market, "source": source,
                "date_validation": evidence["status"],
                "four_digit_unique": counts["four_digit_unique"],
                "valid_close_with_volume": counts["valid_close_with_volume"],
                "missing_close": counts["missing_close"],
                "security_type_verified": "False",
                "row_date_verified": "False",
            })
            print("CHECKED:", day, market, source, counts, flush=True)
            if source == "DOWNLOADED":
                time.sleep(1.5)
    save_summary_atomic(report, rows)
    verify_complete_batch(folder, days, report)
    return report


def main(argv=None) -> dict[str, int]:
    p = argparse.ArgumentParser(description="Safe resumable V2.3 research-only historical sampling")
    p.add_argument("--calendar", default="v23_trading_calendar_2021_2026.csv")
    p.add_argument("--start", default="2026-01-16")
    p.add_argument("--end", default="2026-06-30")
    p.add_argument("--output-dir", default="v23_research_samples")
    p.add_argument("--max-batches", type=int, choices=(1, 2), default=2)
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--windows-trust", action="store_true")
    args = p.parse_args(argv)
    days = select_calendar(args.calendar, args.start, args.end)
    partitions = groups_of_five(days)
    folder = Path(args.output_dir)
    print("MODE:", "DOWNLOAD_OFFICIAL_JSON_ONLY" if args.fetch else "DRY_RUN")
    print("OFFICIAL_DAYS_SELECTED:", len(days))
    print("ALL_BATCHES:", len(partitions))
    print("MAX_NEW_BATCHES_THIS_RUN:", args.max_batches)
    print("MAX_NEW_HTTP_GETS_THIS_RUN:", args.max_batches * MAX_BATCH_DAYS * len(MARKETS))
    print("DATABASE_WRITES: 0")
    if args.fetch and args.windows_trust:
        import truststore
        truststore.inject_into_ssl()
    completed = new_count = planned = 0
    for block in partitions:
        report = summary_file(folder, block)
        if report.exists():
            verify_complete_batch(folder, block, report)
            completed += 1
            print("VERIFIED_EXISTING_BATCH:", report, flush=True)
            continue
        if new_count >= args.max_batches:
            continue
        if not args.fetch:
            planned += 1
            new_count += 1
            print("PLAN_BATCH:", block[0], block[-1], "days=", len(block))
            continue
        print("START_BATCH:", block[0], block[-1], flush=True)
        report = run_new_batch(folder, block)
        print("SAVED_BATCH:", report, flush=True)
        new_count += 1
    print("COMPLETED_PRIOR_BATCHES:", completed)
    print("NEW_BATCHES_THIS_RUN:", new_count if args.fetch else 0)
    print("PLANNED_BATCHES:", planned)
    print("REMAINING_BATCHES:", len(partitions) - completed - (new_count if args.fetch else 0))
    print("DATABASE_WRITES: 0")
    return {"previously_completed": completed, "new": new_count if args.fetch else 0,
            "remaining": len(partitions) - completed - (new_count if args.fetch else 0)}


if __name__ == "__main__":
    main()
