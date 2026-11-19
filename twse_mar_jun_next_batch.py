"""V2 phase 1: select next five TWSE stocks to audit 2026 Mar-Jun.

Default dry-run: no writes. Requires DATABASE_URL to inspect completion.
The job is manual-only until batch behavior and exceptions are verified.
"""
import argparse
import os
import time
from datetime import date

import psycopg
import requests

from stock_list import get_twse_stock_codes
from market_backfill import (
    fetch_month,
    parse_month,
    verified_before_listing,
)
from twse_mar_jun_pilot import write_month


MONTHS = tuple(date(2026, m, 1) for m in (3, 4, 5, 6))
MONTH_KEYS = tuple(d.strftime("%Y-%m") for d in MONTHS)


def load_coverage(conn):
    """A completed progress marker is trusted only if rows still exist."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT code, month, row_count
               FROM public.backfill_progress
               WHERE market='TWSE' AND month = ANY(%s)""",
            (list(MONTH_KEYS),),
        )
        progress = {
            (str(code), month): int(n)
            for code, month, n in cur.fetchall()
        }
        cur.execute(
            """SELECT code, LEFT(date,7), COUNT(*)
               FROM public.prices
               WHERE market='TWSE'
                 AND date >= '2026-03-01'
                 AND date < '2026-07-01'
               GROUP BY code, LEFT(date,7)"""
        )
        existing = {
            (str(code), month): int(n)
            for code, month, n in cur.fetchall()
        }

    complete = {
        key for key, expected in progress.items()
        if expected > 0 and existing.get(key, 0) >= expected
    }
    return complete


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5:
        parser.error("Pilot batch size must be 1-5.")
    if not 1.5 <= args.delay <= 30:
        parser.error("Delay must be 1.5-30 seconds.")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL")

    codes = get_twse_stock_codes()
    with psycopg.connect(database_url, connect_timeout=15) as conn:
        completed = load_coverage(conn)
        pending = []
        for code in codes:
            missing = [
                month
                for month in MONTHS
                if not verified_before_listing(code, month)
                and (code, month.strftime("%Y-%m")) not in completed
            ]
            if missing:
                pending.append((code, missing))

        selected = pending[:args.batch_size]
        print(
            f"TWSE_BATCH_SELECTION candidate_codes={len(codes)}; "
            f"pending_codes={len(pending)}; "
            f"selected={','.join(code for code, _ in selected)}; "
            f"mode={'WRITE' if args.write else 'DRY_RUN'}",
            flush=True,
        )

        checked = 0
        inserted_total = 0
        failures = []

        with requests.Session() as session:
            for code, missing in selected:
                for month in missing:
                    key = month.strftime("%Y-%m")
                    label = f"{code} {key}"
                    try:
                        payload = fetch_month(session, code, month)
                        rows = parse_month(payload, code, key)
                        if len(rows) < 10:
                            raise ValueError(
                                f"Only {len(rows)} valid rows; needs listing "
                                "or official no-data review"
                            )

                        inserted = (
                            write_month(conn, code, key, rows)
                            if args.write else 0
                        )
                        checked += 1
                        inserted_total += inserted
                        print(
                            f"{'WRITTEN' if args.write else 'CHECK'} "
                            f"{label}: valid={len(rows)} inserted={inserted}",
                            flush=True,
                        )
                    except Exception as exc:
                        conn.rollback()
                        failures.append(f"{label}: {exc}")
                        print(f"FAILED {label}: {exc}", flush=True)

                    time.sleep(args.delay)

    print(
        f"TWSE_BATCH_SUMMARY checked={checked}; "
        f"inserted={inserted_total}; failures={len(failures)}; "
        f"mode={'WRITE' if args.write else 'DRY_RUN'}",
        flush=True,
    )
    for detail in failures:
        print(f"FAILED_DETAIL {detail}", flush=True)
    if failures:
        raise RuntimeError("Some pilot months require investigation.")


if __name__ == "__main__":
    main()
