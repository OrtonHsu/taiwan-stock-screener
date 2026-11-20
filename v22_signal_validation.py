"""V2.2 transparent, read-only study of entry confirmation and fixed horizons.

Only immutable V2.1 *recorded* signals are used; do not reconstruct
signals after observing outcomes. Confirmation uses the FIRST complete market
session after the signal. A passed confirmation enters at the SECOND complete
session's closing proxy (not the confirmation-day close). V2.1's previously
recorded T+1 paper entry is not modified.

Study protocol V2.2-A was fixed on 2026-11-20. Signals before 2026-11-23
may appear as illustrative diagnostics only and MUST NOT contribute to V2.2
prospective results. Nothing here is a broker-executable fill, forecast,
calibrated win rate, or causal proof of improved returns.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from paper_tracker_core import net_return_pct

PROTOCOL = "V2.2-A-20261120"
PROSPECTIVE_FROM = "2026-11-23"
HORIZONS = (3, 5, 10, 20)
UPPER_CHASE_LIMIT = 1.05
SIGNAL_COLUMNS = [
    "market", "code", "name", "setup", "signal_date", "signal_close",
    "reference", "cohort", "t1_date", "t1_close", "t1_ma20",
    "check_status", "check_reason", "v21_entry_date", "v21_entry_close",
    "v22_entry_date", "v22_entry_close", "protocol",
]
HORIZON_COLUMNS = [
    "market", "code", "name", "signal_date", "cohort", "strategy",
    "entry_date", "entry_close", "horizon", "evaluation_date",
    "status", "gross_pct", "net_pct", "worst_close_drawdown_pct",
]


def _quotes(prices: pd.DataFrame, sessions: list[str]) -> dict[tuple[str, str], dict[str, float]]:
    if prices is None or prices.empty or not sessions:
        return {}
    required = {"date", "market", "code", "close"}
    if not required.issubset(prices.columns):
        raise ValueError(f"行情缺少欄位: {sorted(required - set(prices.columns))}")
    q = prices[list(required)].copy()
    for k in ("date", "market", "code"):
        q[k] = q[k].astype(str)
    q["close"] = pd.to_numeric(q["close"], errors="coerce")
    q = q[q["date"].isin(sessions) & q["market"].isin(("TWSE", "TPEX"))]
    q = q[q["close"].notna() & (q["close"] > 0)]
    if q.duplicated(["date", "market", "code"]).any():
        raise ValueError("行情有同一市場／股票／日期重複資料，暫停驗證")
    return {
        (str(m), str(code)): dict(zip(g["date"], g["close"].astype(float)))
        for (m, code), g in q.groupby(["market", "code"])
    }


def _ma20(history: dict[str, float], sessions: list[str], pos: int):
    if pos < 19:
        return None
    window = sessions[pos - 19:pos + 1]
    if any(d not in history for d in window):
        return None
    return sum(history[d] for d in window) / 20


def check_next_close(setup: str, signal_close: float, reference: float,
                     t1_close, t1_ma20) -> tuple[str, str]:
    """Decide based on T+1-only information; no T+2 price lookup."""
    if t1_close is None or t1_ma20 is None:
        return "無法確認", "T+1行情缺漏或最近20日收盤不連續"
    price, avg = float(t1_close), float(t1_ma20)
    if not all(x > 0 for x in (signal_close, reference, price, avg)):
        return "無法確認", "價格或參考價異常"
    if price > signal_close * UPPER_CHASE_LIMIT:
        return "未通過", "隔日收盤高於訊號價5%，不再追價"
    if setup == "收盤突破觀察":
        if price >= reference and price > avg:
            return "通過", "隔日收盤守住原突破參考價且高於當日MA20"
        return "未通過", "隔日未守原突破參考價或低於MA20"
    if setup == "回測初步止跌":
        if price >= reference and price >= avg:
            return "通過", "隔日收盤仍守訊號日MA20參考價與當日MA20"
        return "未通過", "隔日未守住MA20參考價"
    return "無法確認", "未知的原始訊號類型"


def _fixed_horizon_rows(record, strategy, entry_day, entry_close, history,
                        dates, index):
    if entry_day not in index or entry_close is None:
        return []
    start = index[entry_day]
    if entry_day not in history or abs(history[entry_day] - float(entry_close)) > 0.011:
        # Repaired official closes conflicting with paper entry must be audited.
        return []
    out = []
    for horizon in HORIZONS:
        end = start + horizon
        done = end < len(dates)
        evaluation_date = dates[end] if done else None
        window = dates[start:end + 1] if done else []
        continuous = done and all(d in history for d in window)
        gross = net = dd = None
        if continuous:
            exit_close = history[evaluation_date]
            gross = round((exit_close / float(entry_close) - 1) * 100, 4)
            net = net_return_pct(entry_close, exit_close)
            dd = round((min(history[d] for d in window) / float(entry_close) - 1) * 100, 4)
        out.append({
            "market": record["market"], "code": record["code"],
            "name": record["name"], "signal_date": record["signal_date"],
            "cohort": record["cohort"], "strategy": strategy,
            "entry_date": entry_day, "entry_close": round(float(entry_close), 4),
            "horizon": horizon, "evaluation_date": evaluation_date,
            "status": ("已完成" if continuous else
                       "行情不連續" if done else "尚未到期"),
            "gross_pct": gross, "net_pct": net,
            "worst_close_drawdown_pct": dd,
        })
    return out


def evaluate_recorded_signals(positions: pd.DataFrame, prices: pd.DataFrame,
                              complete_dates: Iterable[str], asof: str | None = None):
    """Return (signal diagnostics, fixed horizon records) with no writes.

    Requires caller-verified full-market complete dates. Uses each stored
    V2.1 signal/entry; does not infer entries from later stock price moves.
    """
    empty_signals = pd.DataFrame(columns=SIGNAL_COLUMNS)
    empty_horizons = pd.DataFrame(columns=HORIZON_COLUMNS)
    if positions is None or positions.empty:
        return empty_signals, empty_horizons
    required = {
        "market", "code", "name", "setup", "signal_date", "signal_close",
        "invalidation_reference", "entry_date", "entry_close",
    }
    if not required.issubset(positions.columns):
        raise ValueError(f"追蹤訊號缺少欄位: {sorted(required - set(positions.columns))}")
    dates = sorted(set(str(d) for d in complete_dates))
    if asof:
        dates = [d for d in dates if d <= str(asof)]
    if not dates:
        return empty_signals, empty_horizons
    ix = {d: i for i, d in enumerate(dates)}
    quotes = _quotes(prices, dates)
    signal_rows, horizon_rows = [], []

    seen = set()
    for p in positions.to_dict("records"):
        market, code = str(p["market"]), str(p["code"])
        signal_date = str(p["signal_date"])
        if signal_date not in ix:
            continue
        key = (market, code, signal_date)
        if key in seen:
            raise ValueError("同一股票、訊號日有重複原始紀錄，拒絕重複統計")
        seen.add(key)
        signal_close = float(p["signal_close"])
        reference = float(p["invalidation_reference"])
        if signal_close <= 0 or reference <= 0:
            raise ValueError("原始訊號價格必須為正")

        cohort = "前瞻樣本" if signal_date >= PROSPECTIVE_FROM else "規則制定前示例"
        history = quotes.get((market, code), {})
        t1_ix = ix[signal_date] + 1
        t1_day = dates[t1_ix] if t1_ix < len(dates) else None
        t1_close = history.get(t1_day) if t1_day else None
        t1_ma20 = _ma20(history, dates, t1_ix) if t1_day else None

        if t1_day is None:
            state, reason = "等待T+1", "下一個完整市場交易日尚未到期"
        else:
            state, reason = check_next_close(
                str(p["setup"]), signal_close, reference, t1_close, t1_ma20
            )

        recorded_entry_date = str(p["entry_date"]) if pd.notna(p["entry_date"]) else None
        recorded_entry_price = (
            float(p["entry_close"]) if pd.notna(p["entry_close"]) else None
        )
        # V2.1 baseline is allowed only if the paper tracker *actually*
        # recorded a T+1 entry; never synthesize a fill from market prices.
        if recorded_entry_date != t1_day:
            recorded_entry_date, recorded_entry_price = None, None

        t2_day, t2_close = None, None
        if state == "通過" and t1_ix + 1 < len(dates):
            t2_day = dates[t1_ix + 1]
            t2_close = history.get(t2_day)
            if t2_close is None:
                state, reason = "T+2缺價", "確認通過，但進場代理日無個股收盤價"
        elif state == "通過":
            state, reason = "等待T+2", "確認通過，待下一完整交易日收盤代理進場"

        if cohort != "前瞻樣本":
            # Older signals can explain why the rule was proposed, but may
            # not be counted as independent V2.2 performance evidence.
            t2_day, t2_close = None, None

        row = {
            "market": market, "code": code, "name": str(p["name"]),
            "setup": str(p["setup"]), "signal_date": signal_date,
            "signal_close": round(signal_close, 4), "reference": round(reference, 4),
            "cohort": cohort, "t1_date": t1_day, "t1_close": t1_close,
            "t1_ma20": round(t1_ma20, 4) if t1_ma20 is not None else None,
            "check_status": state, "check_reason": reason,
            "v21_entry_date": recorded_entry_date,
            "v21_entry_close": recorded_entry_price,
            "v22_entry_date": t2_day if t2_close is not None else None,
            "v22_entry_close": t2_close,
            "protocol": PROTOCOL,
        }
        signal_rows.append(row)
        if recorded_entry_date is not None:
            horizon_rows.extend(_fixed_horizon_rows(
                row, "V2.1次日代理進場", recorded_entry_date,
                recorded_entry_price, history, dates, ix,
            ))
        if t2_day is not None and t2_close is not None:
            horizon_rows.extend(_fixed_horizon_rows(
                row, "V2.2隔日確認進場", t2_day, t2_close,
                history, dates, ix,
            ))

    return (
        pd.DataFrame(signal_rows, columns=SIGNAL_COLUMNS),
        pd.DataFrame(horizon_rows, columns=HORIZON_COLUMNS),
    )


def summarize_matured(horizons: pd.DataFrame) -> pd.DataFrame:
    """Descriptive cohort statistics, excluding pre-protocol V2.2 examples."""
    columns = ["策略", "交易日", "已到期樣本", "平均淨報酬%", "獲利比例%", "平均最大收盤回撤%"]
    if horizons is None or horizons.empty:
        return pd.DataFrame(columns=columns)
    eligible = horizons.loc[
        (horizons["status"] == "已完成")
        & (horizons["cohort"] == "前瞻樣本")
    ].copy()
    if eligible.empty:
        return pd.DataFrame(columns=columns)
    eligible["net_pct"] = pd.to_numeric(eligible["net_pct"], errors="coerce")
    eligible["worst_close_drawdown_pct"] = pd.to_numeric(
        eligible["worst_close_drawdown_pct"], errors="coerce"
    )
    out = []
    for (strategy, day), group in eligible.groupby(["strategy", "horizon"]):
        n = len(group)
        out.append({
            "策略": strategy, "交易日": int(day), "已到期樣本": n,
            "平均淨報酬%": round(float(group["net_pct"].mean()), 2),
            "獲利比例%": round(float((group["net_pct"] > 0).mean() * 100), 1),
            "平均最大收盤回撤%": round(
                float(group["worst_close_drawdown_pct"].mean()), 2
            ),
        })
    return pd.DataFrame(out, columns=columns).sort_values(["交易日", "策略"])
