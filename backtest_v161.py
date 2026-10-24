"""V1.6.1 EOD backtest with data-quality and exclusion diagnostics.

Read-only. Requires the existing backtest_v16.py and v15_strategy.py.
Reports distinguish strategy signals from data availability and waiting labels.
This is a close-to-close research simulation, NOT executable P&L.
"""
import argparse
from pathlib import Path

import pandas as pd

from backtest_v16 import load_prices, summarize, eligible_signal, MODES
from v15_strategy import rank_v15

TRADE_COLUMNS = [
    "mode", "signal_date", "market", "code", "name", "score", "risk_level",
    "entry_date", "entry_close", "exit_date", "exit_close", "holding_sessions",
    "gross_return_pct", "net_return_pct", "worst_close_drawdown_pct",
]
EXCLUSION_COLUMNS = ["signal_date", "mode", "code", "holding_sessions", "reason"]


def quality_table(p, calendar, min_twse=500, min_tpex=500, relative_floor=0.70):
    """Compare each day's distinct-code coverage with PREVIOUS 10 dates only."""
    by_day = (p.groupby(["date", "market"])["code"]
              .nunique().unstack(fill_value=0).reindex(calendar, fill_value=0))
    for market in ("TWSE", "TPEX"):
        if market not in by_day:
            by_day[market] = 0
    out = []
    for i, day in enumerate(calendar):
        prev = by_day.iloc[max(0, i - 10):i]
        twse, tpex = int(by_day.loc[day, "TWSE"]), int(by_day.loc[day, "TPEX"])
        twse_ref = float(prev["TWSE"].median()) if len(prev) else None
        tpex_ref = float(prev["TPEX"].median()) if len(prev) else None
        twse_ok = twse >= min_twse and (twse_ref is None or twse >= twse_ref * relative_floor)
        tpex_ok = tpex >= min_tpex and (tpex_ref is None or tpex >= tpex_ref * relative_floor)
        out.append({
            "date": day, "twse_stocks": twse, "tpex_stocks": tpex,
            "twse_previous10_median": twse_ref,
            "tpex_previous10_median": tpex_ref,
            "twse_coverage_ok": bool(twse_ok),
            "tpex_coverage_ok": bool(tpex_ok),
            "signal_day_usable": bool(twse_ok and tpex_ok),
        })
    return pd.DataFrame(out)


