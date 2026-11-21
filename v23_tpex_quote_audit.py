"""V2.3 TPEx historic quote audit: OFFLINE ONLY, never writes a database.

Reads one pre-fetched official TPEx dailyQuotes JSON; verifies response date,
selects a uniquely identifiable quote table, and classifies four-digit codes.
It NEVER substitutes average price or next-day reference for missing close.

Run:
  python v23_tpex_quote_audit.py --input v23_research_samples/TPEX_20260102.json --day 2026-01-02

Output is a new local CSV next to the input JSON.  No HTTP/DB/GitHub calls.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELD_ALIASES = {
    "code": ("代號",),
    "name": ("名稱",),
    "close": ("收盤",),
    "volume": ("成交股數",),
    "amount": ("成交金額(元)",),
    "average": ("均價",),
    "next_reference": ("次日參考價",),
}
MISSING = {"", "-", "--", "---", "----", "N/A", "NA", "NULL"}


def _normalized(value: object) -> str:
    return re.sub(r"\s+", "", str(value)).replace("（", "(").replace("）", ")")


def parse_number(value: object) -> Decimal | None:
    raw = str(value).strip().replace(",", "")
    if raw.upper() in MISSING:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def field_positions(fields: object) -> dict[str, int] | None:
    if not isinstance(fields, list):
        return None
    normalized = [_normalized(value) for value in fields]
    positions = {}
    for key, aliases in FIELD_ALIASES.items():
        matches = [i for i, value in enumerate(normalized) if value in aliases]
        if len(matches) != 1:
            return None
        positions[key] = matches[0]
    return positions


def select_quotes(payload: object, requested_day: str):
    """Validate date metadata and exact official table schema.

    TPEx quote rows do NOT have an independent trade_date field.  Therefore
    response-date verification is metadata-level, not per-row confirmation.
    """
    day = date.fromisoformat(requested_day)
    if not isinstance(payload, dict):
        raise ValueError("Expected object JSON")
    if str(payload.get("stat", "")).upper() != "OK":
        raise ValueError("TPEx API status not OK")
    expected = day.strftime("%Y%m%d")
    if str(payload.get("date", "")).strip() != expected:
        raise ValueError(f"TPEx response date mismatch: expected {expected}")
    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise ValueError("No tables array")
    candidates = []
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("data"), list):
            continue
        if not table["data"]:
            continue
        positions = field_positions(table.get("fields"))
        if positions is not None:
            candidates.append((table["data"], positions))
    if len(candidates) != 1:
        raise ValueError(f"Cannot uniquely select TPEx daily quote table: {len(candidates)} matches")
    return candidates[0]


def audit_payload(payload: object, requested_day: str):
    rows, idx = select_quotes(payload, requested_day)
    output = []
    seen = set()
    raw_count = len(rows)
    nonfour = 0
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(idx.values()):
            raise ValueError("Malformed TPEx table row; halt rather than silently drop")
        code = str(row[idx["code"]]).strip()
        if not re.fullmatch(r"\d{4}", code):
            nonfour += 1
            continue
        if code in seen:
            raise ValueError(f"Duplicate 4-digit code in official response: {code}")
        seen.add(code)
        name = str(row[idx["name"]]).strip()
        close = parse_number(row[idx["close"]])
        volume = parse_number(row[idx["volume"]])
        amount = parse_number(row[idx["amount"]])
        avg = parse_number(row[idx["average"]])
        next_ref = parse_number(row[idx["next_reference"]])
        if volume is None or amount is None or volume < 0 or amount < 0:
            state = "INVALID_VOLUME_OR_AMOUNT"
        elif close is None and volume > 0:
            state = "MISSING_CLOSE_WITH_VOLUME"
        elif close is None and volume == 0 and amount == 0:
            state = "MISSING_CLOSE_NO_TRADE"
        elif close is not None and close > 0 and volume > 0:
            state = "VALID_CLOSE_WITH_VOLUME"
        elif close is not None and close > 0 and volume == 0 and amount == 0:
            state = "VALID_CLOSE_ZERO_VOLUME"
        else:
            state = "UNRESOLVED_VALUES"
        output.append({
            "trade_date": requested_day,
            "market": "TPEX",
            "code": code,
            "name": name,
            "close": "" if close is None else str(close),
            "volume": "" if volume is None else str(volume),
            "amount": "" if amount is None else str(amount),
            "average_price_not_close": "" if avg is None else str(avg),
            "next_reference_not_close": "" if next_ref is None else str(next_ref),
            "classification": state,
            "security_type": "UNVERIFIED",
            "date_evidence": "top_level_metadata_only",
        })
    stats = Counter(x["classification"] for x in output)
    stats["RAW_ROWS"] = raw_count
    stats["NON_4_DIGIT_EXCLUDED"] = nonfour
    stats["FOUR_DIGIT_UNIQUE"] = len(output)
    return output, stats


def audit_file(input_path: str | Path, day: str, output_path: str | Path | None = None):
    src = Path(input_path)
    if src.stat().st_size > 15_000_000:
        raise ValueError("Input JSON exceeds 15 MB safety limit")
    with src.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    rows, stats = audit_payload(payload, day)
    target = Path(output_path) if output_path else src.with_name(src.stem + "_audit.csv")
    if src.resolve() == target.resolve():
        raise ValueError("Output cannot replace raw official JSON")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects prior research results from accidental overwrites.
    with target.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "trade_date", "market", "code", "name", "close", "volume", "amount",
            "average_price_not_close", "next_reference_not_close",
            "classification", "security_type", "date_evidence"])
        writer.writeheader()
        writer.writerows(rows)
    return target, stats


def main(argv=None):
    parser = argparse.ArgumentParser(description="V2.3 offline TPEx quote audit; no DB/network access")
    parser.add_argument("--input", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--output")
    options = parser.parse_args(argv)
    output, stats = audit_file(options.input, options.day, options.output)
    print("OFFLINE_ONLY: True")
    for name in ("RAW_ROWS", "FOUR_DIGIT_UNIQUE", "NON_4_DIGIT_EXCLUDED",
                 "VALID_CLOSE_WITH_VOLUME", "MISSING_CLOSE_WITH_VOLUME",
                 "MISSING_CLOSE_NO_TRADE", "VALID_CLOSE_ZERO_VOLUME",
                 "INVALID_VOLUME_OR_AMOUNT", "UNRESOLVED_VALUES"):
        print(name + ":", stats.get(name, 0))
    print("SECURITY_TYPE_VERIFIED: False")
    print("ROW_DATE_VERIFIED: False")
    print("DATABASE_WRITES: 0")
    print("OUTPUT:", output)


if __name__ == "__main__":
    main()
