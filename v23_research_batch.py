"""V2.3 research-only bounded historical quote sample batch.

Maximum five TWSE official calendar days, two markets, ten source-date pairs.
NO database, Supabase, GitHub, or production state writes.
Only local JSON/manifest samples and one local CSV summary.
Existing sample files are hash-verified and never overwritten.
Default dry-run; real GET calls require --fetch.

Example:
 python v23_research_batch.py --start 2026-01-02 --end 2026-01-08 --fetch --windows-trust
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import v23_official_sample as sampler
from v23_tpex_quote_audit import audit_payload

MARKETS = ("TWSE", "TPEX")
MAX_DAYS = 5


def calendar_days(calendar_path: str | Path, start: str, end: str) -> list[str]:
    beginning = date.fromisoformat(start)
    ending = date.fromisoformat(end)
    if beginning > ending:
        raise ValueError("start after end")
    with open(calendar_path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "trade_date" not in reader.fieldnames:
            raise ValueError("Calendar requires trade_date column")
        days = [row["trade_date"] for row in reader]
    if days != sorted(set(days)):
        raise ValueError("Calendar must have sorted unique dates")
    chosen = [day for day in days if beginning <= date.fromisoformat(day) <= ending]
    if not chosen or len(chosen) > MAX_DAYS:
        raise ValueError(f"Select 1 to {MAX_DAYS} official trading dates; got {len(chosen)}")
    if any(date.fromisoformat(day).weekday() >= 5 for day in chosen):
        raise ValueError("Weekend date in calendar")
    return chosen


def positive_number(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def twse_counts(payload: dict, day: str, expected_raw_rows: int) -> dict:
    required = ("證券代號", "收盤價", "成交股數", "成交金額")
    candidates = [t for t in payload.get("tables", []) if isinstance(t, dict)
                  and isinstance(t.get("data"), list) and t["data"]
                  and all(k in t.get("fields", []) for k in required)
                  and len(t["data"]) == expected_raw_rows]
    if len(candidates) != 1:
        raise ValueError("TWSE target quote table is not unique")
    table = candidates[0]
    fields = table["fields"]
    idx = {key: fields.index(key) for key in required}
    codes = set()
    four_digit = valid = missing_close = 0
    for row in table["data"]:
        if not isinstance(row, list) or len(row) <= max(idx.values()):
            raise ValueError("Malformed TWSE quote row")
        code = str(row[idx["證券代號"]]).strip()
        if not re.fullmatch(r"[0-9]{4}", code):
            continue
        if code in codes:
            raise ValueError("Duplicate TWSE code: " + code)
        codes.add(code)
        four_digit += 1
        close = positive_number(row[idx["收盤價"]])
        volume = positive_number(row[idx["成交股數"]])
        amount = positive_number(row[idx["成交金額"]])
        if close is None:
            missing_close += 1
        if close is not None and close > 0 and volume is not None and volume > 0 and amount is not None and amount >= 0:
            valid += 1
    return {"four_digit_unique": four_digit, "valid_close_with_volume": valid,
            "missing_close": missing_close}


def sample_counts(market: str, day: str, payload: dict, evidence: dict) -> dict:
    if evidence.get("status") not in ("schema_date_verified", "metadata_schema_verified_only"):
        raise ValueError("Official sample was not date/schema verified")
    if market == "TWSE":
        if evidence["status"] != "schema_date_verified":
            raise ValueError("TWSE date not verified")
        return twse_counts(payload, day, int(evidence["raw_table_rows"]))
    if market == "TPEX":
        if evidence["status"] != "metadata_schema_verified_only":
            raise ValueError("TPEx date metadata not verified")
        _, stats = audit_payload(payload, day)
        return {"four_digit_unique": stats["FOUR_DIGIT_UNIQUE"],
                "valid_close_with_volume": stats["VALID_CLOSE_WITH_VOLUME"],
                "missing_close": stats["MISSING_CLOSE_WITH_VOLUME"] + stats["MISSING_CLOSE_NO_TRADE"]}
    raise ValueError("Unknown market")


def load_or_get(folder: Path, market: str, day: str, *, fetch: bool):
    stem = f"{market}_{day.replace('-', '')}"
    raw_path = folder / (stem + ".json")
    manifest_path = folder / (stem + ".manifest.json")
    url = sampler.request_url(market, day)
    if raw_path.exists() != manifest_path.exists():
        raise ValueError(f"Incomplete existing sample: {stem}")
    if raw_path.exists():
        raw = raw_path.read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("sha256") != hashlib.sha256(raw).hexdigest()
                or manifest.get("source_url") != url
                or manifest.get("trade_date") != day):
            raise ValueError(f"Existing sample manifest mismatch: {stem}")
        payload = json.loads(raw)
        ev = sampler.evidence(market, day, payload)
        if ev.get("status") != manifest.get("status"):
            raise ValueError(f"Existing sample status mismatch: {stem}")
        return payload, ev, "VERIFIED_REUSE"
    if not fetch:
        raise ValueError("Missing local sample; use --fetch explicitly")
    raw = sampler.fetch_one(url)
    payload = json.loads(raw)
    ev = sampler.evidence(market, day, payload)
    # Validate the metrics before storing a new official raw response.
    sample_counts(market, day, payload, ev)
    sampler.save_sample(folder, market, day, raw, ev, url)
    return payload, ev, "DOWNLOADED"


def main(argv=None):
    cli = argparse.ArgumentParser(description="V2.3 bounded, research-only official market sampler")
    cli.add_argument("--calendar", default="v23_trading_calendar_2021_2026.csv")
    cli.add_argument("--start", default="2026-01-02")
    cli.add_argument("--end", default="2026-01-08")
    cli.add_argument("--output-dir", default="v23_research_samples")
    cli.add_argument("--fetch", action="store_true")
    cli.add_argument("--windows-trust", action="store_true")
    args = cli.parse_args(argv)
    days = calendar_days(args.calendar, args.start, args.end)
    folder = Path(args.output_dir)
    summary = folder / f"v23_batch_{args.start.replace('-', '')}_{args.end.replace('-', '')}.csv"
    if summary.exists():
        raise FileExistsError(f"Existing batch summary: {summary}")
    print("MODE:", "DOWNLOAD_OFFICIAL_JSON_ONLY" if args.fetch else "DRY_RUN")
    print("OFFICIAL_DAYS:", len(days), "MARKETS:", ",".join(MARKETS))
    print("MAX_HTTP_GETS:", len(days) * len(MARKETS))
    print("NO_DATABASE_WRITES: True")
    if not args.fetch:
        for day in days:
            for market in MARKETS:
                print("PLAN:", day, market, sampler.request_url(market, day))
        return []
    if args.windows_trust:
        import truststore
        truststore.inject_into_ssl()
    rows = []
    for day in days:
        for market in MARKETS:
            payload, ev, origin = load_or_get(folder, market, day, fetch=True)
            counts = sample_counts(market, day, payload, ev)
            rows.append({"trade_date": day, "market": market, "source": origin,
                         "date_validation": ev["status"], **counts,
                         "official_security_type_verified": False,
                         "row_date_verified": market == "TWSE"})
            print("CHECKED:", day, market, origin, counts, flush=True)
            if origin == "DOWNLOADED":
                time.sleep(1.5)
    folder.mkdir(parents=True, exist_ok=True)
    with summary.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("CHECKED_PAIRS:", len(rows))
    print("SUMMARY:", summary)
    print("DATABASE_WRITES: 0")
    return rows


if __name__ == "__main__":
    main()
