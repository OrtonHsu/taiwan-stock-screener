"""V2.3 experimental market breadth from verified OFFLINE 2026H1 JSON samples.

Only research. Four-digit, non-00 numeric codes are provisional candidates,
NOT certified ordinary shares. Quotes must have positive close/volume and
nonnegative traded amount, on both immediately adjacent official trade days.
Never forward-fill, never infer a missing close from average/next reference.
No network, database, or GitHub write. Output is a new local CSV.

Example:
  python v23_provisional_breadth.py --start 2026-01-01 --end 2026-06-30
"""
from __future__ import annotations
import argparse
import csv
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from v23_resume_batches import select_calendar
from v23_research_batch import load_or_get, positive_number
from v23_tpex_quote_audit import select_quotes, parse_number

MARKETS = ("TWSE", "TPEX")
TWSE_REQUIRED = ("證券代號", "成交股數", "成交金額", "收盤價")


def _is_candidate(code: str) -> bool:
    """Conservative pattern filter, not a substitute for official security type."""
    return bool(re.fullmatch(r"[0-9]{4}", code)) and not code.startswith("00")


def _valid_price(close, volume, amount) -> bool:
    return (close is not None and close > 0 and
            volume is not None and volume > 0 and
            amount is not None and amount >= 0)


def twse_table(payload: dict) -> tuple[list, dict[str, int]]:
    candidates=[]
    for table in payload.get("tables", []):
        if not isinstance(table, dict) or not isinstance(table.get("data"), list):
            continue
        fields=table.get("fields", [])
        if all(field in fields for field in TWSE_REQUIRED) and table["data"]:
            candidates.append((table["data"], {f:fields.index(f) for f in TWSE_REQUIRED}))
    if len(candidates) != 1:
        raise ValueError(f"TWSE quote table not unique: {len(candidates)}")
    return candidates[0]


def eligible_quotes(market: str, day: str, payload: dict) -> tuple[dict[str, Decimal], dict[str,int]]:
    if market == "TWSE":
        rows, index = twse_table(payload)
        c, v, a, p = (index[k] for k in TWSE_REQUIRED)
        parse = positive_number
    elif market == "TPEX":
        rows, index = select_quotes(payload, day)
        c, v, a, p = (index[k] for k in ("code", "volume", "amount", "close"))
        parse = parse_number
    else:
        raise ValueError("Unknown market")
    close_by_code={}
    seen=set()
    total=excluded00=bad_price=0
    for row in rows:
        if not isinstance(row, list) or len(row) <= max(c,v,a,p):
            raise ValueError(f"Malformed {market} quote row")
        code=str(row[c]).strip()
        if not re.fullmatch(r"[0-9]{4}", code):
            continue
        if code in seen:
            raise ValueError(f"Duplicate code in official source: {day} {market} {code}")
        seen.add(code)
        if code.startswith("00"):
            excluded00+=1
            continue
        total+=1
        close, volume, amount = parse(row[p]), parse(row[v]), parse(row[a])
        if _valid_price(close,volume,amount):
            close_by_code[code]=close
        else:
            bad_price+=1
    return close_by_code, {
        "candidate_codes":total,
        "eligible_codes":len(close_by_code),
        "invalid_or_unpriced":bad_price,
        "excluded_00":excluded00,
    }


def advance_decline(previous: dict[str, Decimal] | None,
                    current: dict[str, Decimal]) -> dict[str, int | str]:
    if previous is None:
        return {"advancing":0,"declining":0,"unchanged":0,
                "comparable":0,"new_or_uncomparable":len(current),
                "breadth_ratio":""}
    same=set(previous) & set(current)
    advances=sum(current[c]>previous[c] for c in same)
    declines=sum(current[c]<previous[c] for c in same)
    unchanged=len(same)-advances-declines
    ratio=f"{advances/len(same):.6f}" if same else ""
    return {"advancing":advances,"declining":declines,"unchanged":unchanged,
            "comparable":len(same),"new_or_uncomparable":len(current)-len(same),
            "breadth_ratio":ratio}


def calculate(calendar: str, folder: str, start: str, end: str) -> list[dict]:
    days=select_calendar(calendar,start,end)
    prior={market:None for market in MARKETS}
    out=[]
    for day in days:
        for market in MARKETS:
            payload,evidence,_=load_or_get(Path(folder),market,day,fetch=False)
            if evidence.get("status") != (
                "schema_date_verified" if market=="TWSE" else "metadata_schema_verified_only"
            ):
                raise ValueError(f"Unverified sample: {market} {day}")
            valid,counts=eligible_quotes(market,day,payload)
            metric=advance_decline(prior[market],valid)
            out.append({"trade_date":day,"market":market,**counts,**metric,
                        "security_classification":"UNVERIFIED",
                        "market_breadth":"PROVISIONAL_RESEARCH_ONLY",
                        "can_gate_entries":"False"})
            prior[market]=valid
    return out


def main(argv=None):
    p=argparse.ArgumentParser(description="Read-only V2.3 provisional breadth calculation")
    p.add_argument("--calendar",default="v23_trading_calendar_2021_2026.csv")
    p.add_argument("--sample-dir",default="v23_research_samples")
    p.add_argument("--start",default="2026-01-01")
    p.add_argument("--end",default="2026-06-30")
    p.add_argument("--output",default="v23_research_samples/v23_provisional_breadth_2026H1.csv")
    args=p.parse_args(argv)
    records=calculate(args.calendar,args.sample_dir,args.start,args.end)
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",newline="",encoding="utf-8-sig") as fp:
        writer=csv.DictWriter(fp,fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    stats=Counter(row["market"] for row in records)
    print("OFFLINE_ONLY: True")
    print("TRADING_DAYS:",len(records)//len(MARKETS))
    print("TWSE_DAYS:",stats["TWSE"])
    print("TPEX_DAYS:",stats["TPEX"])
    print("UNVERIFIED_SECURITY_TYPE: True")
    print("ENTRY_GATING_ENABLED: False")
    print("TOTAL_MARKET_DATE_ROWS:",len(records))
    print("OUTPUT:",target)
    print("DATABASE_WRITES: 0")


if __name__=="__main__":
    main()
