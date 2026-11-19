"""Automated, bounded TWSE 2026-03..06 history recovery.

Manual default: DRY_RUN. Scheduled writes require GitHub repo variable
TWSE_MAR_JUN_AUTO_ENABLED=true. Failing months are cooled down and then
quarantined for review; existing daily prices are never overwritten.
"""
import argparse
import os
import time
from datetime import date, datetime, timezone

import psycopg
import requests

from stock_list import get_twse_stock_codes
from market_backfill import fetch_month, parse_month, verified_before_listing
from twse_mar_jun_pilot import write_month
from twse_recovery_status import (
    COOLDOWN, MAX_ATTEMPTS, build_status, read_state, report_status,
)

MONTHS = tuple(date(2026, m, 1) for m in (3, 4, 5, 6))
MONTH_KEYS = [m.strftime("%Y-%m") for m in MONTHS]

CREATE_FAILURES = """
    CREATE TABLE IF NOT EXISTS public.twse_mar_jun_failures (
        code TEXT NOT NULL,
        month TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_error TEXT NOT NULL,
        PRIMARY KEY (code, month)
    )
"""
UPSERT_FAILURE = """
    INSERT INTO public.twse_mar_jun_failures
        (code, month, attempts, last_attempt_at, last_error)
    VALUES (%s, %s, 1, now(), %s)
    ON CONFLICT (code, month) DO UPDATE SET
        attempts = public.twse_mar_jun_failures.attempts + 1,
        last_attempt_at = now(),
        last_error = EXCLUDED.last_error
"""


def ensure_failure_table(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_FAILURES)
    conn.commit()


def load_state(conn):
    progress, counts, errors = read_state(conn, MONTH_KEYS)
    completed = {
        key for key, n in progress.items()
        if n > 0 and counts.get(key, 0) >= n
    }
    return completed, errors


def due_for_retry(record, current):
    if record is None:
        return True
    attempts, last = record
    if attempts >= MAX_ATTEMPTS:
        return False
    return current - last >= COOLDOWN


def record_failure(conn, code, month, message):
    with conn.cursor() as cur:
        cur.execute(UPSERT_FAILURE, (code, month, message[:600]))
    conn.commit()


def clear_failure(conn, code, month):
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM public.twse_mar_jun_failures
               WHERE code=%s AND month=%s""",
            (code, month),
        )
    conn.commit()


def get_codes_with_retries():
    last_exc = None
    for attempt in range(1, 4):
        try:
            return get_twse_stock_codes()
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_exc = exc
            print(f"STOCK_LIST_RETRY {attempt}/3: {exc}", flush=True)
            if attempt < 3:
                time.sleep(5 * attempt)
    raise RuntimeError(f"TWSE stock list unavailable: {last_exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--status-only", action="store_true",
                        help="Read progress only; no monthly downloads or DB writes.")
    args = parser.parse_args()

    if args.write and args.status_only:
        parser.error("--status-only cannot be combined with --write")

    if not 1 <= args.batch_size <= 5:
        parser.error("Batch size must be 1..5.")
    if not 1.5 <= args.delay <= 30:
        parser.error("Delay must be 1.5..30 seconds.")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("Missing DATABASE_URL")

    codes = get_codes_with_retries()
    now = datetime.now(timezone.utc)
    with psycopg.connect(db_url, connect_timeout=15) as conn:
        conn.read_only = not args.write
        if args.write:
            ensure_failure_table(conn)
        status = build_status(codes, MONTHS, *read_state(conn, MONTH_KEYS), now)
        if args.status_only:
            report_status(status, "STATUS_ONLY")
            return
        pending_codes = len(status["due_by_code"])
        quarantined = status["states"]["manual_review"]
        cooling = status["states"]["cooldown"]
        selected = [
            (code, [date.fromisoformat(m + "-01") for m in months])
            for code, months in list(status["due_by_code"].items())[:args.batch_size]
        ]

        mode = "WRITE" if args.write else "DRY_RUN"
        print(
            f"TWSE_AUTO_SELECTION total_codes={len(codes)} "
            f"due_codes={pending_codes} cooldown_months={cooling} "
            f"manual_review_months={quarantined} "
            f"selected={','.join(code for code, _ in selected)} mode={mode}",
            flush=True,
        )

        checked = 0
        inserted_total = 0
        errors = []
        with requests.Session() as session:
            for code, months in selected:
                for month in months:
                    key = month.strftime("%Y-%m")
                    label = f"{code} {key}"
                    try:
                        payload = fetch_month(session, code, month)
                        rows = parse_month(payload, code, key)
                        if len(rows) < 10:
                            raise ValueError(
                                f"Only {len(rows)} valid rows; official listing/no-trading "
                                "review needed before completion."
                            )
                        inserted = write_month(conn, code, key, rows) if args.write else 0
                        if args.write:
                            clear_failure(conn, code, key)
                        checked += 1
                        inserted_total += inserted
                        print(
                            f"{'WRITTEN' if args.write else 'CHECK'} {label}: "
                            f"valid={len(rows)} inserted={inserted}",
                            flush=True,
                        )
                    except Exception as exc:
                        conn.rollback()
                        errors.append(f"{label}: {exc}")
                        print(f"FAILED {label}: {exc}", flush=True)
                        if args.write:
                            record_failure(conn, code, key, str(exc))
                    time.sleep(args.delay)

        print(
            f"TWSE_AUTO_SUMMARY checked={checked} inserted={inserted_total} "
            f"failures={len(errors)} cooldown_months={cooling} "
            f"manual_review_months={quarantined} mode={mode}",
            flush=True,
        )
        for item in errors:
            print(f"REVIEW_DETAIL {item}", flush=True)
        status = build_status(
            codes, MONTHS, *read_state(conn, MONTH_KEYS), datetime.now(timezone.utc)
        )
        report_status(status, mode, checked, inserted_total, len(errors))
        if errors:
            # Continue other stocks in future runs; make the problem visible.
            print(
                f"::warning::TWSE historical batch had {len(errors)} errors; "
                "failed months have retry cooldown, see REVIEW_DETAIL.",
                flush=True,
            )
        if selected and checked == 0 and errors:
            raise RuntimeError("Every selected month failed; see REVIEW_DETAIL.")


if __name__ == "__main__":
    main()

