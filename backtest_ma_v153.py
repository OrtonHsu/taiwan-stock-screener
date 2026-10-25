
"""V1.5.3 MA-convergence A/B research backtest. Read-only; EOD close-to-close.

Requires backtest_v16.py (load_prices), backtest_v161.py (quality_table),
and ma_convergence.py (scan_ma_dual) from the existing repository.
No database writes, orders, or changes to the Streamlit app.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest_v16 import load_prices
from backtest_v161 import quality_table
from ma_convergence import scan_ma_dual

MODES = ("A 蓄勢觀察", "B 突破確認")
HORIZONS = (3, 5, 10)
TRADE_COLUMNS = [
    "mode", "signal_date", "market", "code", "name",
    "convergence_pct", "convergence_day", "historical_convergence_pct",
    "entry_date", "entry_close", "exit_date", "exit_close",
    "holding_sessions", "gross_return_pct", "net_return_pct",
    "worst_close_drawdown_pct",
]
EXCLUSION_COLUMNS = ["signal_date", "market", "mode", "code", "holding_sessions", "reason"]
DIAG_COLUMNS = ["signal_date", "market_coverage_ok", "raw_A", "raw_B",
                "selected_A", "selected_B", "completed_trades", "excluded_trades"]


def run_backtest(prices, signal_days=20, top=10, min_history=65,
                 min_amount=50_000_000, min_twse=800, min_tpex=750,
                 commission_pct=0.1425, sell_tax_pct=0.3,
                 threshold_pct=2.0, lookback_days=5):
    p = prices.copy()
    required = {"date", "market", "code", "name", "close", "volume", "amount"}
    if not required.issubset(p.columns):
        raise ValueError(f"行情缺少必要欄位：{sorted(required - set(p.columns))}")
    for col in ("date", "market", "code"):
        p[col] = p[col].astype(str).str.strip()
    p = p[p.market.isin(["TWSE", "TPEX"]) & p.code.str.fullmatch(r"[1-9]\d{3}")].copy()
    for col in ("close", "volume", "amount"):
        p[col] = pd.to_numeric(p[col], errors="coerce")
    p = p.dropna(subset=["close", "volume", "amount"])
    p = p[(p.close > 0) & (p.volume >= 0) & (p.amount >= 0)]
    p = p.sort_values(["date", "market", "code"]).drop_duplicates(
        ["date", "market", "code"], keep="last")

    # TWSE trading calendar is the shared session reference for both markets.
    by_twse = p[p.market == "TWSE"].groupby("date").code.nunique()
    calendar = sorted(by_twse[by_twse >= min_twse].index)
    needed = max(min_history, 26) + max(HORIZONS) + 2
    if len(calendar) < needed:
        raise ValueError(f"合格交易日只有 {len(calendar)} 天，需要至少 {needed} 天")
    quality = quality_table(p, calendar, min_twse=min_twse, min_tpex=min_tpex)
    quality_idx = quality.set_index("date")
    day_ix = {d: i for i, d in enumerate(calendar)}
    dates = calendar[max(min_history, 26) - 1:-(max(HORIZONS) + 1)][-signal_days:]
    indexed = p.set_index(["date", "market", "code"])
    closes = indexed["close"].to_dict()
    trades, excluded, diagnostics, audit = [], [], [], []

    for day in dates:
        q = quality_idx.loc[day]
        diag = dict(signal_date=day, market_coverage_ok=bool(q.signal_day_usable),
                    raw_A=0, raw_B=0, selected_A=0, selected_B=0,
                    completed_trades=0, excluded_trades=0)
        if not q.signal_day_usable:
            excluded.append(dict(signal_date=day, market="", mode="ALL", code="",
                                 holding_sessions="", reason="訊號日市場行情覆蓋率不足"))
            diagnostics.append(diag)
            continue
        ix = day_ix[day]
        # Signal-day and earlier prices only. All horizons begin NEXT session.
        history_dates = set(calendar[max(0, ix - max(min_history + 15, 110)):ix + 1])
        asof = p[p.date.isin(history_dates)]
        selected = scan_ma_dual(asof, threshold_pct=threshold_pct,
                                min_history=min_history, min_amount=min_amount,
                                lookback_days=lookback_days)
        if selected.empty:
            diagnostics.append(diag)
            continue
        selected = selected[selected.date.astype(str) == day]
        for mode, prefix in ((MODES[0], "A"), (MODES[1], "B")):
            pool = selected[selected["mode"] == mode].copy()
            diag[f"raw_{prefix}"] = len(pool)
            if mode == MODES[0]:
                pool = pool.sort_values(["convergence_pct", "volume_ratio"], ascending=[True, False])
            else:
                pool = pool.sort_values(["volume_ratio", "breakout_pct"], ascending=[False, False])
            pool = pool.head(top)
            diag[f"selected_{prefix}"] = len(pool)

            for _, stock in pool.iterrows():
                market, code = str(stock.market), str(stock.code)
                conv_day = str(stock.convergence_day)
                hist_conv = float(stock.convergence_pct) if mode == MODES[0] else None
                # Recalculate historical MA convergence from that stock's
                # actual as-of prices, independent of the strategy's label.
                if mode == MODES[1]:
                    past_g = asof[(asof.market == market) & (asof.code == code)
                                  & (asof.date <= conv_day)].sort_values("date")
                    if len(past_g) >= 20 and str(past_g.iloc[-1].date) == conv_day:
                        past_c = past_g.close.tail(20).astype(float)
                        old_mas = [past_c.tail(n).mean() for n in (5, 10, 20)]
                        hist_conv = (max(old_mas) - min(old_mas)) / (sum(old_mas) / 3) * 100
                        old_ext = (float(past_c.iloc[-1]) / old_mas[2] - 1) * 100
                    else:
                        old_ext = float("inf")
                    if hist_conv is None or hist_conv > threshold_pct + 1e-6 or abs(old_ext) > 3.0:
                        excluded.append(dict(signal_date=day, market=market, mode=mode,
                                             code=code, holding_sessions="",
                                             reason="歷史糾結率或乖離未通過獨立驗證"))
                        diag["excluded_trades"] += 1
                        continue
                audit.append({"signal_date": day, "market": market, "code": code,
                              "mode": mode, "convergence_day": conv_day,
                              "historical_convergence_pct": hist_conv,
                              "current_convergence_pct": stock.convergence_pct,
                              "breakout_reference": stock.breakout_reference,
                              "volume_ratio": stock.volume_ratio})
                for horizon in HORIZONS:
                    period = calendar[ix + 1:ix + horizon + 2]
                    if len(period) != horizon + 1:
                        continue
                    # A broken market day in the position's market invalidates the path.
                    flag_col = "twse_coverage_ok" if market == "TWSE" else "tpex_coverage_ok"
                    if not bool(quality_idx.loc[period, flag_col].all()):
                        reason = "持有期間市場資料覆蓋率不足"
                        path = []
                    else:
                        path = [closes.get((d, market, code)) for d in period]
                        reason = "進出場區間個股收盤資料缺漏"
                    if not path or any(x is None or x <= 0 for x in path):
                        excluded.append(dict(signal_date=day, market=market, mode=mode,
                                             code=code, holding_sessions=horizon, reason=reason))
                        diag["excluded_trades"] += 1
                        continue
                    entry, exit_price = path[0], path[-1]
                    buy = entry * (1 + commission_pct / 100)
                    sell = exit_price * (1 - (commission_pct + sell_tax_pct) / 100)
                    trades.append({
                        "mode": mode, "signal_date": day, "market": market, "code": code,
                        "name": stock["name"], "convergence_pct": stock.convergence_pct,
                        "convergence_day": conv_day, "historical_convergence_pct": round(hist_conv, 4),
                        "entry_date": period[0], "entry_close": entry,
                        "exit_date": period[-1], "exit_close": exit_price,
                        "holding_sessions": horizon,
                        "gross_return_pct": round((exit_price / entry - 1) * 100, 4),
                        "net_return_pct": round((sell / buy - 1) * 100, 4),
                        "worst_close_drawdown_pct": round((min(path) / entry - 1) * 100, 4),
                    })
                    diag["completed_trades"] += 1
        diagnostics.append(diag)
        print(f"{day}: A={diag['selected_A']} B={diag['selected_B']} "
              f"completed={diag['completed_trades']} excluded={diag['excluded_trades']}", flush=True)

    trades_df = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    summary_rows = []
    if not trades_df.empty:
        for (mode, horizon), g in trades_df.groupby(["mode", "holding_sessions"]):
            summary_rows.append({
                "mode": mode, "holding_sessions": horizon,
                "trades": len(g), "signal_dates": g.signal_date.nunique(),
                "win_rate_pct": round((g.net_return_pct > 0).mean() * 100, 2),
                "avg_net_return_pct": round(g.net_return_pct.mean(), 3),
                "median_net_return_pct": round(g.net_return_pct.median(), 3),
                "avg_worst_close_drawdown_pct": round(g.worst_close_drawdown_pct.mean(), 3),
                "worst_close_drawdown_pct": round(g.worst_close_drawdown_pct.min(), 3),
            })
    return (pd.DataFrame(summary_rows), trades_df,
            pd.DataFrame(diagnostics, columns=DIAG_COLUMNS), quality,
            pd.DataFrame(excluded, columns=EXCLUSION_COLUMNS), pd.DataFrame(audit))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-days", type=int, default=20)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output", default="ma_v153_backtest_results")
    parser.add_argument("--min-twse", type=int, default=800)
    parser.add_argument("--min-tpex", type=int, default=750)
    parser.add_argument("--commission-pct", type=float, default=0.1425)
    parser.add_argument("--sell-tax-pct", type=float, default=0.3)
    args = parser.parse_args()
    if not 1 <= args.signal_days <= 100 or not 1 <= args.top <= 50:
        parser.error("signal-days 必須 1~100，top 必須 1~50")
    if min(args.min_twse, args.min_tpex) < 100:
        parser.error("市場最低筆數不得小於 100")
    if not 0 <= args.commission_pct <= 2 or not 0 <= args.sell_tax_pct <= 2:
        parser.error("費率須在 0~2%")

    frames = run_backtest(load_prices(), signal_days=args.signal_days, top=args.top,
                          min_twse=args.min_twse, min_tpex=args.min_tpex,
                          commission_pct=args.commission_pct, sell_tax_pct=args.sell_tax_pct)
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in zip(("summary", "trades", "signal_quality", "market_coverage",
                         "exclusions", "convergence_audit"), frames):
        df.to_csv(folder / f"{name}.csv", index=False, encoding="utf-8-sig")
    print("回測完成；請先檢查 market_coverage.csv、exclusions.csv 及 convergence_audit.csv。")
    if not frames[0].empty:
        print(frames[0].to_string(index=False))
    else:
        print("沒有合格交易樣本，不能推估勝率。")
    print("注意：僅以隔日收盤價模擬進場，不是可執行績效；除權息未還原，亦非投組回撤。")


if __name__ == "__main__":
    main()
