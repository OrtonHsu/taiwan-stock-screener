"""2026 Taiwan securities market trading-calendar reference (TWSE/TPEX).

Manually verified from the TWSE official annual schedule. The sudden 2026-10-23
closure is separately verified from the exchange announcement and contemporary
coverage. This is an audit reference, NOT a live source of exchange decisions.

Official annual schedule:
https://www.twse.com.tw/holidaySchedule/holidaySchedule?queryYear=115&response=html
2026-10-23 emergency typhoon closure:
https://www.cna.com.tw/news/afe/202610220388.aspx

Fail closed for unsupported years; never assume an unknown weekday is open.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


class UnsupportedCalendarYear(ValueError):
    """No verified trading calendar exists for this year."""


# Includes the year-end / Lunar New Year non-trading *settlement-only* days.
ANNUAL_CLOSED_2026 = frozenset({
    "2026-01-01",
    "2026-02-12", "2026-02-13",
    "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-02-27",
    "2026-04-03", "2026-04-06",
    "2026-05-01", "2026-06-19",
    "2026-09-25", "2026-09-28",
    "2026-10-09", "2026-10-26",
    "2026-12-25",
})
# The sudden closure is NOT in the original annual holiday calendar.
EMERGENCY_CLOSED_2026 = frozenset({"2026-10-23"})
CLOSED_2026 = ANNUAL_CLOSED_2026 | EMERGENCY_CLOSED_2026
SUPPORTED_YEARS = frozenset({2026})


def market_is_open(day):
    """Return True only for dates covered by a verified reference calendar."""
    if not isinstance(day, date) or isinstance(day, datetime):
        day = date.fromisoformat(str(day))
    if day.year not in SUPPORTED_YEARS:
        raise UnsupportedCalendarYear(
            f"{day.year} 尚無已核對的上市／上櫃年度休市日曆；拒絕推測交易日"
        )
    return day.weekday() < 5 and day.isoformat() not in CLOSED_2026


def expected_market_dates(start, end):
    """Inclusive trading dates; unsupported years fail rather than guessing."""
    if not isinstance(start, date):
        start = date.fromisoformat(str(start))
    if not isinstance(end, date):
        end = date.fromisoformat(str(end))
    if start > end:
        raise ValueError("日曆起日不得晚於迄日")
    if any(y not in SUPPORTED_YEARS for y in range(start.year, end.year + 1)):
        raise UnsupportedCalendarYear(
            "日期範圍跨越未驗證年度；請先核對官方年度及臨時休市公告"
        )
    days = []
    day = start
    while day <= end:
        if market_is_open(day):
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def latest_due_market_date(now):
    """Latest session that should have ended by 16:00 Asia/Taipei.

    This does not prove official daily data are published yet.
    """
    if now.tzinfo is None:
        raise ValueError("now 必須具有時區")
    local = now.astimezone(ZoneInfo("Asia/Taipei"))
    day = local.date()
    if local.hour < 16:
        day -= timedelta(days=1)
    for _ in range(370):
        if market_is_open(day):
            return day.isoformat()
        day -= timedelta(days=1)
    raise UnsupportedCalendarYear("找不到已核對的最近交易日")
