"""Guard for reliable TWSE/TPEx end-of-day daily quote recovery.

This script is intentionally standalone: it does not alter the historical
backfill system or its schedules. All triggers first verify that the latest
due, officially-calendar-verified market day is incomplete before requesting
fresh official snapshots. Successful repeated invocations are no-ops.

A GitHub recovery workflow_run event is a FALLBACK, not the primary schedule.
Skip its triggers outside the Taiwan evening repair window to avoid wasting
GitHub Actions minutes. Failed or partial official responses remain failures,
not fictitious complete days.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from daily_update import main as refresh_official_market_data
from daily_update import MIN_RECORDS
from tw_market_calendar import latest_due_market_date

EVENTS = frozenset({"push", "workflow_run", "schedule", "workflow_dispatch"})


def auto_event_allowed(event: str, now: datetime) -> bool:
    """Do not fetch unfinished intraday data from auxiliary triggers.

    Scheduled GitHub Actions can be delayed by many hours, including into
    the next UTC/Taiwan calendar date. Keep scheduled + manual runs eligible
    regardless of local clock, but only use the backfill-triggered fallback
    (or deploy-time push) during the evening local weekday repair window.
    """
    if event not in EVENTS:
        return False
    if event in ("workflow_dispatch", "schedule"):
        return True
    local = now.astimezone(ZoneInfo("Asia/Taipei"))
    return local.weekday() < 5 and 17 <= local.hour < 24


def coverage_complete(counts) -> bool:
    """Require both markets; a handful of historical backfill quotes is not
    a verified all-market trading-day snapshot."""
    return all(
        int(counts.get(market, 0)) >= MIN_RECORDS[market]
        for market in ("TWSE", "TPEX")
    )


def fetch_coverage(target: str, database_url: str) -> dict[str, int]:
    """Read exact trading-day coverage through the existing PostgreSQL URL."""
    import psycopg

    with psycopg.connect(database_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT market, count(DISTINCT code)
                   FROM public.prices
                   WHERE date=%s AND market IN ('TWSE','TPEX')
                   GROUP BY market""",
                (target,),
            )
            return {str(m): int(n) for m, n in cur.fetchall()}


def run_guard(*, event: str, now: datetime, database_url: str,
              get_counts=fetch_coverage, update=refresh_official_market_data):
    if now.tzinfo is None:
        raise ValueError("現在時間必須具備時區")
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL")
    if not auto_event_allowed(event, now):
        print(f"DAILY_GUARD_SKIPPED trigger={event} outside safe Taiwan window",
              flush=True)
        return {"result": "skipped", "date": None}

    # A 2027 market date MUST NOT be fabricated from weekdays alone.
    target = latest_due_market_date(now)
    before = get_counts(target, database_url)
    print(
        f"DAILY_GUARD_START target={target} event={event} "
        f"TWSE={before.get('TWSE', 0)} TPEx={before.get('TPEX', 0)}",
        flush=True,
    )
    if coverage_complete(before):
        print(f"DAILY_GUARD_ALREADY_COMPLETE target={target}", flush=True)
        return {"result": "already_complete", "date": target, "counts": before}

    # Official market fetcher performs separate per-market validity/date
    # checks; do not modify or bypass those checks.
    update()

    after = get_counts(target, database_url)
    if not coverage_complete(after):
        raise RuntimeError(
            f"官方更新執行後 {target} 仍不完整："
            f"TWSE={after.get('TWSE', 0)}, TPEx={after.get('TPEX', 0)}"
        )
    print(
        f"DAILY_GUARD_RECOVERED target={target} "
        f"TWSE={after['TWSE']} TPEx={after['TPEX']}",
        flush=True,
    )
    return {"result": "recovered", "date": target, "counts": after}


def main():
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    event = os.getenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    url = os.getenv("DATABASE_URL", "")
    result = run_guard(event=event, now=now, database_url=url)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        from pathlib import Path
        Path(summary).open("a", encoding="utf-8").write(
            f"\n### Daily Quote Guard\n"
            f"Event: {event}; Result: {result['result']}; "
            f"Date: {result['date'] or 'n/a'}\n"
        )


if __name__ == "__main__":
    main()
