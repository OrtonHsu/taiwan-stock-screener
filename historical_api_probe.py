"""V2.3 research-only historical API probe: GET requests, no database writes.

Run with Windows trust store if required:
python -c "import truststore,runpy;truststore.inject_into_ssl();runpy.run_path('historical_api_probe.py',run_name='__main__')"
All results are evidence reports, not historical completeness guarantees.
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
    # Official daily index table, observed to return ten tables for historical days.
    "TWSE_index": ("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", {"response": "json", "type": "IND"}, "date"),
    "TWSE_institutional": ("https://www.twse.com.tw/rwd/zh/fund/BFI82U", {"type": "day", "response": "json"}, "dayDate"),
    "TPEX_quotes": ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", {"response": "json"}, "date"),
}
REQUIRED = {
    "TWSE_stock": ("日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"),
    "TWSE_turnover": ("日期", "成交金額", "發行量加權股價指數"),
    "TWSE_index": ("指數", "收盤指數"),
    "TWSE_institutional": ("單位名稱", "買進金額", "賣出金額"),
    "TPEX_quotes": ("代號", "收盤"),
}

def roc_day(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{d.year-1911:03d}/{d.month:02d}/{d.day:02d}"

def date_tokens(day: str) -> set[str]:
    d = date.fromisoformat(day)
    roc = roc_day(day)
    return {day, day.replace("-", "/"), day.replace("-", ""),
            roc, roc.replace("/", ""), f"{d.year-1911}/{d.month:02d}/{d.day:02d}",
            f"{d.year-1911}年{d.month}月{d.day}日",
            f"{d.year-1911}年{d.month:02d}月{d.day:02d}日"}

def make_url(source: str, day: str) -> str:
    if source not in SOURCES:
        raise ValueError("Unknown source")
    date.fromisoformat(day)
    base, fixed, date_key = SOURCES[source]
    params = dict(fixed)
    params[date_key] = day.replace("-", "") if source.startswith("TWSE") else roc_day(day)
    return base + "?" + urlencode(params)

def _date_in(value: object, day: str) -> bool:
    if not isinstance(value, (str, int)):
        return False
    text = str(value).strip()
    return any(re.search(r"(?<!\\d)" + re.escape(token) + r"(?!\\d)", text) for token in date_tokens(day))

def _rows_have_date(rows: list, day: str) -> bool:
    for row in rows[:500]:
        cells = row if isinstance(row, list) else list(row.values()) if isinstance(row, dict) else [row]
        if any(_date_in(cell, day) for cell in cells):
            return True
    return False

def _fields(table: dict) -> list[str]:
    raw = table.get("fields", [])
    return [str(x).strip() for x in raw] if isinstance(raw, list) else []

def _field_check(source: str, fields: list[str]) -> list[str]:
    # TPEx schemas vary by table/version; aliases are checked explicitly.
    if source == "TPEX_quotes":
        groups = (("代號", "證券代號", "股票代號"), ("收盤", "收盤價"))
        return ["/".join(group) for group in groups if not any(any(alias in f for alias in group) for f in fields)]
    return [key for key in REQUIRED[source] if not any(key in f for f in fields)]

def _index_evidence(payload: dict, day: str) -> dict:
    """Validate daily index table by ROC title, target name and numeric closing value."""
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return {"status": "unverified", "reason": "index tables missing"}
    matching = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        title = str(table.get("title", ""))
        # A date in a generic top-level response is insufficient for this endpoint.
        if not _date_in(title, day):
            continue
        fields = _fields(table)
        if not ("指數" in fields and "收盤指數" in fields):
            continue
        name_col, close_col = fields.index("指數"), fields.index("收盤指數")
        for row in table.get("data", []):
            if not isinstance(row, list) or len(row) <= max(name_col, close_col):
                continue
            if str(row[name_col]).strip() not in ("發行量加權股價指數", "臺灣證券交易所發行量加權股價指數"):
                continue
            raw = str(row[close_col]).strip().replace(",", "")
            try:
                closing = float(raw)
            except ValueError:
                return {"status": "unverified", "reason": "invalid index close", "value": raw[:50]}
            if not (0 < closing < float("inf")):
                return {"status": "unverified", "reason": "invalid index close", "value": raw[:50]}
            matching.append((title, closing))
    if len(matching) != 1:
        return {"status": "unverified", "reason": "requested date and target index not uniquely confirmed",
                "matching_rows": len(matching), "table_count": len(tables)}
    title, closing = matching[0]
    return {"status": "date_and_fields_confirmed", "date_evidence": "table_title",
            "index_name": "發行量加權股價指數", "index_close": closing,
            "table_title": title[:100], "table_count": len(tables)}

def _institutional_evidence(p, day):
    """Strictly verify BFI82U date, title, categories, and amounts."""
    import math
    expected = date.fromisoformat(day)
    title = p.get("title", "")
    if (str(p.get("date", "")) != day.replace("-", "") or
            not isinstance(title, str) or
            f"{expected.year-1911}年{expected.month}月{expected.day}日" not in title or
            "三大法人" not in title):
        return {"status": "unverified", "reason": "institutional date/title mismatch"}
    fields = _fields(p)
    required = ("單位名稱", "買進金額", "賣出金額", "買賣差額")
    if any(fields.count(k) != 1 for k in required):
        return {"status": "unverified", "reason": "institutional fields missing or duplicated"}
    rows = p.get("data")
    if not isinstance(rows, list) or not rows:
        return {"status": "unverified", "reason": "institutional rows missing"}
    idx = [fields.index(k) for k in required]
    categories = []
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(idx):
            return {"status": "unverified", "reason": "institutional malformed row"}
        category = str(row[idx[0]]).strip()
        if not category or category in categories:
            return {"status": "unverified", "reason": "institutional duplicate/empty category"}
        categories.append(category)
        values = []
        for col in idx[1:]:
            raw = str(row[col]).replace(",", "").strip()
            if not re.fullmatch(r"[+-]?[0-9]+(?:[.][0-9]+)?", raw):
                return {"status": "unverified", "reason": "institutional invalid amount"}
            value = float(raw)
            if not math.isfinite(value):
                return {"status": "unverified", "reason": "institutional invalid amount"}
            values.append(value)
        buy, sell, net = values
        if buy < 0 or sell < 0 or abs(buy-sell-net) > max(1, (buy+sell)*1e-10):
            return {"status": "unverified", "reason": "institutional amount mismatch"}
    foreign = [c for c in categories if ("外資" in c or "外國" in c) and "自營商" not in c]
    if len(foreign) != 1:
        return {"status": "unverified", "reason": "foreign category not unique", "categories": categories}
    return {"status": "date_and_fields_confirmed", "date_evidence": "date_and_title",
            "rows": len(rows), "fields": fields, "categories": categories,
            "foreign_category": foreign[0], "amounts_validated": True}

def inspect_payload(payload: object, requested_day: str, source: str = "TWSE_stock") -> dict:
    """Conservative validation: date + schema evidence, never trust HTTP 200 alone."""
    if source not in SOURCES:
        raise ValueError("Unknown source")
    date.fromisoformat(requested_day)
    if not isinstance(payload, dict):
        return {"status": "unverified", "reason": "non-object JSON"}
    stat = str(payload.get("stat", payload.get("status", "")))
    result = {"api_status": stat[:100]}
    if source == "TWSE_index":
        return {**result, **_index_evidence(payload, requested_day)}
    if source == "TWSE_institutional":
        return {**result, **_institutional_evidence(payload, requested_day)}
    tables = payload.get("tables")
    candidates = []
    if isinstance(payload.get("data"), list):
        candidates.append({"data": payload["data"], "fields": payload.get("fields", []),
                           "title": payload.get("title", "")})
    if isinstance(tables, list):
        candidates.extend(t for t in tables if isinstance(t, dict) and isinstance(t.get("data"), list))
    if not candidates:
        return {**result, "status": "unverified", "reason": "no data array or parseable tables",
                "keys": list(payload)[:15]}
    # Look for requested day in row values, or in explicit date metadata.
    # A date in an arbitrary title is not sufficient if no schema exists.
    for table in candidates:
        rows = table.get("data", [])
        fields = _fields(table)
        if not rows:
            continue
        row_date = _rows_have_date(rows, requested_day)
        metadata_date = any(_date_in(table.get(key), requested_day) for key in ("date", "title", "subtitle"))
        metadata_date |= any(_date_in(payload.get(key), requested_day) for key in ("date", "title", "subtitle"))
        missing = _field_check(source, fields)
        # For monthly TWSE tables require the requested date in a row.
        # For daily institutional reports, explicitly dated metadata is acceptable.
        dated = row_date or (source in ("TWSE_institutional", "TPEX_quotes") and metadata_date)
        if dated and not missing:
            return {**result, "status": "date_and_fields_confirmed",
                    "date_evidence": "row" if row_date else "metadata",
                    "rows": len(rows), "fields": fields[:25]}
        if dated:
            return {**result, "status": "unverified", "reason": "required fields missing",
                    "missing_fields": missing, "date_evidence": "row" if row_date else "metadata",
                    "rows": len(rows), "fields": fields[:25]}
    # Date may not be in BFI82U payload; response does not prove requested-day data.
    first = next((t for t in candidates if t.get("data")), candidates[0])
    return {**result, "status": "unverified", "reason": "requested date not proven",
            "rows": len(first.get("data", [])), "fields": _fields(first)[:25],
            "top_level_date": str(payload.get("date", ""))[:80],
            "titles": [str(t.get("title", ""))[:100] for t in candidates[:4]]}

def inspect_response(raw: bytes, requested_day: str, source: str, *, http_status: int | None = None,
                     content_type: str = "") -> dict:
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        preview = raw[:120].decode("utf-8", errors="replace")
        return {"status": "unverified", "reason": "non-JSON response",
                "http_status": http_status, "content_type": content_type[:80],
                "response_prefix": preview, "parse_error": type(exc).__name__}
    return {"http_status": http_status, "content_type": content_type[:80],
            **inspect_payload(payload, requested_day, source)}

def probe(source: str, day: str, *, opener=urlopen) -> dict:
    url = make_url(source, day)
    result = {"source": source, "requested_day": day, "url": url}
    try:
        request = Request(url, headers={"User-Agent": "TaiwanStockScreener-Research/1.0",
                                        "Accept": "application/json"})
        with opener(request, timeout=15) as response:
            status = getattr(response, "status", None)
            headers = getattr(response, "headers", {})
            content_type = headers.get("Content-Type", "") if hasattr(headers, "get") else ""
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("response exceeds safety limit")
        result.update(inspect_response(raw, day, source, http_status=status, content_type=content_type))
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
