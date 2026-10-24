"""V1.6 EOD research backtest for the existing V1.5.1 dual-mode engine.

Uses only information dated on/before each signal close. Hypothetical entry
at the NEXT session's close and exit at a subsequent session's close.
NOT an execution-accurate or survivorship-bias-free backtest.

Usage (DATABASE_URL in environment):
  python backtest_v16.py --signal-days 20 --top 10
Outputs to backtest_v16_results/ as CSV.
"""

import argparse
from pathlib import Path

import pandas as pd

from storage import Store
from v15_strategy import rank_v15


MODES = ("A 起漲前期", "B 強勢回檔")


def eligible_signal(row):
    """Only research signals not explicitly labeled as waiting/watch-only."""
    if str(row.get("risk_level", "")) == "高":
        return False
    signal = str(row.get("signal", ""))
    if any(x in signal for x in ("僅供觀察", "等待止跌", "等待確認")):
        return False
    return True


def load_prices():
    db = Store()
    try:
        p = db.load()
    finally:
        db.close()
    required = {"date", "market", "code", "name", "close", "volume", "amount"}
    if not required.issubset(p.columns):
        raise ValueError("prices 資料表欄位不足")
    p = p.copy()
    for col in ("date", "market", "code"):
        p[col] = p[col].astype(str)
    # Do not reinterpret the legacy TPEx spelling: use canonical TPEX only.
    p = p[p.market.isin(["TWSE", "TPEX"]) & p.code.str.fullmatch(r"[1-9]\d{3}")]
    p["date"] = pd.to_datetime(p["date"], errors="coerce")
    for col in ("close", "volume", "amount"):
        p[col] = pd.to_numeric(p[col], errors="coerce")
    p = p.dropna(subset=["date", "close", "volume", "amount"])
    p = p[(p.close > 0) & (p.volume >= 0) & (p.amount >= 0)]
    p = p.sort_values("date").drop_duplicates(["date", "market", "code"], keep="last")
    p["date"] = p.date.dt.strftime("%Y-%m-%d")
    return p


def summarize(trades):
    items = []
    for (mode, horizon), g in trades.groupby(["mode", "holding_sessions"]):
        r = g.net_return_pct / 100
        items.append({
            "mode": mode,
            "holding_sessions": horizon,
            "trades": len(g),
            "signal_dates": g.signal_date.nunique(),
            "win_rate_pct": round(float((r > 0).mean() * 100), 2),
            "avg_net_return_pct": round(float(r.mean() * 100), 2),
            "median_net_return_pct": round(float(r.median() * 100), 2),
            "avg_worst_close_drawdown_pct": round(float(g.worst_close_drawdown_pct.mean()), 2),
            "max_worst_close_drawdown_pct": round(float(g.worst_close_drawdown_pct.min()), 2),
        })
    return pd.DataFrame(items)


def backtest(p, signal_days=20, top=10, horizons=(3, 5, 10),
             commission_pct=0.1425, sell_tax_pct=0.3,
             min_amount=50_000_000, min_history=65):
    # Market calendar: days with broad TWSE quotes, not individual-stock dates.
    listed_counts = p[p.market == "TWSE"].groupby("date").code.nunique()
    calendar = sorted(listed_counts[listed_counts >= 500].index)
    longest = max(horizons)
    if len(calendar) < 65 + longest + 2:
        raise ValueError("完整交易日期不足以進行此設定的回測")

    # Only evaluate dates for which there is an entire future holding horizon.
    signal_dates = calendar[64:-(longest + 1)][-signal_days:]
    if not signal_dates:
        raise ValueError("無可回測的訊號日期")
    cal_index = {d: i for i, d in enumerate(calendar)}
    # Fast lookup; drop dates not in the canonical market calendar.
    closes = p.set_index(["date", "market", "code"])["close"]
    trades = []
    inspected = 0
    for signal_date in signal_dates:
        asof = p[p.date <= signal_date]
        # Only last 100 trading days needed by existing 65-day screener.
        active_days = [d for d in calendar if d <= signal_date][-100:]
        asof = asof[asof.date.isin(active_days)]
        ranked = rank_v15(asof, min_history=min_history, min_amount=min_amount)
        inspected += 1
        print(f"SIGNAL {signal_date}: {len(ranked)} candidates", flush=True)
        if ranked.empty:
            continue
        ranked = ranked[ranked.apply(eligible_signal, axis=1)]
        ix = cal_index[signal_date]
        entry_date = calendar[ix + 1]
        for mode in MODES:
            selected = ranked[ranked["mode"] == mode].head(top)
            for _, s in selected.iterrows():
                market, code = str(s.market), str(s.code)
                for horizon in horizons:
                    exit_date = calendar[ix + 1 + horizon]
                    period = calendar[ix + 1:ix + horizon + 2]
                    try:
                        path = [float(closes.loc[(d, market, code)]) for d in period]
                    except KeyError:
                        # Incomplete stock-day coverage; never skip ahead to next existing date.
                        continue
                    if len(path) != len(period) or min(path) <= 0:
                        continue
                    entry, exit_price = path[0], path[-1]
                    # Cost rates on respective transaction notionals (round trip).
                    buy_cost = entry * (1 + commission_pct / 100)
                    sell_proceeds = exit_price * (1 - (commission_pct + sell_tax_pct) / 100)
                    net = (sell_proceeds / buy_cost - 1) * 100
                    drawdown = (min(path) / entry - 1) * 100
                    trades.append({
                        "mode": mode, "signal_date": signal_date,
                        "market": market, "code": code,
                        "name": str(s["name"]), "score": float(s.score),
                        "risk_level": str(s.risk_level),
                        "entry_date": entry_date, "entry_close": round(entry, 4),
                        "exit_date": exit_date, "exit_close": round(exit_price, 4),
                        "holding_sessions": horizon,
                        "gross_return_pct": round((exit_price / entry - 1) * 100, 4),
                        "net_return_pct": round(net, 4),
                        "worst_close_drawdown_pct": round(drawdown, 4),
                    })
    return pd.DataFrame(trades), inspected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-days", type=int, default=20)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--commission-pct", type=float, default=0.1425)
    parser.add_argument("--sell-tax-pct", type=float, default=0.3)
    parser.add_argument("--output", default="backtest_v16_results")
    args = parser.parse_args()
    if not 1 <= args.signal_days <= 100 or not 1 <= args.top <= 50:
        parser.error("signal-days 必須為 1~100；top 必須為 1~50")
    if not 0 <= args.commission_pct <= 2 or not 0 <= args.sell_tax_pct <= 2:
        parser.error("手續費與交易稅參數超出允許範圍")
    p = load_prices()
    trades, inspected = backtest(
        p, signal_days=args.signal_days, top=args.top,
        commission_pct=args.commission_pct, sell_tax_pct=args.sell_tax_pct,
    )
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
    if trades.empty:
        print(f"已檢查 {inspected} 個訊號日期；無合格且具完整未來行情的交易樣本。")
        return
    summary = summarize(trades)
    summary.to_csv(out / "summary.csv", index=False, encoding="utf-8-sig")
    print("\n=== V1.6 回測摘要（假設隔日收盤進場；扣成本） ===")
    print(summary.to_string(index=False))
    print(f"已檢查 {inspected} 個訊號日期，輸出至 {out}/")
    print("警告：樣本間可能重複持股、未建模資金限制及盤中停損；非真實投組績效。")


if __name__ == "__main__":
    main()
