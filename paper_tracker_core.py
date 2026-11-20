"""Pure, testable transitions for V2.1 simulated positions.

Research only. T+1 close is a backtesting *proxy* not a tradeable fill
guarantee. An exit observed after today's close takes effect at the NEXT
verified market session's close proxy, not the signal close.
"""
from __future__ import annotations

from copy import deepcopy

VERSION = "v2.1.0"
BUY_FEE = 0.001425
SELL_FEE = 0.001425
SELL_TAX = 0.003
MAX_NEW_SIGNALS = 5
MAX_ACTIVE_POSITIONS = 25
COOLDOWN_SESSIONS = 5
EXPOSURE_STATES = frozenset({"pending_entry", "open", "pending_exit"})


def net_return_pct(entry_close, exit_close):
    """Conservative *illustrative* roundtrip costs for ordinary stocks.

    This excludes slippage, market impact, minimum fees and corporate actions.
    """
    if entry_close is None or exit_close is None:
        return None
    entry, exit_ = float(entry_close), float(exit_close)
    if min(entry, exit_) <= 0:
        raise ValueError("進出場價格須為正")
    return round(
        (exit_ * (1 - SELL_FEE - SELL_TAX) / (entry * (1 + BUY_FEE)) - 1) * 100,
        4,
    )


def advance_position(position, day, close, ma20, holding_sessions=0):
    """Advance ONE existing episode by ONE full dual-market session.

    Returns (updated position, immutable daily observation), or (unchanged,
    None) when date is not newer. Missing quotes never fabricate a fill.
    """
    p = deepcopy(position)
    session = str(day)
    if session <= str(p["last_observed_date"] or p["signal_date"]):
        return p, None
    if p["status"] not in EXPOSURE_STATES:
        return p, None

    valid_price = close is not None and float(close) > 0
    valid_ma = ma20 is not None and float(ma20) > 0
    note = ""
    state = p["status"]
    entry = p.get("entry_close")
    net = None

    if state == "pending_entry":
        if not valid_price:
            state = "skipped"
            note = "訊號後首個完整市場日缺少個股收盤價，拒絕模擬成交"
            p["exit_reason"] = "T+1收盤行情缺漏，未進場"
        else:
            state = "open"
            p["entry_date"] = session
            p["entry_close"] = float(close)
            p["below_ma20_streak"] = 0
            note = "T+1收盤價代理進場（不保證真實可成交）"
    elif state == "open":
        if not valid_price:
            p["below_ma20_streak"] = 0
            note = "本日缺少有效個股收盤價；持倉未假設成交，趨勢確認中斷"
        elif not valid_ma:
            p["below_ma20_streak"] = 0
            note = "最近20個完整交易日價位不連續；不判斷均線出場"
        else:
            below = float(close) < float(ma20)
            p["below_ma20_streak"] = min(
                10, (p.get("below_ma20_streak") or 0) + 1
            ) if below else 0
            if p["below_ma20_streak"] >= 2:
                state = "pending_exit"
                note = "連續兩個完整交易日收盤跌破MA20；下一個完整交易日模擬出場"
            elif below:
                note = "首次收盤跌破MA20，持續觀察"
            else:
                note = "趨勢尚未觸發MA20連續兩日失效條件"
    elif state == "pending_exit":
        if valid_price:
            state = "closed"
            p["exit_date"] = session
            p["exit_close"] = float(close)
            p["exit_reason"] = "兩日收盤跌破MA20後，次一完整交易日收盤價代理出場"
            p["net_return_pct"] = net_return_pct(p["entry_close"], close)
            note = p["exit_reason"]
        else:
            note = "預定出場日個股缺價，延後代理出場，不編造成交"

    p["status"] = state
    p["last_observed_date"] = session
    if valid_price:
        p["last_observed_close"] = float(close)
    if p.get("entry_close") is not None and valid_price:
        net = net_return_pct(p["entry_close"], close)

    obs = {
        "position_id": p.get("id"),
        "asof_date": session,
        "close": round(float(close), 4) if valid_price else None,
        "ma20": round(float(ma20), 4) if valid_ma else None,
        "holding_sessions": max(0, int(holding_sessions)) if p.get("entry_date") else 0,
        "below_ma20_streak": p.get("below_ma20_streak", 0),
        "unrealized_net_pct": net,
        "state": state,
        "note": note,
    }
    return p, obs


def select_new_signals(plans, existing_active, recent_exits, dates, *,
                       max_new=MAX_NEW_SIGNALS, max_active=MAX_ACTIVE_POSITIONS):
    """Limit fresh candidates, exclude already-active and exit-cooldown names.

    'recent_exits' is a mapping (market, code) -> latest exit_date.
    Signal date must already be validated by the caller.
    """
    if plans is None or plans.empty:
        return []
    if max_new < 0 or max_active < 0:
        raise ValueError("候選額度不可為負")
    present = set(existing_active)
    room = max(0, max_active - len(present))
    capacity = min(max_new, room)
    if capacity <= 0:
        return []
    selected = []
    for rec in plans.itertuples(index=False):
        if rec.decision != "趨勢進場研究候選":
            continue
        key = (str(rec.market), str(rec.code))
        if key in present:
            continue
        if key in recent_exits:
            exit_day = str(recent_exits[key])
            elapsed = sum(d > exit_day and d <= str(rec.date) for d in dates)
            if elapsed < COOLDOWN_SESSIONS:
                continue
        selected.append(rec)
        present.add(key)
        if len(selected) == capacity:
            break
    return selected