def run_backtest(p, signal_days, top, horizons, commission_pct, sell_tax_pct,
                 min_history, min_amount, min_twse, min_tpex):
    listed = p[p.market == "TWSE"].groupby("date").code.nunique()
    calendar = sorted(listed[listed >= min_twse].index)
    max_horizon = max(horizons)
    if len(calendar) < min_history + max_horizon + 2:
        raise ValueError("完整交易日不足；請先回補歷史行情，勿降低數據品質門檻")
    quality = quality_table(p, calendar, min_twse, min_tpex)
    quality_idx = quality.set_index("date")
    signal_dates = calendar[min_history - 1:-(max_horizon + 1)][-signal_days:]
    date_index = {d: i for i, d in enumerate(calendar)}
    close_lookup = p.set_index(["date", "market", "code"])["close"]
    trades, excluded, diagnostics = [], [], []

    for day in signal_dates:
        row = quality_idx.loc[day]
        diag = {
            "signal_date": day,
            "twse_stocks": int(row.twse_stocks),
            "tpex_stocks": int(row.tpex_stocks),
            "signal_day_usable": bool(row.signal_day_usable),
            "raw_candidates": 0,
            "eligible_candidates": 0,
            "waiting_or_high_risk": 0,
            "attempted_horizon_trades": 0,
            "completed_horizon_trades": 0,
            "missing_price_paths": 0,
        }
        if not row.signal_day_usable:
            excluded.append({"signal_date": day, "mode": "ALL", "code": "",
                             "holding_sessions": "", "reason": "訊號日市場覆蓋率不足"})
            diagnostics.append(diag)
            continue
        active_days = calendar[max(0, date_index[day] - 99):date_index[day] + 1]
        asof = p[p.date.isin(active_days)]
        ranked = rank_v15(asof, min_history=min_history, min_amount=min_amount)
        diag["raw_candidates"] = len(ranked)
        if ranked.empty:
            diagnostics.append(diag)
            continue
        allowed = ranked.apply(eligible_signal, axis=1)
        diag["waiting_or_high_risk"] = int((~allowed).sum())
        diag["eligible_candidates"] = int(allowed.sum())
        filtered = ranked[allowed]
        ix = date_index[day]
        entry_date = calendar[ix + 1]
        for mode in MODES:
            for _, s in filtered[filtered["mode"] == mode].head(top).iterrows():
                market, code = str(s["market"]), str(s["code"])
                for horizon in horizons:
                    diag["attempted_horizon_trades"] += 1
                    exit_date = calendar[ix + 1 + horizon]
                    period = calendar[ix + 1:ix + horizon + 2]
                    path = []
                    for d in period:
                        key = (d, market, code)
                        if key not in close_lookup.index:
                            path = []
                            break
                        path.append(float(close_lookup.loc[key]))
                    if len(path) != len(period) or not path or min(path) <= 0:
                        diag["missing_price_paths"] += 1
                        excluded.append({"signal_date": day, "mode": mode, "code": code,
                                         "holding_sessions": horizon,
                                         "reason": "進場至出場期間個股收盤價缺漏"})
                        continue
                    entry, exit_price = path[0], path[-1]
                    buy_cost = entry * (1 + commission_pct / 100)
                    sell_proceeds = exit_price * (1 - (commission_pct + sell_tax_pct) / 100)
                    trades.append({
                        "mode": mode, "signal_date": day, "market": market,
                        "code": code, "name": str(s["name"]),
                        "score": float(s["score"]), "risk_level": str(s["risk_level"]),
                        "entry_date": entry_date, "entry_close": round(entry, 4),
                        "exit_date": exit_date, "exit_close": round(exit_price, 4),
                        "holding_sessions": horizon,
                        "gross_return_pct": round((exit_price / entry - 1) * 100, 4),
                        "net_return_pct": round((sell_proceeds / buy_cost - 1) * 100, 4),
                        "worst_close_drawdown_pct": round((min(path) / entry - 1) * 100, 4),
                    })
                    diag["completed_horizon_trades"] += 1
        diagnostics.append(diag)
        print(f"SIGNAL {day}: raw={diag['raw_candidates']} eligible={diag['eligible_candidates']} "
              f"completed={diag['completed_horizon_trades']} missing={diag['missing_price_paths']}", flush=True)

    return (pd.DataFrame(trades, columns=TRADE_COLUMNS),
            pd.DataFrame(diagnostics), quality,
            pd.DataFrame(excluded, columns=EXCLUSION_COLUMNS))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-days", type=int, default=5)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--output", default="backtest_v161_results")
    parser.add_argument("--commission-pct", type=float, default=0.1425)
    parser.add_argument("--sell-tax-pct", type=float, default=0.3)
    parser.add_argument("--min-twse", type=int, default=500)
    parser.add_argument("--min-tpex", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.signal_days <= 100 or not 1 <= args.top <= 50:
        parser.error("signal-days 必須 1~100、top 必須 1~50")
    if not 0 <= args.commission_pct <= 2 or not 0 <= args.sell_tax_pct <= 2:
        parser.error("費率超出容許範圍")
    p = load_prices()  # from original V1.6, read only
    trades, diagnostics, quality, exclusions = run_backtest(
        p, args.signal_days, args.top, (3, 5, 10),
        args.commission_pct, args.sell_tax_pct, 65, 50_000_000,
        args.min_twse, args.min_tpex,
    )
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    trades.to_csv(folder / "trades.csv", index=False, encoding="utf-8-sig")
    diagnostics.to_csv(folder / "signal_quality.csv", index=False, encoding="utf-8-sig")
    quality.tail(120).to_csv(folder / "market_coverage.csv", index=False, encoding="utf-8-sig")
    exclusions.to_csv(folder / "exclusions.csv", index=False, encoding="utf-8-sig")
    if trades.empty:
        summary = pd.DataFrame(columns=["mode", "holding_sessions", "trades", "signal_dates",
                                        "win_rate_pct", "avg_net_return_pct"])
        print("沒有合格交易樣本：請查看 signal_quality.csv 和 exclusions.csv")
    else:
        summary = summarize(trades)
        print(summary.to_string(index=False))
    summary.to_csv(folder / "summary.csv", index=False, encoding="utf-8-sig")
    print(f"訊號日期 {len(diagnostics)}；有效交易紀錄 {len(trades)}；排除記錄 {len(exclusions)}")
    print("注意：同一股票可能重複進場；非投組回撤，未調整除權息或模擬盤中停損。")


if __name__ == "__main__":
    main()
