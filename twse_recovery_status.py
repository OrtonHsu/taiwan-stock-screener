"""Read-only accounting for the fixed March-June TWSE recovery.

Progress markers plus stored row counts are a recovery check, NOT proof of
full trading-calendar coverage or point-in-time universe completeness.
"""
import json
import os
from datetime import date, timedelta

from market_backfill import verified_before_listing

MAX_ATTEMPTS = 3
COOLDOWN = timedelta(hours=24)
STATES = ("completed", "pre_listing", "due", "cooldown", "manual_review")


def read_state(conn, month_keys):
    """Read only canonical TWSE prices; never create or update a table."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT code, month, row_count FROM public.backfill_progress
               WHERE market='TWSE' AND month = ANY(%s)""", (month_keys,)
        )
        progress = {(str(c), m): int(n) for c, m, n in cur.fetchall()}
        cur.execute(
            """SELECT code, LEFT(date,7), COUNT(*) FROM public.prices
               WHERE market='TWSE' AND LEFT(date,7) = ANY(%s)
               GROUP BY code, LEFT(date,7)""", (month_keys,)
        )
        counts = {(str(c), m): int(n) for c, m, n in cur.fetchall()}
        cur.execute("SELECT to_regclass('public.twse_mar_jun_failures')")
        failures = {}
        if cur.fetchone()[0] is not None:
            cur.execute(
                """SELECT code, month, attempts, last_attempt_at
                   FROM public.twse_mar_jun_failures
                   WHERE month = ANY(%s)""", (month_keys,)
            )
            failures = {(str(c), m): (int(n), t) for c, m, n, t in cur.fetchall()}
    return progress, counts, failures


def build_status(codes, months, progress, counts, failures, now):
    """Partition every current candidate/month exactly once, including retries."""
    codes = sorted(set(codes))
    month_keys = [m.strftime("%Y-%m") for m in months]
    totals = dict.fromkeys(STATES, 0)
    per_month = {m: dict.fromkeys(STATES, 0) for m in month_keys}
    due_by_code = {}
    review = []
    mismatches = []
    finished_codes = 0
    stored_rows = 0
    for code in codes:
        finished = True
        for month in months:
            key = (code, month.strftime("%Y-%m"))
            expected = progress.get(key, 0)
            actual = counts.get(key, 0)
            stored_rows += actual
            failure = failures.get(key)
            if expected > actual:
                mismatches.append({"code": code, "month": key[1],
                                   "recorded": expected, "stored": actual})
            if verified_before_listing(code, month):
                # Quotes in a verified pre-listing period require investigation.
                state = "manual_review" if actual else "pre_listing"
            elif expected > 0 and actual >= expected:
                state = "completed"
            elif failure and failure[0] >= MAX_ATTEMPTS:
                state = "manual_review"
            elif failure and now - failure[1] < COOLDOWN:
                state = "cooldown"
            else:
                state = "due"
            totals[state] += 1
            per_month[key[1]][state] += 1
            if state not in ("completed", "pre_listing"):
                finished = False
            if state == "due":
                due_by_code.setdefault(code, []).append(key[1])
            if state == "manual_review":
                review.append({"code": code, "month": key[1]})
        finished_codes += int(finished)
    unresolved = totals["due"] + totals["cooldown"] + totals["manual_review"]
    return {
        "as_of": now.isoformat(), "candidate_codes": len(codes),
        "total_code_months": len(codes) * len(months),
        "finished_codes": finished_codes, "stored_rows": stored_rows,
        "unresolved_months": unresolved, "states": totals,
        "per_month": per_month, "due_by_code": due_by_code,
        "manual_review": review, "marker_mismatches": mismatches,
        "recovery_counts_complete": bool(codes and months) and unresolved == 0
                                    and not mismatches,
    }


def report_status(status, mode, checked=0, inserted=0, failures=0):
    states = status["states"]
    # Keep the machine-readable log small: individual due codes stay in memory.
    compact = {k: v for k, v in status.items() if k != "due_by_code"}
    print("TWSE_RECOVERY_STATUS " + json.dumps(compact, ensure_ascii=False), flush=True)
    if status["manual_review"] or status["marker_mismatches"]:
        print("::warning::TWSE recovery has manual-review months or inconsistent "
              "progress markers; see the recovery summary.", flush=True)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as output:
        output.write("\n## 上市 2026 年 3～6 月回補進度\n\n")
        output.write(f"執行模式：**{mode}**；本批通過 {checked} 個月份、"
                     f"新增 {inserted:,} 筆、失敗 {failures} 個月份。\n\n")
        output.write(f"目前候選清單：**{status['candidate_codes']:,} 檔**；"
                     f"四個月均已處理：**{status['finished_codes']:,} 檔**。\n\n")
        output.write("| 月份 | 已有紀錄且筆數足夠 | 官方確認上市前 | 待回補 | 冷卻中 | 人工核對 |\n")
        output.write("|---|---:|---:|---:|---:|---:|\n")
        for month, values in status["per_month"].items():
            output.write("| " + month + " | " + " | ".join(str(values[s]) for s in STATES) + " |\n")
        output.write(f"\n尚待處理 **{status['unresolved_months']:,} 個股票月份**；"
                     f"進度與筆數不符 **{len(status['marker_mismatches'])} 項**。\n\n")
        if status["recovery_counts_complete"]:
            output.write("目前清單的回補筆數檢查已完成，仍須進行交易日與資料內容驗收。\n")
        else:
            output.write("**整體回補尚未完成；本批綠燈不代表全市場歷史資料已齊。**\n")
        output.write("\n只核對目前候選清單、進度標記與實存筆數；不代表逐交易日完整，"
                     "不包含已下市股票完整歷史名單。上市前月份不算成行情。"
                     "DRY_RUN / STATUS_ONLY 不會寫入資料庫。\n")
        if status["manual_review"]:
            output.write("\n待人工核對（最多列 20 項）：\n")
            for item in status["manual_review"][:20]:
                output.write(f"- {item['code']} / {item['month']}\n")
