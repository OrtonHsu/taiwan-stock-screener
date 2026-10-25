
"""V1.5.3 MA dual-mode backtest with extra quality reports.

Read-only. Preserves the existing backtest engine and its six CSV reports.
Adds independent-signal, unusual-price and repeated-stock diagnostics.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest_v16 import load_prices
from backtest_ma_v153 import run_backtest


REPORT_NAMES = (
    "summary",
    "trades",
    "signal_quality",
    "market_coverage",
    "exclusions",
    "convergence_audit",
)

SIGNAL_KEYS = ["mode", "signal_date", "market", "code"]

ANOMALY_COLUMNS = [
    "mode", "signal_date", "market", "code", "name",
    "entry_date", "exit_date", "holding_sessions",
    "entry_close", "exit_close", "gross_return_pct",
    "flag",
]


def build_quality_reports(trades, audit, anomaly_pct=20.0):
    """Build descriptive diagnostics, not additional strategy signals."""

    if not audit.empty:
        signals = audit.copy()
        signals["signal_date"] = signals["signal_date"].astype(str)
        signals = signals.drop_duplicates(SIGNAL_KEYS)
    elif not trades.empty:
        signals = trades[SIGNAL_KEYS].drop_duplicates().copy()
    else:
        signals = pd.DataFrame(columns=SIGNAL_KEYS)

    if signals.empty:
        independent = pd.DataFrame(columns=[
            "mode", "independent_signal_dates",
            "unique_stock_signals", "unique_stocks",
        ])
        repeated = pd.DataFrame(columns=[
            "mode", "market", "code",
            "signal_dates", "first_signal", "last_signal",
        ])
    else:
        independent = (
            signals.groupby("mode")
            .agg(
                independent_signal_dates=("signal_date", "nunique"),
                unique_stock_signals=("code", "size"),
            )
            .reset_index()
        )

        unique_stocks = (
            signals.assign(
                stock_id=signals["market"].astype(str)
                + ":"
                + signals["code"].astype(str)
            )
            .groupby("mode")["stock_id"]
            .nunique()
            .rename("unique_stocks")
            .reset_index()
        )

        independent = independent.merge(
            unique_stocks, on="mode", how="left"
        )

        repeated = (
            signals.groupby(["mode", "market", "code"])
            .agg(
                signal_dates=("signal_date", "nunique"),
                first_signal=("signal_date", "min"),
                last_signal=("signal_date", "max"),
            )
            .reset_index()
        )

        repeated = repeated[
            repeated["signal_dates"] > 1
        ].sort_values(
            ["signal_dates", "mode"],
            ascending=[False, True],
        )

    if trades.empty:
        anomalies = pd.DataFrame(columns=ANOMALY_COLUMNS)
    else:
        # Entry-to-exit close change is a review flag only.
        # It does not establish a bad quote or a stock split.
        anomalies = trades[
            trades["gross_return_pct"].abs() >= anomaly_pct
        ].copy()

        anomalies["flag"] = (
            "進出場收盤價變動達門檻；需檢查除權息、"
            "公司事件及原始行情"
        )

        anomalies = anomalies.reindex(
            columns=ANOMALY_COLUMNS
        ).sort_values(
            "gross_return_pct"
        )

    return independent, anomalies, repeated


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--signal-days", type=int, default=20
    )
    parser.add_argument(
        "--top", type=int, default=10
    )
    parser.add_argument(
        "--min-twse", type=int, default=800
    )
    parser.add_argument(
        "--min-tpex", type=int, default=750
    )
    parser.add_argument(
        "--anomaly-pct", type=float, default=20.0
    )
    parser.add_argument(
        "--output",
        default="ma_v153_quality_results",
    )

    args = parser.parse_args()

    if not 1 <= args.signal_days <= 100:
        parser.error("signal-days 必須介於 1～100")

    if not 1 <= args.top <= 50:
        parser.error("top 必須介於 1～50")

    if min(args.min_twse, args.min_tpex) < 100:
        parser.error("市場最低檔數不可低於 100")

    if not 0 < args.anomaly_pct <= 1000:
        parser.error("異常變動門檻須介於 0～1000%")

    print("讀取歷史行情...", flush=True)
    prices = load_prices()

    print("執行原版 V1.5.3 回測...", flush=True)
    reports = run_backtest(
        prices,
        signal_days=args.signal_days,
        top=args.top,
        min_twse=args.min_twse,
        min_tpex=args.min_tpex,
    )

    summary, trades, diagnostics, quality, exclusions, audit = reports

    independent, anomalies, repeated = build_quality_reports(
        trades,
        audit,
        anomaly_pct=args.anomaly_pct,
    )

    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)

    # Keep the six original report files unchanged.
    for name, frame in zip(REPORT_NAMES, reports):
        frame.to_csv(
            folder / f"{name}.csv",
            index=False,
            encoding="utf-8-sig",
        )

    extra_reports = {
        "independent_signals": independent,
        "price_anomalies": anomalies,
        "repeated_stocks": repeated,
    }

    for name, frame in extra_reports.items():
        frame.to_csv(
            folder / f"{name}.csv",
            index=False,
            encoding="utf-8-sig",
        )

    print("\n===== 回測品質摘要 =====")
    print(f"完成的持有期交易紀錄：{len(trades)}")
    print(f"排除紀錄：{len(exclusions)}")
    print(f"價格變動警示紀錄：{len(anomalies)}")
    print(f"重複出現股票組合：{len(repeated)}")

    if not independent.empty:
        print("\n獨立股票訊號統計：")
        print(independent.to_string(index=False))
    else:
        print("沒有可統計的股票訊號。")

    print(f"\n報告已輸出至：{folder}")
    print("共有 9 份 CSV 報告。")
    print(
        "注意：異常價格不會被自動刪除；"
        "交易日覆蓋與除權息問題仍需人工查驗。"
    )


if __name__ == "__main__":
    main()
