"""Save date-verified latest TWSE and TPEx whole-market daily snapshots."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from screener import num
from storage import Store

SOURCES = {
    'TWSE': 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
    'TPEX': 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
}
HEADERS = {'User-Agent': 'Mozilla/5.0 TaiwanStockScreener/1.2', 'Accept': 'application/json'}


def trading_date(raw):
    value = str(raw or '').strip()
    if re.fullmatch(r'\d{7}', value):
        year, month, day = int(value[:3]) + 1911, int(value[3:5]), int(value[5:])
    elif re.fullmatch(r'\d{8}', value):
        year, month, day = int(value[:4]), int(value[4:6]), int(value[6:])
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        year, month, day = map(int, value.split('-'))
    else:
        raise ValueError(f'不支援的官方交易日期: {value!r}')
    return date(year, month, day).isoformat()


def pick(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ''):
            return row[key]
    return None


def parse_market(market, payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError(f'{market}: 官方回傳不是非空陣列')
    records = []
    dates = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        raw_date = pick(row, 'Date', '日期')
        if raw_date is None:
            raise ValueError(f'{market}: 官方資料缺少交易日期')
        day = trading_date(raw_date)
        dates.add(day)
        if market == 'TWSE':
            code = str(pick(row, 'Code', '證券代號') or '').strip()
            name = pick(row, 'Name', '證券名稱')
            close = num(pick(row, 'ClosingPrice', '收盤價'))
            volume = num(pick(row, 'TradeVolume', '成交股數'))
            amount = num(pick(row, 'TradeValue', '成交金額'))
        else:
            code = str(pick(row, 'SecuritiesCompanyCode', 'SecuritiesCode', '證券代號') or '').strip()
            name = pick(row, 'CompanyName', 'Name', '證券名稱')
            close = num(pick(row, 'Close', 'ClosingPrice', '收盤'))
            volume = num(pick(row, 'TradingShares', 'TradeVolume', '成交股數'))
            amount = num(pick(row, 'TransactionAmount', 'TradingAmount', 'TradeValue', '成交金額'))
        if not re.fullmatch(r'\d{4}', code):
            continue
        if close is None or volume is None or amount is None:
            continue
        if close <= 0 or volume <= 0 or amount < 0:
            continue
        records.append((day, market, code, str(name or code).strip(), close, volume, amount))
    if len(dates) != 1:
        raise ValueError(f'{market}: 官方資料混有不同日期: {sorted(dates)}')
    if len(records) < 100:
        raise ValueError(f'{market}: 有效股票僅 {len(records)} 檔，拒絕寫入以防欄位異動')
    return dates.pop(), records


def main():
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    if now.weekday() >= 5 or now.hour < 16:
        raise RuntimeError('只允許台灣時間週一至週五 16:00 後執行')
    batches = {}
    with requests.Session() as session:
        for market, url in SOURCES.items():
            response = session.get(url, headers=HEADERS, timeout=45)
            response.raise_for_status()
            batches[market] = parse_market(market, response.json())
    days = {day for day, _ in batches.values()}
    if len(days) != 1:
        raise RuntimeError(f'上市與上櫃交易日期不同，拒絕寫入: {days}')
    official_day = date.fromisoformat(days.pop())
    if official_day != now.date():
        raise RuntimeError(f'官方資料日期為 {official_day}，今天為 {now.date()}；不寫入舊快照')
    rows = [row for _, batch in batches.values() for row in batch]
    db = Store()
    try:
        count = db.store(rows)
    finally:
        db.close()
    print(f'官方交易日 {official_day}; TWSE={len(batches["TWSE"][1])}; TPEX={len(batches["TPEX"][1])}; written={count}')


if __name__ == '__main__':
    main()
