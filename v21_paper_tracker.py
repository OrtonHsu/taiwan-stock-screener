"""V2.1 daily simulated-signal tracker (PostgreSQL only; no real orders).

Run ONLY after verified dual-market daily snapshots are stored.
--dry-run (default) is read-only; --write performs one idempotent transaction.
Never generate historical signals retrospectively; existing episodes may be
advanced across genuinely completed sessions after an interrupted workflow.
"""
from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from entry_timing import evaluate_entry_timing
from paper_tracker_core import (
    VERSION, EXPOSURE_STATES, advance_position, select_new_signals,
)
from trend_following import plan_trend_entries
from tw_market_calendar import latest_due_market_date


def read_quotes(conn, target: str):
    start = (date.fromisoformat(target) - timedelta(days=270)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT date,market,code,name,close,volume,amount
               FROM public.prices
               WHERE date >= %s AND date <= %s
                 AND market IN ('TWSE','TPEX')
               ORDER BY date,market,code""",
            (start, target),
        )
        quotes = pd.DataFrame(
            cur.fetchall(),
            columns=["date", "market", "code", "name", "close", "volume", "amount"],
        )
    if quotes.empty:
        raise RuntimeError("沒有可核對的雙市場行情")
    for key in ("date", "market", "code"):
        quotes[key] = quotes[key].astype(str)
    for key in ("close", "volume", "amount"):
        quotes[key] = pd.to_numeric(quotes[key], errors="coerce")
    counts = (
        quotes.groupby(["date", "market"])["code"]
        .nunique()
        .unstack(fill_value=0)
    )
    for market in ("TWSE", "TPEX"):
        if market not in counts:
            counts[market] = 0
    complete_dates = sorted(
        counts.index[(counts["TWSE"] >= 900) & (counts["TPEX"] >= 700)]
    )
    # Absolutely no stale or half-complete next-day labels.
    if not complete_dates or complete_dates[-1] != target:
        raise RuntimeError(
            f"最近完整雙市場日={complete_dates[-1] if complete_dates else '無'}；"
            f"應完成日={target}，拒絕模擬更新"
        )
    if len(complete_dates) < 65:
        raise RuntimeError(
            f"完整雙市場日只有{len(complete_dates)}天，未達65天品質門檻"
        )
    quotes = quotes[quotes["date"].isin(complete_dates)].copy()
    return quotes, complete_dates


def asof_close_and_ma20(group, day, market_dates):
    """One exact close and 20 consecutive full-market closes or no MA."""
    if group is None or day not in group:
        return None, None
    close = group[day]
    try:
        ix = market_dates.index(day)
    except ValueError:
        return None, None
    window = market_dates[max(0, ix - 19):ix + 1]
    if len(window) != 20 or any(d not in group for d in window):
        return close, None
    return close, sum(group[d] for d in window) / 20


def map_closes(quotes, active_positions):
    """Build small per-symbol lookup only for active paper positions."""
    keys = {(str(p["market"]), str(p["code"])) for p in active_positions}
    if not keys:
        return {}
    subset = quotes[
        pd.MultiIndex.from_frame(quotes[["market", "code"]]).isin(list(keys))
    ]
    result = {}
    for key, g in subset.groupby(["market", "code"]):
        # Duplicate quotes would invalidate a simulated price sequence.
        if g["date"].duplicated().any():
            raise RuntimeError(f"重複的模擬行情日期: {key}")
        result[tuple(key)] = {
            str(row.date): float(row.close)
            for row in g.itertuples(index=False)
            if pd.notna(row.close) and float(row.close) > 0
        }
    return result


def load_positions(cur):
    from psycopg.rows import dict_row
    with cur.connection.cursor(row_factory=dict_row) as c:
        c.execute(
            """SELECT id,strategy_version,market,code,name,setup,signal_date,
                      signal_close,signal_reason,status,entry_date,entry_close,
                      exit_date,exit_close,exit_reason,below_ma20_streak,
                      last_observed_date,last_observed_close,net_return_pct
               FROM stock_research.simulation_positions
               WHERE strategy_version=%s
               ORDER BY id""",
            (VERSION,),
        )
        return list(c.fetchall())


def save_transition(cur, p, obs):
    cur.execute(
        """UPDATE stock_research.simulation_positions
           SET status=%s,entry_date=%s,entry_close=%s,exit_date=%s,
               exit_close=%s,exit_reason=%s,below_ma20_streak=%s,
               last_observed_date=%s,last_observed_close=%s,
               net_return_pct=%s,updated_at=now()
           WHERE id=%s AND strategy_version=%s""",
        (
            p["status"], p.get("entry_date"), p.get("entry_close"),
            p.get("exit_date"), p.get("exit_close"), p.get("exit_reason"),
            p.get("below_ma20_streak", 0), p.get("last_observed_date"),
            p.get("last_observed_close"), p.get("net_return_pct"),
            p["id"], VERSION,
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError("模擬部位狀態未能正確更新，回滾整批")
    cur.execute(
        """INSERT INTO stock_research.simulation_daily (
               position_id,asof_date,close,ma20,holding_sessions,
               below_ma20_streak,unrealized_net_pct,state,note
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(position_id,asof_date) DO NOTHING""",
        (
            obs["position_id"], obs["asof_date"], obs["close"],
            obs["ma20"], obs["holding_sessions"],
            obs["below_ma20_streak"], obs["unrealized_net_pct"],
            obs["state"], obs["note"],
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError("模擬追蹤同日紀錄已存在，拒絕覆蓋以免績效失真")


def advance_all(cur, positions, quotes, days, *, write):
    """Replay the existing paper episodes ONLY, never fabricate old signals."""
    active = [p for p in positions if p["status"] in EXPOSURE_STATES]
    prices = map_closes(quotes, active)
    updated = 0
    for source in active:
        p = dict(source)
        by_day = prices.get((str(p["market"]), str(p["code"])), {})
        for day in days:
            if day <= str(p["last_observed_date"] or p["signal_date"]):
                continue
            if p["status"] not in EXPOSURE_STATES:
                break
            close, ma20 = asof_close_and_ma20(by_day, day, days)
            entry_day = str(p["entry_date"]) if p.get("entry_date") else None
            age = sum(entry_day < d <= day for d in days) if entry_day else 0
            p, obs = advance_position(p, day, close, ma20, age)
            if obs is not None:
                updated += 1
                if write:
                    save_transition(cur, p, obs)
        source.update(p)
    return updated


def existing_recent_exits(positions):
    result = {}
    for p in positions:
        if p["status"] != "closed" or p["exit_date"] is None:
            continue
        key = (str(p["market"]), str(p["code"]))
        day = str(p["exit_date"])
        if key not in result or day > result[key]:
            result[key] = day
    return result


def write_new_signals(cur, fresh):
    created = 0
    for rec in fresh:
        cur.execute(
            """INSERT INTO stock_research.simulation_positions (
                 strategy_version,market,code,name,setup,
                 signal_date,signal_close,signal_reason,
                 next_session_check,invalidation_reference,
                 status,last_observed_date,last_observed_close
               ) VALUES (
                 %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending_entry',%s,%s
               ) ON CONFLICT DO NOTHING RETURNING id""",
            (
                VERSION,str(rec.market),str(rec.code),str(rec.name),
                str(rec.stage),str(rec.date),float(rec.close),
                str(rec.reason),str(rec.next_session_check),
                float(rec.invalidation_reference),
                str(rec.date),float(rec.close),
            ),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("候選持倉唯一性衝突，拒絕靜默遺漏或重複")
        cur.execute(
            """INSERT INTO stock_research.simulation_daily (
               position_id,asof_date,close,holding_sessions,
               below_ma20_streak,state,note
               ) VALUES (%s,%s,%s,0,0,'pending_entry',
                         '訊號日：尚未進場；下個完整交易日收盤才建立代理價格')""",
            (row[0],str(rec.date),float(rec.close)),
        )
        created += 1
    return created


def process(conn, *, write=False, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    asof = latest_due_market_date(now)
    from psycopg.rows import dict_row
    with conn.transaction():
        with conn.cursor() as cur:
            # Advisory transaction lock prevents two simultaneous workflow
            # invocations from opening duplicate paper positions.
            if write:
                cur.execute("SELECT pg_advisory_xact_lock(20261120,21)")
            quotes, complete_days = read_quotes(conn, asof)
            timing = evaluate_entry_timing(quotes, complete_days)
            plans = plan_trend_entries(timing)
            candidates = plans[
                plans["decision"] == "趨勢進場研究候選"
            ] if not plans.empty else plans

            positions = load_positions(cur)
            cur.execute(
                """SELECT 1 FROM stock_research.simulation_runs
                   WHERE strategy_version=%s AND asof_date=%s""",
                (VERSION, asof),
            )
            already_recorded = cur.fetchone() is not None
            if already_recorded:
                print(f"PAPER_TRACKER_ALREADY_RECORDED asof={asof} version={VERSION}")
                return {"date": asof, "already_recorded": True}

            updates = advance_all(
                cur, positions, quotes, complete_days, write=write
            )
            active = {
                (str(p["market"]), str(p["code"]))
                for p in positions if p["status"] in EXPOSURE_STATES
            }
            fresh = select_new_signals(
                plans, active, existing_recent_exits(positions),
                complete_days,
            )
            dupes = max(0, len(candidates) - len(fresh))
            if write:
                written = write_new_signals(cur, fresh)
                cur.execute(
                    """INSERT INTO stock_research.simulation_runs (
                         strategy_version,asof_date,candidates,new_signals,
                         duplicates,active_positions
                       ) VALUES (%s,%s,%s,%s,%s,%s)""",
                    (VERSION,asof,len(candidates),written,dupes,
                     len(active) + written),
                )
            else:
                written = 0
            print(
                f"PAPER_TRACKER_{'WRITE' if write else 'PREVIEW'} "
                f"asof={asof} candidates={len(candidates)} "
                f"new={'%d' % written if write else len(fresh)} "
                f"active={len(active)} observations={updates} "
                f"excluded_or_held={dupes}",
                flush=True,
            )
            return {
                "date": asof, "candidates": len(candidates),
                "new": written if write else len(fresh),
                "active": len(active),
                "observations": updates,
                "already_recorded": False,
            }


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="Commit daily simulations (trusted workflow only)")
    mode.add_argument("--dry-run", action="store_true",
                      help="Read-only eligibility preview (default)")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("Missing DATABASE_URL")
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as conn:
        process(conn, write=bool(args.write))


if __name__ == "__main__":
    main()
