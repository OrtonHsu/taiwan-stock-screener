"""Research-only TWSE/TPEx sample fetcher.

Default is DRY RUN; --fetch sends exactly ONE HTTP GET.
Writes raw official JSON plus checksum manifest to local research folder.
No Supabase, no production DB, no GitHub writes.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen

MAX_BYTES=10_000_000
URLS={
    "TWSE":("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",{"type":"ALLBUT0999","response":"json"}),
    "TPEX":("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",{"response":"json"}),
}

def request_url(market,day):
    d=date.fromisoformat(day)
    if market not in URLS: raise ValueError("Unsupported market")
    base,fixed=URLS[market]
    params=dict(fixed)
    params["date"]=d.strftime("%Y%m%d") if market=="TWSE" else f"{d.year-1911:03d}/{d.month:02d}/{d.day:02d}"
    return base+"?"+urlencode(params)

def validate_day(day,calendar):
    with open(calendar,newline="",encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        if not reader.fieldnames or "trade_date" not in reader.fieldnames:
            raise ValueError("Calendar must have trade_date column")
        official_days={row["trade_date"] for row in reader}
    if day not in official_days:
        raise ValueError("Date not in supplied official TWSE calendar")

def evidence(market,day,payload):
    if market not in URLS: raise ValueError("Unsupported market")
    if not isinstance(payload,dict) or str(payload.get("stat","")).lower()!="ok":
        raise ValueError("Official response status not OK")
    d=date.fromisoformat(day)
    tables=payload.get("tables")
    if not isinstance(tables,list):
        raise ValueError("No official tables array")
    tables=[t for t in tables if isinstance(t,dict) and isinstance(t.get("data"),list)]
    if not tables: raise ValueError("No data tables")
    if market=="TWSE":
        def title_day(title):
            match=re.search(r"(\d{2,3})年\s*(\d{1,2})月\s*(\d{1,2})日",str(title))
            return date(int(match[1])+1911,int(match[2]),int(match[3])) if match else None
        required={"證券代號","證券名稱","成交股數","成交金額","收盤價"}
        matches=[t for t in tables if title_day(t.get("title"))==d
                 and required.issubset(set(t.get("fields",[]))) and t["data"]]
        if len(matches)!=1:
            raise ValueError("TWSE quote table not uniquely dated/schema-verified")
        return {"status":"schema_date_verified","trade_date":day,
                "table_count":len(tables),"raw_table_rows":len(matches[0]["data"]),
                "security_types_filtered":False,"quote_values_verified":False}
    roc=f"{d.year-1911:03d}/{d.month:02d}/{d.day:02d}"
    if str(payload.get("date","")).strip() not in (roc,day,day.replace("-","")):
        raise ValueError("TPEx date metadata does not match requested day")
    matches=[t for t in tables if t["data"]
             and any("代號" in str(f) for f in t.get("fields",[]))
             and any("收盤" in str(f) for f in t.get("fields",[]))]
    if not matches: raise ValueError("No TPEx quote fields")
    # TPEx response may cover more than one date: do not claim per-day coverage.
    return {"status":"metadata_schema_verified_only","trade_date":day,
            "table_count":len(tables),"raw_table_rows":sum(len(t["data"]) for t in matches),
            "daily_row_granularity_verified":False,"quote_values_verified":False}

def fetch_one(url,*,opener=urlopen):
    req=Request(url,headers={"User-Agent":"TaiwanStockScreener-V23-Research/1.0",
                             "Accept":"application/json"})
    with opener(req,timeout=25) as response:
        raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES: raise ValueError("Official response too large")
        if "json" not in response.headers.get("Content-Type","").lower():
            raise ValueError("Official response is not JSON")
    return raw

def save_sample(outdir,market,day,raw,info,url):
    folder=Path(outdir)
    stem=f"{market}_{day.replace('-','')}"
    original=folder/(stem+".json")
    manifest=folder/(stem+".manifest.json")
    if original.exists() or manifest.exists():
        raise FileExistsError(f"Sample already exists: {stem}")
    folder.mkdir(parents=True,exist_ok=True)
    original.write_bytes(raw)
    meta={"source_url":url,"sha256":hashlib.sha256(raw).hexdigest(),**info}
    manifest.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    return original,manifest

def main(argv=None):
    p=argparse.ArgumentParser(description="V2.3 one-day read-only official research sampler")
    p.add_argument("--calendar",default="v23_trading_calendar_2021_2026.csv")
    p.add_argument("--day",required=True)
    p.add_argument("--market",required=True,choices=sorted(URLS))
    p.add_argument("--output",default="v23_research_samples")
    p.add_argument("--fetch",action="store_true",help="Send ONE official HTTP GET")
    p.add_argument("--windows-trust",action="store_true",help="Use Windows trust store; TLS verification stays enabled")
    args=p.parse_args(argv)
    validate_day(args.day,args.calendar)
    url=request_url(args.market,args.day)
    if not args.fetch:
        print("DRY_RUN_ONLY",url)
        return
    if args.windows_trust:
        import truststore
        truststore.inject_into_ssl()
    raw=fetch_one(url)
    try:
        payload=json.loads(raw)
    except (ValueError,UnicodeDecodeError) as exc:
        raise ValueError("Official response is not valid JSON") from exc
    info=evidence(args.market,args.day,payload)
    original,manifest=save_sample(args.output,args.market,args.day,raw,info,url)
    print("OFFICIAL_SAMPLE_ONLY: no DB writes")
    print(json.dumps(info,ensure_ascii=False))
    print("RAW:",original)
    print("MANIFEST:",manifest)

if __name__=="__main__":
    main()
