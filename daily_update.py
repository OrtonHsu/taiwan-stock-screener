
"""Save date-verified TWSE and TPEx daily market snapshots."""

import re
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
        raise ValueError(f"不支援的官方交易日期: {value!r}")

    return date(year, month, day).isoformat()


def pick(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def parse_market(market, payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{market}: 官方回傳不是非空陣列")

    records = []
    dates = set()

    for row in payload:
        if not isinstance(row, dict):
            continue

        raw_date = pick(row, "Date", "日期")
        if raw_date is None:
            raise ValueError(f"{market}: 官方資料缺少交易日期")

        day = trading_date(raw_date)
        dates.add(day)

        if market == "TWSE":
            code = str(
                pick(row, "Code", "證券代號") or ""
            ).strip()
            name = pick(row, "Name", "證券名稱")
            close = num(pick(row, "ClosingPrice", "收盤價"))
            volume = num(pick(row, "TradeVolume", "成交股數"))
            amount = num(pick(row, "TradeValue", "成交金額"))

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
                row, "CompanyName", "Name", "證券名稱"
            )

            close = num(
                pick(row, "Close", "ClosingPrice", "收盤")
            )

            volume = num(
                pick(row, "TradingShares", "TradeVolume", "成交股數")
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

        if not re.fullmatch(r"\d{4}", code):
            continue

        if close is None or volume is None or amount is None:
            continue

        if close <= 0 or volume <= 0 or amount < 0:
            continue

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
            f"{market}: 官方資料混有不同日期: {sorted(dates)}"
        )

    if len(records) < 100:
        raise ValueError(
            f"{market}: 有效股票僅 {len(records)} 檔，拒絕寫入"
        )

    return dates.pop(), records


def parse_twse_backup(payload, expected_day):
    """Parse official TWSE MI_INDEX daily closing-price table."""

    if not isinstance(payload, dict):
        raise ValueError("TWSE 備援: 官方回傳格式錯誤")

    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise ValueError("TWSE 備援: 找不到 tables")

    required = {
        "證券代號",
        "證券名稱",
        "成交股數",
        "成交金額",
        "收盤價",
    }

    for table in tables:
        if not isinstance(table, dict):
            continue

        fields = table.get("fields", [])
        if not isinstance(fields, list):
            continue

        if not required.issubset(set(fields)):
            continue

        title = str(table.get("title", ""))

        match = re.search(
            r"(\d{2,3})年\s*(\d{1,2})月\s*(\d{1,2})日",
            title,
        )

        if not match:
            raise ValueError(
                f"TWSE 備援: 無法辨識表格日期: {title}"
            )

        roc_year, month, day_num = map(
            int, match.groups()
        )

        official_day = date(
            roc_year + 1911, month, day_num
        ).isoformat()

        if official_day != expected_day:
            raise ValueError(
                f"TWSE 備援: 官方日期 {official_day}，"
                f"今天 {expected_day}"
            )

        indices = {
            field: fields.index(field)
            for field in required
        }

        records = []
        seen = set()

        for row in table.get("data", []):
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

            except (IndexError, TypeError, ValueError):
                continue

            if not re.fullmatch(r"\d{4}", code):
                continue

            if code in seen:
                continue

            if close is None or volume is None or amount is None:
                continue

            if close <= 0 or volume <= 0 or amount < 0:
                continue

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

        if len(records) < 100:
            raise ValueError(
                f"TWSE 備援: 有效股票僅 {len(records)} 檔，拒絕寫入"
            )

        return official_day, records

    raise ValueError(
        "TWSE 備援: 找不到包含個股收盤行情的表格"
    )


def get_twse(session, today):
    """Try primary TWSE API, then official backup if needed."""

    try:
        response = session.get(
            SOURCES["TWSE"],
            headers=HEADERS,
            timeout=45,
        )
        response.raise_for_status()

        day, records = parse_market(
            "TWSE", response.json()
        )

        if day == today:
            print(f"TWSE PRIMARY OK: {day}")
            return day, records

        print(
            f"TWSE PRIMARY STALE: {day}，"
            f"今天 {today}，改用備援"
        )

    except (requests.RequestException, ValueError) as exc:
        print(
            f"TWSE PRIMARY FAILED: {exc}，改用備援"
        )

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
        response.json(), today
    )

    print(f"TWSE BACKUP OK: {day}")
    return day, records


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

    with requests.Session() as session:
        try:
            batches["TWSE"] = get_twse(
                session, today
            )
        except (requests.RequestException, ValueError) as exc:
            print(f"TWSE FAILED: {exc}")

        try:
            response = session.get(
                SOURCES["TPEX"],
                headers=HEADERS,
                timeout=45,
            )
            response.raise_for_status()

            batches["TPEX"] = parse_market(
                "TPEX", response.json()
            )

        except (requests.RequestException, ValueError) as exc:
            print(f"TPEX FAILED: {exc}")

    if not batches:
        raise RuntimeError(
            "TWSE 與 TPEX 均無法取得有效資料"
        )

    db = Store()

    try:
        for market, (day, records) in batches.items():
            if day != today:
                print(
                    f"SKIPPED {market}: 官方日期 {day}，"
                    f"今天 {today}"
                )
                continue

            count = db.store(records)

            print(
                f"WRITTEN {market}: 日期 {day}，"
                f"有效股票 {len(records)} 檔，"
                f"寫入 {count} 筆"
            )

    finally:
        db.close()


if __name__ == "__main__":
    main()
