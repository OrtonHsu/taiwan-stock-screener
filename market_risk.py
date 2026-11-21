"""V2.3 market-risk research engine. Pure functions; no database access or trading.

Inputs are historical daily stock rows with date, market, code, close, volume.
A daily breadth observation requires BOTH markets' coverage. All thresholds
are provisional research hypotheses, NOT validated trading rules.
"""
from __future__ import annotations

import pandas as pd

MIN_CODES = {"TWSE": 900, "TPEX": 700}
MIN_COMPARABLE_RATIO = 0.90


def build_market_breadth(prices: pd.DataFrame) -> pd.DataFrame:
    """Build daily breadth using only adjacent complete sessions.

    No look-ahead: each day's result uses current and prior observed closes.
    A missing session or inadequate comparable coverage yields unknown metrics.
    """
    required = {"date", "market", "code", "close", "volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    columns = ["date", "twse_codes", "tpex_codes", "comparable", "advancers",
               "decliners", "unchanged", "advance_ratio", "volume_ratio",
               "data_status"]
    if prices.empty:
        return pd.DataFrame(columns=columns)
    frame = prices.loc[prices["market"].isin(MIN_CODES)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["date", "market", "code", "close", "volume"])
    frame = frame[(frame["close"] > 0) & (frame["volume"] >= 0)]
    if frame.duplicated(["date", "market", "code"]).any():
        raise ValueError("Duplicate date/market/code records")
    if frame.empty:
        return pd.DataFrame(columns=columns)
    counts = frame.groupby(["date", "market"])["code"].nunique().unstack(fill_value=0)
    for market in MIN_CODES:
        if market not in counts:
            counts[market] = 0
    dates = counts.index.sort_values()
    results = []
    previous = None
    volumes = []
    for date in dates:
        day = frame.loc[frame["date"].eq(date)].set_index(["market", "code"])
        twse, tpex = (int(counts.loc[date, market]) for market in MIN_CODES)
        complete = twse >= MIN_CODES["TWSE"] and tpex >= MIN_CODES["TPEX"]
        row = dict(date=date.date().isoformat(), twse_codes=twse, tpex_codes=tpex,
                   comparable=0, advancers=0, decliners=0, unchanged=0,
                   advance_ratio=None, volume_ratio=None, data_status="insufficient")
        if complete and previous is not None:
            joined = day[["close"]].join(previous[["close"]], how="inner", lsuffix="_now", rsuffix="_prev")
            comparable = len(joined)
            expected = len(day)
            row["comparable"] = comparable
            if comparable >= expected * MIN_COMPARABLE_RATIO:
                up = int((joined.close_now > joined.close_prev).sum())
                down = int((joined.close_now < joined.close_prev).sum())
                flat = comparable - up - down
                row.update(advancers=up, decliners=down, unchanged=flat,
                           advance_ratio=up / (up + down) if up + down else None,
                           data_status="ok")
                if len(volumes) >= 20:
                    mean_vol = sum(volumes[-20:]) / 20
                    row["volume_ratio"] = float(day.volume.sum()) / mean_vol if mean_vol > 0 else None
        results.append(row)
        # An incomplete day breaks continuity; do not silently bridge it.
        previous = day if complete else None
        if complete:
            volumes.append(float(day.volume.sum()))
        else:
            volumes.clear()
    return pd.DataFrame(results, columns=columns)


def assess_risk(breadth_row: dict) -> dict:
    """Provisional breadth-only risk; unavailable factors stay explicitly unknown.

    Not a full 0-100 composite: avoid falsely treating missing foreign flows,
    index OHLC, or market-wide turnover data as zero risk.
    """
    if breadth_row.get("data_status") != "ok":
        return {"level": "unknown", "breadth_score": None,
                "reason": "行情或前日可比較樣本不足", "can_gate_entries": False}
    ratio = breadth_row.get("advance_ratio")
    if ratio is None or pd.isna(ratio):
        return {"level": "unknown", "breadth_score": None,
                "reason": "無有效漲跌比較", "can_gate_entries": False}
    if ratio < 0.25:
        score, level = 30, "breadth_stress"
    elif ratio < 0.40:
        score, level = 20, "breadth_caution"
    else:
        score, level = 0, "breadth_normal"
    return {"level": level, "breadth_score": score,
            "reason": f"上漲占漲跌家數比例 {ratio:.1%}",
            "can_gate_entries": False}
