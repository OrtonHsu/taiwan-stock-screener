"""Read-only V2.2 validation report after each verified V2.1 tracker run.

No writes. This report is descriptive, not a trading signal, forecast,
or claim of strategy efficacy.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from tw_market_calendar import latest_due_market_date
from v21_paper_tracker import read_quotes
from v22_signal_validation import (
    PROTOCOL, PROSPECTIVE_FROM, evaluate_recorded_signals, summarize_matured,
)


def main():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Missing DATABASE_URL")
    import psycopg

    asof = latest_due_market_date(datetime.now(ZoneInfo("Asia/Taipei")))
    with psycopg.connect(url, connect_timeout=20) as conn:
        quotes, dates = read_quotes(conn, asof)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT market,code,name,setup,signal_date,signal_close,
                          invalidation_reference,entry_date,entry_close
                   FROM stock_research.simulation_positions
                   WHERE strategy_version=%s
                   ORDER BY signal_date,market,code""",
                ("v2.1.0",),
            )
            source = pd.DataFrame(
                cur.fetchall(),
                columns=[
                    "market", "code", "name", "setup", "signal_date",
                    "signal_close", "invalidation_reference", "entry_date",
                    "entry_close",
                ],
            )
    signals, horizons = evaluate_recorded_signals(source, quotes, dates, asof=asof)
    eligible = signals[signals["cohort"] == "前瞻樣本"] if not signals.empty else signals
    confirmed = int(eligible["check_status"].isin(
        ["通過", "等待T+2", "T+2缺價"]
    ).sum()) if not eligible.empty else 0
    rejected = int((eligible["check_status"] == "未通過").sum()) if not eligible.empty else 0
    matured = int((horizons["status"] == "已完成").sum()) if not horizons.empty else 0
    summary = summarize_matured(horizons)

    line = (
        f"V22_RESEARCH_OK asof={asof} protocol={PROTOCOL} "
        f"prospective_from={PROSPECTIVE_FROM} "
        f"prospective_signals={len(eligible)} confirmed={confirmed} "
        f"rejected={rejected} finished_horizon_rows_all_cohorts={matured} "
        f"prospective_summary_rows={len(summary)}"
    )
    print(line, flush=True)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        from pathlib import Path
        with Path(summary_path).open("a", encoding="utf-8") as f:
            f.write(
                "\n### V2.2 隔日確認與固定持有期研究（唯讀）\n"
                f"- 最新完整交易日：{asof}\n"
                f"- 前瞻開始：{PROSPECTIVE_FROM}（規則制定前樣本不納入績效）\n"
                f"- 前瞻訊號數：{len(eligible)}，確認通過：{confirmed}，未通過：{rejected}\n"
                f"- 已成熟固定持有期紀錄（包含示例、不等於前瞻樣本）：{matured}\n"
                "- 不含除權息與滑價，不可視為實際獲利率。\n"
            )
            if not summary.empty:
                for row in summary.to_dict("records"):
                    f.write(
                        f"- {row['策略']}／{row['交易日']}日："
                        f"樣本{row['已到期樣本']}、"
                        f"平均淨報酬{row['平均淨報酬%']}%\n"
                    )


if __name__ == "__main__":
    main()
