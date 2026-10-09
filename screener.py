"""Official TWSE/TPEx daily snapshots and conservative daily-bar screening."""
from __future__ import annotations
import io
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests

TWSE = 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
TPEX = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'
DB = Path('data/quotes.sqlite3')
HEADERS = {'User-Agent': 'TaiwanStockScreener/1.0 (personal research)', 'Accept': 'application/json'}


def num(x):
    if x is None: return None
    s = str(x).replace(',', '').replace('＋','+').strip()
    if s in ('', '-', '--', '---', '除權', '除息', 'N/A', 'nan'): return None
    s = re.sub(r'<[^>]+>', '', s)
    try: return float(s)
    except ValueError: return None


def field(row, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ''): return row[k]
    return None


def get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list):
        raise ValueError(f'官方 API 回傳非預期格式：{url}；請檢查端點或公告')
    return payload


def parse_twse(rows, date):
    out = []
    for r in rows:
        code = str(field(r, 'Code', '證券代號') or '').strip()
        if not re.fullmatch(r'\d{4}', code): continue
        price = num(field(r, 'ClosingPrice', '收盤價'))
        volume = num(field(r, 'TradeVolume', '成交股數'))
        amount = num(field(r, 'TradeValue', '成交金額'))
        if not price or not volume or price <= 0 or volume <= 0: continue
        out.append((date, 'TWSE', code, str(field(r, 'Name','證券名稱') or code), price, volume, amount))
    return out


def parse_tpex(rows, date):
    out = []
    for r in rows:
        code = str(field(r, 'SecuritiesCompanyCode', 'SecuritiesCode', '代號', '證券代號') or '').strip()
        if not re.fullmatch(r'\d{4}', code): continue
        price = num(field(r, 'Close', 'ClosingPrice', '收盤', '收盤價'))
        volume = num(field(r, 'TradingShares', 'TradeVolume', '成交股數', '成交量'))
        amount = num(field(r, 'TradingAmount', 'TradeValue', '成交金額'))
        if not price or not volume or price <= 0 or volume <= 0: continue
        out.append((date, 'TPEX', code, str(field(r, 'CompanyName','Name','名稱','證券名稱') or code), price, volume, amount))
    return out


def connect(path=DB):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE IF NOT EXISTS prices (
      date TEXT NOT NULL, market TEXT NOT NULL, code TEXT NOT NULL,
      name TEXT NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
      amount REAL, PRIMARY KEY(date, market, code))''')
    conn.commit()
    return conn


def store(conn, rows):
    if rows:
        conn.executemany('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)', rows)
        conn.commit()
    return len(rows)


def fetch_snapshot(date=None):
    # Official OpenAPI endpoints are latest-session snapshots, NOT date-query endpoints.
    date = date or datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    results, errors = [], []
    for label, url, parser in [('TWSE', TWSE, parse_twse), ('TPEX', TPEX, parse_tpex)]:
        try:
            parsed = parser(get_json(url), date)
            if not parsed: raise ValueError('無可解析的有效行情；官方欄位可能變更')
            results.extend(parsed)
        except Exception as exc:
            errors.append(f'{label}: {exc}')
    return results, errors


def update(conn, date=None):
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    if now.weekday() >= 5 or now.hour < 16:
        return 0, ['為避免將前一交易日快照誤標為今日，僅允許台灣時間週一至週五16:00後更新。遇休市仍需人工核對資料日期。']
    rows, errors = fetch_snapshot(date)
    # Fail closed: public latest-only endpoints do not always include an explicit data date.
    # Caller must explicitly acknowledge current trading day to store rows.
    return store(conn, rows), errors


def load(conn):
    return pd.read_sql_query('SELECT * FROM prices ORDER BY date, market, code', conn)


def rank_stocks(data, min_history=65, min_amount=50_000_000, vol_ratio=1.5):
    """Score only complete histories. Prior 20-day high is shifted to avoid look-ahead."""
    if data.empty: return pd.DataFrame()
    candidates = []
    for (market, code), g in data.groupby(['market','code']):
        g = g.sort_values('date').drop_duplicates('date')
        if len(g) < min_history: continue
        p = g['close'].astype(float); v = g['volume'].astype(float)
        amt = g['amount'].astype(float)
        ma20, ma60 = p.rolling(20).mean(), p.rolling(60).mean()
        avgvol = v.rolling(20).mean()
        high20 = p.shift(1).rolling(20).max()
        last = g.iloc[-1]
        if any(pd.isna(x) for x in (ma20.iloc[-1],ma60.iloc[-1],avgvol.iloc[-1],high20.iloc[-1])): continue
        avg_amount = amt.tail(20).mean()
        # Reject missing turnover instead of inventing amounts.
        if pd.isna(avg_amount) or avg_amount < min_amount: continue
        if not (p.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]): continue
        if not (p.iloc[-1] > high20.iloc[-1]): continue
        ratio = v.iloc[-1] / avgvol.iloc[-1] if avgvol.iloc[-1] else 0
        if ratio < vol_ratio: continue
        slope = ma60.iloc[-1] / ma60.iloc[-6] - 1 if ma60.iloc[-6] else 0
        if slope <= 0: continue
        # Heuristic ranking only, NOT probability of positive next-day returns.
        momentum = max(0, min(35, (p.iloc[-1]/ma60.iloc[-1]-1)*250))
        volume_score = min(30, ratio*10)
        breakout = min(25, max(0, (p.iloc[-1]/high20.iloc[-1]-1)*1000))
        slope_score = min(10, slope*300)
        score = round(momentum+volume_score+breakout+slope_score,1)
        candidates.append(dict(date=last['date'], market=market, code=code,
            name=last['name'], close=round(p.iloc[-1],2), score=score,
            volume_ratio=round(ratio,2), ma20=round(ma20.iloc[-1],2),
            ma60=round(ma60.iloc[-1],2), breakout_ref=round(high20.iloc[-1],2),
            avg_turnover=round(avg_amount), history_days=len(g)))
    return pd.DataFrame(candidates).sort_values('score',ascending=False).reset_index(drop=True) if candidates else pd.DataFrame()


def import_csv(conn, raw):
    """Import user-supplied verified OHLCV snapshots; columns date,market,code,name,close,volume,amount."""
    frame = pd.read_csv(io.BytesIO(raw), dtype={'code':str,'date':str,'market':str})
    required = ['date','market','code','name','close','volume','amount']
    if not set(required).issubset(frame.columns): raise ValueError('CSV 欄位必須包含：'+', '.join(required))
    if not frame['date'].astype(str).str.fullmatch(r'\d{4}-\d{2}-\d{2}').all(): raise ValueError('date 必須為 YYYY-MM-DD')
    if not frame['market'].isin(['TWSE','TPEX']).all(): raise ValueError('market 僅接受 TWSE 或 TPEX')
    if not frame['code'].astype(str).str.fullmatch(r'\d{4}').all(): raise ValueError('code 必須為四碼股票代號')
    rows=[]
    for row in frame[required].itertuples(index=False,name=None):
        d,m,c,n,p,v,a=row
        if not (pd.notna(p) and pd.notna(v) and pd.notna(a) and float(p)>0 and float(v)>0 and float(a)>=0): continue
        rows.append((str(d),str(m),str(c),str(n),float(p),float(v),float(a)))
    return store(conn,rows)
