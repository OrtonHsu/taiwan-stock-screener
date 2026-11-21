"""Read-only historical endpoint capability probe. No database or file writes.

Run: python historical_api_probe.py
Only HTTP GET; bounded responses, timeouts and explicit date/field validation.
A successful HTTP status alone is NOT proof of historical availability.
"""
from __future__ import annotations
import json
import re
import time
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATES = ("2021-11-19", "2023-11-20", "2025-11-20", "2026-11-20")
MAX_BYTES = 5_000_000
SOURCES = {
    "TWSE_stock": ("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY", {"stockNo": "2330", "response": "json"}, "date"),
    "TWSE_turnover": ("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK", {"response": "json"}, "date"),
    "TWSE_index": ("https://www.twse.com.tw/rwd/zh/indicesReport/MI_5MINS_HIST", {"response": "json"}, "date"),
    "TWSE_institutional": ("https://www.twse.com.tw/rwd/zh/fund/BFI82U", {"type": "day", "response": "json"}, "day"),
    # Experimental historical TPEx query endpoint; validate its schema before use.
    "TPEX_quotes": ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", {"response": "json"}, "date"),
}
def roc_day(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{d.year-1911:03d}/{d.month:02d}/{d.day:02d}"

def make_url(source: str, day: str) -> str:
    if source not in SOURCES:
        raise ValueError("Unknown source")
    date.fromisoformat(day)
    base, fixed, date_key = SOURCES[source]
    params = dict(fixed)
    params[date_key] = day.replace("-", "") if source.startswith("TWSE") else roc_day(day)
    return base + "?" + urlencode(params)

def inspect_payload(payload: object, requested_day: str) -> dict:
    """Fail closed: report evidence, not speculative success."""
    if not isinstance(payload, dict):
        return {"status": "unverified", "reason": "non-object JSON"}
    stat = str(payload.get("stat", payload.get("status", "")))
    rows = payload.get("data")
    if not isinstance(rows, list):
        # TPEx may nest records; do not assume the structure.
        return {"status": "unverified", "reason": "data array not found", "api_status": stat[:100],
                "keys": list(payload)[:15]}
    if not rows:
        return {"status": "unverified", "reason": "empty data", "api_status": stat[:100]}
    fields = payload.get("fields", [])
    fields = fields if isinstance(fields, list) else []
    # Some endpoints return monthly tables; a response with rows but no
    # verified date must never be treated as a confirmed historical response.
    target_tokens = {requested_day, requested_day.replace("-", "/"),
                     requested_day.replace("-", ""), roc_day(requested_day),
                     roc_day(requested_day).replace("/", "")}
    dates_seen = any(any(token in str(cell) for token in target_tokens)
                     for row in rows[:500] for cell in (row if isinstance(row, list) else [row]))
    return {"status": "date_confirmed" if dates_seen else "unverified",
            "reason": "requested date seen in returned rows" if dates_seen else "requested date not found in first 500 rows",
            "api_status": stat[:100], "rows": len(rows), "fields": [str(x) for x in fields[:20]]}

def probe(source: str, day: str, *, opener=urlopen) -> dict:
    url = make_url(source, day)
    result = {"source": source, "requested_day": day, "url": url}
    try:
        request = Request(url, headers={"User-Agent": "TaiwanStockScreener-Research/1.0",
                                        "Accept": "application/json"})
        with opener(request, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("response exceeds safety limit")
        result.update(inspect_payload(json.loads(raw), day))
    except Exception as exc:
        result.update(status="unverified", reason=f"{type(exc).__name__}: {str(exc)[:180]}")
    return result

def main() -> None:
    for source in SOURCES:
        for day in DATES:
            print(json.dumps(probe(source, day), ensure_ascii=False), flush=True)
            time.sleep(1)

if __name__ == "__main__":
    main()
