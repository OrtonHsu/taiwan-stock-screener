
"""Save date-verified TWSE and TPEx daily market snapshots.

Protect against incomplete/stale market responses. Preserve successful
market writes, but fail the workflow when either market was not saved.
"""

import re
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from screener import num
from storage import Store


SOURCES = {
    "TWSE": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "TPEX": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
}

TWSE_BACKUP = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

HEADERS = {
    "User-Agent": "Mozilla/5.0 TaiwanStockScreener/1.2",
    "Accept": "application/json",
}

# Safety thresholds, not official listing counts.
# Adjust only after checking actual daily coverage.
MIN_RECORDS = {"TWSE": 900, "TPEX": 700}

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5


def trading_date(raw):
    value = str(raw or "").strip()

    if re.fullmatch(r"\d{7}", value):
        year = int(value[:3]) + 1911
        month = int(value[3:5])
        day = int(value[5:])

    elif re.fullmatch(r"\d{8}", value):
        year = int(value[:4])
        month = int(value[4:6])
        day = int(value[6:])

    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        year, month, day = map(int, value.split("-"))

    else:
        raise ValueError(
            f"不支援的官方交易日期: {value!r}"
        )

    return date(year, month, day).isoformat()


def pick(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def check_coverage(market, records):
    minimum = MIN_RECORDS[market]

    if len(records) < minimum:
        raise ValueError(
            f"{market}: 有效股票僅 {len(records)} 檔，"
            f"低於安全門檻 {minimum} 檔；拒絕寫入"
        )


def parse_market(market, payload):
    """Parse official OpenAPI snapshot."""

    if market not in MIN_RECORDS:
        raise ValueError(f"不支援的市場: {market}")

    if not isinstance(payload, list) or not payload:
        raise ValueError(
            f"{market}: 官方回傳不是非空陣列"
        )

    records = []
    dates = set()
    seen_codes = set()

    for row in payload:
        if not isinstance(row, dict):
            raise ValueError(
                f"{market}: 官方包含非物件資料列"
            )

        raw_date = pick(row, "Date", "日期")

        if raw_date is None:
            raise ValueError(
                f"{market}: 官方資料缺少交易日期"
            )

        day = trading_date(raw_date)
        dates.add(day)

        if market == "TWSE":
            code = str(
                pick(row, "Code", "證券代號") or ""
            ).strip()

            name = pick(row, "Name", "證券名稱")

            close = num(
                pick(row, "ClosingPrice", "收盤價")
            )

            volume = num(
                pick(row, "TradeVolume", "成交股數")
            )

            amount = num(
                pick(row, "TradeValue", "成交金額")
            )

        else:
            code = str(
                pick(
                    row,
                    "SecuritiesCompanyCode",
                    "SecuritiesCode",
                    "證券代號",
                ) or ""
            ).strip()

            name = pick(
                row,
                "CompanyName",
                "Name",
                "證券名稱",
            )

            close = num(
                pick(
                    row,
                    "Close",
                    "ClosingPrice",
                    "收盤",
                )
            )

            volume = num(
                pick(
                    row,
                    "TradingShares",
                    "TradeVolume",
                    "成交股數",
                )
            )

            amount = num(
                pick(
                    row,
                    "TransactionAmount",
                    "TradingAmount",
                    "TradeValue",
                    "成交金額",
                )
            )

        # Ignore non-four-digit instruments and invalid trades.
        if not re.fullmatch(r"\d{4}", code):
            continue

        if (
            close is None
            or volume is None
            or amount is None
        ):
            continue

        if (
            close <= 0
            or volume <= 0
            or amount < 0
        ):
            continue

        if code in seen_codes:
            raise ValueError(
                f"{market}: 同一交易日出現"
                f"重複股票代號 {code}"
            )

        seen_codes.add(code)

        records.append(
            (
                day,
                market,
                code,
                str(name or code).strip(),
                close,
                volume,
                amount,
            )
        )

    if len(dates) != 1:
        raise ValueError(
            f"{market}: 官方資料混有不同日期: "
            f"{sorted(dates)}"
        )

    check_coverage(market, records)

    return dates.pop(), records


def parse_twse_backup(payload, expected_day):
    """Parse official MI_INDEX closing-price table."""

    if not isinstance(payload, dict):
        raise ValueError(
            "TWSE 備援: 官方回傳格式錯誤"
        )

    tables = payload.get("tables")

    if not isinstance(tables, list):
        raise ValueError(
            "TWSE 備援: 找不到 tables"
        )

    required = (
        "證券代號",
        "證券名稱",
        "成交股數",
        "成交金額",
        "收盤價",
    )

    for table in tables:
        if not isinstance(table, dict):
            continue

        fields = table.get("fields", [])

        if (
            not isinstance(fields, list)
            or not set(required).issubset(fields)
        ):
            continue

        title = str(table.get("title", ""))

        match = re.search(
            r"(\d{2,3})年\s*"
            r"(\d{1,2})月\s*"
            r"(\d{1,2})日",
            title,
        )

        if not match:
            raise ValueError(
                f"TWSE 備援: 無法辨識表格日期: {title}"
            )

        roc_year, month, day_num = map(
            int,
            match.groups(),
        )

        official_day = date(
            roc_year + 1911,
            month,
            day_num,
        ).isoformat()

        if official_day != expected_day:
            raise ValueError(
                f"TWSE 備援: 官方日期 {official_day}，"
                f"要求日期 {expected_day}"
            )

        indices = {
            field: fields.index(field)
            for field in required
        }

        source_rows = table.get("data", [])

        if not isinstance(source_rows, list):
            raise ValueError(
                "TWSE 備援: 表格資料格式異常"
            )

        records = []
        seen = set()

        for row in source_rows:
            if not isinstance(row, list):
                continue

            try:
                code = str(
                    row[indices["證券代號"]]
                ).strip()

                name = str(
                    row[indices["證券名稱"]]
                ).strip()

                volume = num(
                    row[indices["成交股數"]]
                )

                amount = num(
                    row[indices["成交金額"]]
                )

                close = num(
                    row[indices["收盤價"]]
                )

            except (
                IndexError,
                TypeError,
                ValueError,
            ):
                continue

            if not re.fullmatch(r"\d{4}", code):
                continue

            if (
                close is None
                or volume is None
                or amount is None
            ):
                continue

            if (
                close <= 0
                or volume <= 0
                or amount < 0
            ):
                continue

            if code in seen:
                raise ValueError(
                    f"TWSE 備援: 重複股票代號 {code}"
                )

            seen.add(code)

            records.append(
                (
                    official_day,
                    "TWSE",
                    code,
                    name or code,
                    close,
                    volume,
                    amount,
                )
            )

        check_coverage("TWSE", records)

        return official_day, records

    raise ValueError(
        "TWSE 備援: 找不到包含個股收盤行情的表格"
    )


def get_twse(session, today):
    """Try TWSE OpenAPI, then official MI_INDEX backup."""

    primary_error = None

    try:
        response = session.get(
            SOURCES["TWSE"],
            headers=HEADERS,
            timeout=45,
        )

        response.raise_for_status()

        day, records = parse_market(
            "TWSE",
            response.json(),
        )

        if day != today:
            raise ValueError(
                f"TWSE PRIMARY STALE: "
                f"官方 {day}，今日 {today}"
            )

        print(
            f"TWSE PRIMARY OK: {day}; "
            f"{len(records)} 檔",
            flush=True,
        )

        return day, records

    except (
        requests.RequestException,
        ValueError,
    ) as exc:
        primary_error = exc

        print(
            f"TWSE PRIMARY FAILED: {exc}; "
            "嘗試官方備援",
            flush=True,
        )

    try:
        response = session.get(
            TWSE_BACKUP,
            params={
                "date": today.replace("-", ""),
                "type": "ALLBUT0999",
                "response": "json",
            },
            headers=HEADERS,
            timeout=60,
        )

        response.raise_for_status()

        day, records = parse_twse_backup(
            response.json(),
            today,
        )

        print(
            f"TWSE BACKUP OK: {day}; "
            f"{len(records)} 檔",
            flush=True,
        )

        return day, records

    except (
        requests.RequestException,
        ValueError,
    ) as exc:
        raise RuntimeError(
            "TWSE 主要與備援來源都失敗；"
            f"主要: {primary_error}; "
            f"備援: {exc}"
        ) from exc


def get_tpex(session, today):
    """Require current and sufficiently broad TPEx snapshot."""

    response = session.get(
        SOURCES["TPEX"],
        headers=HEADERS,
        timeout=45,
    )

    response.raise_for_status()

    day, records = parse_market(
        "TPEX",
        response.json(),
    )

    if day != today:
        raise ValueError(
            f"TPEX STALE: "
            f"官方 {day}，今日 {today}"
        )

    print(
        f"TPEX PRIMARY OK: {day}; "
        f"{len(records)} 檔",
        flush=True,
    )

    return day, records


def get_with_retries(session, market, today):
    """Retry bad responses without storing partial data."""

    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if market == "TWSE":
                return get_twse(session, today)

            if market == "TPEX":
                return get_tpex(session, today)

            raise ValueError(
                f"未知市場: {market}"
            )

        except (
            requests.RequestException,
            ValueError,
            RuntimeError,
        ) as exc:
            last_error = exc

            print(
                f"RETRY {market} "
                f"{attempt}/{MAX_ATTEMPTS}: "
                f"{exc}",
                flush=True,
            )

            if attempt < MAX_ATTEMPTS:
                time.sleep(
                    RETRY_DELAY_SECONDS * attempt
                )

    raise RuntimeError(
        f"{market}: 取得今日完整行情失敗，"
        f"已嘗試 {MAX_ATTEMPTS} 次: "
        f"{last_error}"
    )


def main():
    now = datetime.now(
        ZoneInfo("Asia/Taipei")
    )

    if now.weekday() >= 5 or now.hour < 16:
        raise RuntimeError(
            "只允許台灣時間週一至週五 16:00 後執行"
        )

    today = now.date().isoformat()

    batches = {}
    failures = []

    with requests.Session() as session:
        for market in ("TWSE", "TPEX"):
            try:
                batches[market] = get_with_retries(
                    session,
                    market,
                    today,
                )

            except Exception as exc:
                failures.append(
                    f"{market} DOWNLOAD: {exc}"
                )

                print(
                    f"FAILED {market} DOWNLOAD: {exc}",
                    flush=True,
                )

    # Save valid market data even if the other market failed.
    if batches:
        db = Store()

        try:
            for market, (day, records) in batches.items():
                if day != today:
                    failures.append(
                        f"{market}: 官方日期 {day} "
                        f"並非 {today}"
                    )
                    continue

                try:
                    count = db.store(records)

                    print(
                        f"WRITTEN {market}: "
                        f"日期 {day}; "
                        f"有效股票 {len(records)} 檔; "
                        f"送交寫入 {count} 筆",
                        flush=True,
                    )

                except Exception as exc:
                    failures.append(
                        f"{market} STORE: {exc}"
                    )

                    print(
                        f"FAILED {market} STORE: {exc}",
                        flush=True,
                    )

        finally:
            db.close()

    if failures:
        for detail in failures:
            print(
                f"FAILED_DETAIL {detail}",
                flush=True,
            )

        raise RuntimeError(
            "每日快照未完整更新，"
            f"失敗項目 {len(failures)} 個；"
            "已成功寫入的市場資料仍保留，"
            "請查 FAILED_DETAIL"
        )

    print(
        f"DAILY_UPDATE_OK {today}: "
        "TWSE / TPEX 皆成功",
        flush=True,
    )


if __name__ == "__main__":
    main()
