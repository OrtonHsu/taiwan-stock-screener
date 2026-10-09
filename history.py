"""Date-verifiable official TWSE monthly history for a bounded stock watchlist.
Does NOT claim to backfill the whole market or TPEx automatically.
"""
import argparse
import calendar
import os
import re
import time
from datetime import date
import requests
from screener import num
from storage import Store

URL='https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; TaiwanStockScreenerResearch/1.1)','Accept':'application/json'}

def parse_month(payload,code):
    if payload.get('stat')!='OK':
        raise ValueError('TWSE history status: '+str(payload.get('stat')))
    fields=payload.get('fields',[])
    needed=['日期','成交股數','成交金額','收盤價']
    if not all(x in fields for x in needed): raise ValueError('TWSE 欄位異動: '+repr(fields))
    idx={k:fields.index(k) for k in needed}
    out=[]
    for r in payload.get('data',[]):
        raw=str(r[idx['日期']]).strip()
        match=re.fullmatch(r'(\d{2,3})/(\d{1,2})/(\d{1,2})',raw)
        if not match: continue
        y,m,d=map(int,match.groups())
        try: day=date(y+1911,m,d).isoformat()
        except ValueError: continue
        close=num(r[idx['收盤價']]); vol=num(r[idx['成交股數']]); amount=num(r[idx['成交金額']])
        if close is None or vol is None or amount is None or close<=0 or vol<=0 or amount<0: continue
        out.append((day,'TWSE',code,code,close,vol,amount))
    return out

def month_starts(months=8, today=None):
    today=today or date.today()
    y,m=today.year,today.month
    for _ in range(months):
        yield date(y,m,1)
        m-=1
        if m==0: m=12;y-=1

def run(codes,months=8,delay=2.0,store=None):
    if months<1 or months>24: raise ValueError('months 必須在1到24之間')
    codes=list(dict.fromkeys(c.strip() for c in codes if c.strip()))
    if not codes or len(codes)>30 or any(not re.fullmatch(r'\d{4}',x) for x in codes):
        raise ValueError('僅接受1至30個四碼上市股票代號，避免過量請求')
    db=store or Store()
    success=0; failures=[]
    with requests.Session() as s:
        for code in codes:
            for month in month_starts(months):
                try:
                    resp=s.get(URL,params={'date':month.strftime('%Y%m%d'),'stockNo':code,'response':'json'},headers=HEADERS,timeout=30)
                    resp.raise_for_status()
                    rows=parse_month(resp.json(),code)
                    if not rows: raise ValueError('沒有有效歷史資料')
                    if any(x[0][:7]!=month.strftime('%Y-%m') for x in rows):
                        raise ValueError('官方回傳日期不屬於要求月份，拒絕寫入')
                    success+=db.store(rows)
                    print(f'{code} {month:%Y-%m}: {len(rows)} records')
                except Exception as e:
                    failures.append(f'{code} {month:%Y-%m}: {e}')
                time.sleep(max(1.5,delay))
    print('written=',success,'failed_requests=',len(failures))
    for f in failures: print('WARNING',f)
    if not success: raise RuntimeError('完全沒有取得歷史行情；請檢查官方端點及連線')
    return success,failures

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--codes',default=os.getenv('TWSE_CODES','2330,2317,2454,2303,2882'))
    p.add_argument('--months',type=int,default=8)
    args=p.parse_args()
    run(args.codes.split(','),args.months)
