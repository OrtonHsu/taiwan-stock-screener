"""V2.1 paper tracker safety, idempotence and entry/exit tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from paper_tracker_core import (
    COOLDOWN_SESSIONS, MAX_NEW_SIGNALS, VERSION, advance_position,
    net_return_pct, select_new_signals,
)
from v21_paper_tracker import (
    asof_close_and_ma20, existing_recent_exits, map_closes,
)


def pending():
    return {
        "id": 1, "strategy_version": VERSION, "market": "TWSE", "code": "2330",
        "name": "測試股票", "signal_date": "2026-11-18", "signal_close": 100.0,
        "status": "pending_entry", "entry_date": None, "entry_close": None,
        "exit_date": None, "exit_close": None, "exit_reason": None,
        "below_ma20_streak": 0, "last_observed_date": "2026-11-18",
        "last_observed_close": 100.0, "net_return_pct": None,
    }


def test_signal_not_filled_at_same_closing_price():
    p, obs = advance_position(pending(), "2026-11-18", 100, 98)
    assert obs is None
    assert p["status"] == "pending_entry"


def test_next_session_close_is_only_a_paper_proxy():
    p, obs = advance_position(pending(), "2026-11-19", 104, 99)
    assert p["status"] == "open"
    assert p["entry_date"] == "2026-11-19"
    assert p["entry_close"] == 104
    assert obs["state"] == "open"
    assert "代理" in obs["note"]
    assert obs["unrealized_net_pct"] < 0
    same, repeat = advance_position(p, "2026-11-19", 104, 99)
    assert repeat is None and same == p


def test_missing_t1_quote_cannot_be_faked():
    p, obs = advance_position(pending(), "2026-11-19", None, None)
    assert p["status"] == "skipped"
    assert p["entry_close"] is None
    assert obs["unrealized_net_pct"] is None
    assert obs["close"] is None


def test_two_close_below_ma20_schedules_next_session_exit():
    p, _ = advance_position(pending(), "2026-11-19", 105, 100)
    p, first = advance_position(p, "2026-11-20", 98, 99, 1)
    assert p["status"] == "open"
    assert p["below_ma20_streak"] == 1
    p, second = advance_position(p, "2026-11-23", 97, 99, 2)
    assert p["status"] == "pending_exit"
    assert p["exit_close"] is None
    assert second["state"] == "pending_exit"
    p, exit_day = advance_position(p, "2026-11-24", 95, 99, 3)
    assert p["status"] == "closed"
    assert p["exit_date"] == "2026-11-24"
    assert p["exit_close"] == 95
    assert p["net_return_pct"] == net_return_pct(105, 95)
    assert p["net_return_pct"] < (95 / 105 - 1) * 100
    assert exit_day["state"] == "closed"


def test_missing_quote_during_exit_keeps_exposure_open():
    p, _ = advance_position(pending(), "2026-11-19", 105, 100)
    p, _ = advance_position(p, "2026-11-20", 98, 99, 1)
    p, _ = advance_position(p, "2026-11-23", 97, 99, 2)
    p, obs = advance_position(p, "2026-11-24", None, None, 3)
    assert p["status"] == "pending_exit"
    assert p["exit_date"] is None
    assert obs["close"] is None
    p, obs = advance_position(p, "2026-11-25", 93, 95, 4)
    assert p["status"] == "closed" and p["exit_close"] == 93


def test_data_gap_resets_confirmation_not_invents_two_red_closes():
    p, _ = advance_position(pending(), "2026-11-19", 105, 100)
    p, _ = advance_position(p, "2026-11-20", 98, 100, 1)
    p, gap = advance_position(p, "2026-11-23", None, None, 2)
    assert p["below_ma20_streak"] == 0
    assert gap["unrealized_net_pct"] is None
    p, _ = advance_position(p, "2026-11-24", 97, 100, 3)
    assert p["status"] == "open" and p["below_ma20_streak"] == 1


def test_cost_model_is_not_gross_profit_and_rejects_invalid_prices():
    assert net_return_pct(100, 100) < 0
    assert net_return_pct(100, 105) < 5
    with pytest.raises(ValueError):
        net_return_pct(0, 100)


def candidate(code="2330", date="2026-11-19", decision="趨勢進場研究候選"):
    return {
        "date": date, "market": "TWSE", "code": code,
        "name": "測試", "decision": decision,
    }


def test_active_position_is_not_opened_again():
    rows = pd.DataFrame([candidate("2330"), candidate("2317")])
    picks = select_new_signals(rows, {("TWSE","2330")}, {}, ["2026-11-19"])
    assert [p.code for p in picks] == ["2317"]


def test_new_signal_capacity_and_skip_non_candidates():
    rows = pd.DataFrame([candidate(str(2000+i)) for i in range(9)] + [
        candidate("9000", decision="等待確認")
    ])
    picks = select_new_signals(rows, set(), {}, ["2026-11-19"])
    assert len(picks) == MAX_NEW_SIGNALS
    assert len(select_new_signals(rows, {("TWSE",str(i)) for i in range(25)}, {},
                                  ["2026-11-19"])) == 0
    with pytest.raises(ValueError):
        select_new_signals(rows, set(), {}, ["2026-11-19"], max_new=-1)


def test_recent_exit_cooldown_requires_5_complete_sessions():
    days = pd.bdate_range("2026-11-12", periods=9).strftime("%Y-%m-%d").tolist()
    exit_day = days[-4]
    row = pd.DataFrame([candidate()])
    assert select_new_signals(
        row, set(), {("TWSE", "2330"): exit_day}, days
    ) == []
    old_exit = days[0]
    assert len(select_new_signals(
        row, set(), {("TWSE", "2330"): old_exit}, days
    )) == 1
    assert COOLDOWN_SESSIONS == 5


def test_close_reference_needs_20_consecutive_complete_market_days():
    days = pd.bdate_range("2026-09-01", periods=25).strftime("%Y-%m-%d").tolist()
    history = {d: float(i+100) for i, d in enumerate(days)}
    close, ma = asof_close_and_ma20(history, days[-1], days)
    assert close == 124
    assert ma == pytest.approx(sum(history[d] for d in days[-20:]) / 20)
    bad = history.copy()
    del bad[days[-7]]
    close, ma = asof_close_and_ma20(bad, days[-1], days)
    assert close == 124 and ma is None


def test_live_updater_is_read_only_by_default():
    from pathlib import Path
    code = Path(__file__).with_name("v21_paper_tracker.py").read_text("utf-8")
    assert 'mode.add_argument("--dry-run"' in code
    assert "github.event.workflow_run.conclusion" in Path(
        __file__
    ).parent.joinpath(".github/workflows/v21_paper_tracking.yml").read_text("utf-8")
    assert "ON CONFLICT DO NOTHING" in code
    assert "pg_advisory_xact_lock" in code
    assert "latest_due_market_date" in code


def test_no_real_trade_or_unrelated_database_write_in_engine():
    from pathlib import Path
    code = Path(__file__).with_name("v21_paper_tracker.py").read_text("utf-8")
    assert "stock_research.simulation_positions" in code
    assert "public.prices" in code
    assert "INSERT INTO public.prices" not in code
    assert "stock_automation.dispatch_twse_v2" not in code


def test_schema_has_private_rls_and_unique_open_position():
    from pathlib import Path
    sql = Path(__file__).with_name("sql").joinpath("v21_paper_tracking.sql").read_text("utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON SCHEMA stock_research" in sql
    assert "sim_one_active_per_stock" in sql
