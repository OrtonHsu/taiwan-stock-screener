"""V2.3 exploratory breadth vs forward TAIEX returns (RESEARCH ONLY).

- Reads existing v23_provisional_breadth_2026H1.csv.
- Reads verified TWSE FMTQIK monthly JSON, with optional explicit --fetch-index.
- Evaluates 5/10 NEXT official trading-day close-to-close outcomes, after each
  signal day's close. Never inserts data or gates live trading decisions.
- Signals overlap; all thresholds were selected exploratorily after looking
  at sample dates. NO causal, statistical, or forward-test claim.
- No changes to Supabase, GitHub main, V2.1 or V2.2.

Typical Windows PowerShell invocation:
 python v23_breadth_forward_study.py --fetch-index --windows-trust
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

INDEX_FIELD = "\u767c\u884c\u91cf\u52a0\u6b0a\u80a1\u50f9\u6307\u6578"
DATE_FIELD = "\u65e5\u671f"
MAX_BYTES = 2_000_000
MONTHS = [f"2026{m:02d}" for m in range(1, 8)]
STUDY_START = date(2026, 1, 1)
STUDY_END = date(2026, 6, 30)
LABELS = ("BOTH_LT_15PCT", "BOTH_GTE_40PCT", "OTHER")
HORIZONS = (5, 10)


def _finite_positive(raw) -> float:
    try:
        num = float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        raise ValueError(f"Invalid index close: {raw!r}") from None
    if not (math.isfinite(num) and num > 0):
        raise ValueError(f"Invalid index close: {raw!r}")
    return num


def parse_fmtqik(payload: object, month: str) -> dict[date, float]:
    """Require real monthly dates, correct schema, nonzero closing index."""
    if not re.fullmatch(r"20[0-9]{4}", month):
        raise ValueError("Month must be YYYYMM")
    target_year, target_month = int(month[:4]), int(month[4:])
    if not 1 <= target_month <= 12:
        raise ValueError("Invalid month")
    if not isinstance(payload, dict) or str(payload.get("stat")) != "OK":
        raise ValueError(f"Official FMTQIK status not OK for {month}")
    fields = payload.get("fields")
    rows = payload.get("data")
    if not isinstance(fields, list) or fields.count(DATE_FIELD) != 1 or fields.count(INDEX_FIELD) != 1:
        raise ValueError(f"FMTQIK required field missing or repeated: {month}")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"No index records for month {month}")
    di, ci = fields.index(DATE_FIELD), fields.index(INDEX_FIELD)
    output = {}
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(di, ci):
            raise ValueError(f"Malformed index row in {month}")
        raw_date = str(row[di]).strip()
        match = re.fullmatch(r"(\d{2,3})/(\d{2})/(\d{2})", raw_date)
        if not match:
            raise ValueError(f"Unrecognized ROC date: {raw_date}")
        yy, mm, dd = map(int, match.groups())
        day = date(yy + 1911, mm, dd)
        if (day.year, day.month) != (target_year, target_month):
            raise ValueError(f"Stale/wrong month response: {day} != {month}")
        if day in output:
            raise ValueError(f"Duplicate index date: {day}")
        output[day] = _finite_positive(row[ci])
    return output


def fmtqik_url(month: str) -> str:
    return "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?" + urlencode({
        "response": "json", "date": month + "01",
    })


def index_month(month: str, folder: Path, *, fetch: bool) -> dict[date, float]:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"TWSE_FMTQIK_{month}.json"
    manifest = folder / f"TWSE_FMTQIK_{month}.sha256"
    if dest.exists() != manifest.exists():
        raise ValueError(f"Cache incomplete for {month}")
    if dest.exists():
        raw = dest.read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.read_text(encoding="ascii").strip():
            raise ValueError(f"Cache hash mismatch for {month}")
        return parse_fmtqik(json.loads(raw), month)
    if not fetch:
        raise FileNotFoundError(f"Index cache missing for {month}. Use --fetch-index")
    url = fmtqik_url(month)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        if "json" not in resp.headers.get("Content-Type", "").lower():
            raise ValueError(f"Official server returned non-JSON for {month}")
        raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"Official response exceeds byte limit: {month}")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid official JSON in {month}") from exc
    parsed = parse_fmtqik(data, month)
    # Exclusive writes protect prior evidence; no database touches.
    with dest.open("xb") as handle:
        handle.write(raw)
    with manifest.open("x", encoding="ascii") as handle:
        handle.write(hashlib.sha256(raw).hexdigest() + "\n")
    time.sleep(1)
    return parsed


def load_index(folder: Path, *, fetch: bool) -> dict[date, float]:
    quotes = {}
    for month in MONTHS:
        monthly = index_month(month, folder, fetch=fetch)
        if set(quotes) & set(monthly):
            raise ValueError("Index month overlap")
        quotes.update(monthly)
        print("INDEX_MONTH_CHECKED:", month, len(monthly), flush=True)
    return dict(sorted(quotes.items()))


def read_breadth(path: Path) -> dict[str, dict[str, float]]:
    by_day = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        needed = {"trade_date", "market", "breadth_ratio", "comparable",
                  "advancing", "declining", "unchanged", "can_gate_entries"}
        if not reader.fieldnames or not needed.issubset(reader.fieldnames):
            raise ValueError("Breadth CSV missing fields")
        for row in reader:
            day = date.fromisoformat(row["trade_date"])
            if day < STUDY_START or day > STUDY_END:
                raise ValueError(f"Outside 2026H1: {day}")
            market = row["market"]
            if market not in ("TWSE", "TPEX") or market in by_day[day.isoformat()]:
                raise ValueError(f"Invalid/duplicate market date: {day} {market}")
            if str(row["can_gate_entries"]).lower() != "false":
                raise ValueError("Entry gating unexpectedly enabled")
            # Preserve first official trading day even when no prior close exists.
            by_day[day.isoformat()]
            c = int(row["comparable"])
            up, down, flat = (int(row[k]) for k in ("advancing", "declining", "unchanged"))
            if c < 0 or min(up,down,flat) < 0 or c != up + down + flat:
                raise ValueError(f"Invalid breadth counts: {day} {market}")
            ratio = row["breadth_ratio"].strip()
            if ratio:
                numeric = float(ratio)
                if not math.isfinite(numeric) or not 0 <= numeric <= 1:
                    raise ValueError(f"Invalid breadth ratio: {day}")
                if c < 1 or abs(numeric - up / c) > 0.000001:
                    raise ValueError(f"Wrong breadth ratio: {day}")
                by_day[day.isoformat()][market] = numeric
            elif c != 0:
                raise ValueError(f"Missing breadth ratio with comparable stocks: {day}")
    if len(by_day) != 116:
        raise ValueError(f"Expected 116 sampled study days, got {len(by_day)}")
    if any(set(x) != {"TWSE", "TPEX"} for x in by_day.values() if x):
        raise ValueError("Incomplete market pair with usable breadth")
    return dict(by_day)


def category(two: dict[str,float]) -> str:
    if two["TWSE"] < 0.15 and two["TPEX"] < 0.15:
        return LABELS[0]
    if two["TWSE"] >= 0.40 and two["TPEX"] >= 0.40:
        return LABELS[1]
    return LABELS[2]


def forward_outcomes(closes: list[float], index: int, horizon: int) -> dict:
    """Only next H closes. Peak-to-trough drawdown starts at signal close."""
    if index + horizon >= len(closes):
        raise ValueError("Insufficient subsequent trading days")
    start = closes[index]
    future = closes[index + 1:index + horizon + 1]
    peak = start
    mdd = 0.0
    for price in future:
        peak = max(peak, price)
        mdd = min(mdd, price / peak - 1.0)
    return {
        "end_return_pct": (future[-1] / start - 1) * 100,
        "worst_from_signal_pct": (min(future) / start - 1) * 100,
        "max_drawdown_pct": mdd * 100,
    }


def analyze(breadth: dict, index_map: dict[date,float]) -> list[dict]:
    dates = sorted(index_map)
    if len(dates) != len(set(dates)):
        raise ValueError("Duplicate index date")
    by_date = {d:i for i,d in enumerate(dates)}
    signal_days = sorted(date.fromisoformat(d) for d,x in breadth.items() if x)
    if not signal_days:
        raise ValueError("No usable breadth dates")
    rows = []
    for d in signal_days:
        if d not in by_date:
            raise ValueError(f"Signal date absent from index series: {d}")
        signal = breadth[d.isoformat()]
        group = category(signal)
        index = by_date[d]
        for horizon in HORIZONS:
            outcome = forward_outcomes([index_map[day] for day in dates], index, horizon)
            rows.append({
                "signal_date": d.isoformat(),
                "horizon_days": horizon,
                "category": group,
                "twse_breadth": f"{signal['TWSE']:.6f}",
                "tpex_breadth": f"{signal['TPEX']:.6f}",
                **{key:round(value,6) for key,value in outcome.items()},
                "max_drawdown_at_least_5pct": int(outcome["max_drawdown_pct"] <= -5),
                "positive_terminal_return": int(outcome["end_return_pct"] > 0),
                "research_only": True,
            })
    return rows


def show_aggregates(rows: list[dict]) -> None:
    from statistics import mean
    print("OBSERVATIONS:", len(rows))
    print("SIGNAL_DATES:", len({r["signal_date"] for r in rows}))
    print("NOTE: overlapping horizons; in-sample exploratory thresholds; not independently predictive")
    for horizon in HORIZONS:
        for group in LABELS:
            sel = [x for x in rows if x["horizon_days"]==horizon and x["category"]==group]
            if not sel:
                print("GROUP:",group,"HORIZON:",horizon,"N: 0")
                continue
            print("GROUP:",group,"HORIZON:",horizon,
                  "N:",len(sel),
                  "AVG_END_PCT:",round(mean(r["end_return_pct"] for r in sel),2),
                  "AVG_WORST_FROM_SIGNAL_PCT:",round(mean(r["worst_from_signal_pct"] for r in sel),2),
                  "AVG_MAX_DRAWDOWN_PCT:",round(mean(r["max_drawdown_pct"] for r in sel),2),
                  "DD5_COUNT:",sum(r["max_drawdown_at_least_5pct"] for r in sel),
                  "POSITIVE_END_COUNT:",sum(r["positive_terminal_return"] for r in sel))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Research-only exploratory 2026H1 breadth forward outcomes")
    parser.add_argument("--breadth", default="v23_research_samples/v23_provisional_breadth_2026H1.csv")
    parser.add_argument("--index-cache", default="v23_research_samples/v23_index_months")
    parser.add_argument("--output", default="v23_research_samples/v23_breadth_forward_2026H1.csv")
    parser.add_argument("--fetch-index", action="store_true", help="Fetch 7 monthly official index JSON files")
    parser.add_argument("--windows-trust", action="store_true")
    args = parser.parse_args(argv)
    target = Path(args.output)
    if target.exists():
        raise FileExistsError(f"Research output already exists (no overwrite): {target}")
    if args.fetch_index and args.windows_trust:
        import truststore
        truststore.inject_into_ssl()
    breadth=read_breadth(Path(args.breadth))
    index=load_index(Path(args.index_cache),fetch=args.fetch_index)
    index_days=set(index)
    if any(date.fromisoformat(x) not in index_days for x in breadth):
        raise ValueError("Study calendar has dates missing from official index")
    rows=analyze(breadth,index)
    show_aggregates(rows)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",newline="",encoding="utf-8-sig") as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("OUTPUT:",target)
    print("ENTRY_GATING_ENABLED: False")
    print("DATABASE_WRITES: 0")


if __name__=="__main__":
    main()
