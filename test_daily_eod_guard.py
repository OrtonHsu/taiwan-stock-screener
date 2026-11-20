"""Daily EOD quote fallback regression and safety tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from daily_eod_guard import auto_event_allowed, coverage_complete, run_guard

TW = ZoneInfo("Asia/Taipei")


def stamp(value: str):
    return datetime.fromisoformat(value).replace(tzinfo=TW)


def test_fallback_only_during_taiwan_weekday_evening():
    assert not auto_event_allowed("workflow_run", stamp("2026-11-20T16:59:00"))
    assert auto_event_allowed("workflow_run", stamp("2026-11-20T17:00:00"))
    assert auto_event_allowed("workflow_run", stamp("2026-11-20T23:59:00"))
    assert not auto_event_allowed("workflow_run", stamp("2026-11-21T00:01:00"))
    assert not auto_event_allowed("push", stamp("2026-11-22T21:00:00"))
    assert auto_event_allowed("schedule", stamp("2026-11-21T04:00:00"))
    assert auto_event_allowed("workflow_dispatch", stamp("2026-11-21T04:00:00"))
    assert not auto_event_allowed("untrusted_unknown", stamp("2026-11-20T22:00:00"))


def test_coverage_requires_both_whole_markets():
    assert not coverage_complete({"TWSE": 5, "TPEX": 2})
    assert not coverage_complete({"TWSE": 1050, "TPEX": 0})
    assert not coverage_complete({"TWSE": 899, "TPEX": 850})
    assert not coverage_complete({"TWSE": 1050, "TPEX": 699})
    assert coverage_complete({"TWSE": 900, "TPEX": 700})


def test_complete_snapshot_skips_official_api_call():
    called = []
    result = run_guard(
        event="workflow_run", now=stamp("2026-11-20T19:00:00"),
        database_url="test-only-url",
        get_counts=lambda d,u: {"TWSE": 1000, "TPEX": 850},
        update=lambda: called.append(True),
    )
    assert result["result"] == "already_complete"
    assert result["date"] == "2026-11-20"
    assert not called


def test_incomplete_snapshot_recovered_and_reverified():
    reads, fetched = [], []
    def coverage(day, url):
        reads.append(day)
        if len(reads) == 1:
            return {"TWSE": 5, "TPEX": 2}
        return {"TWSE": 1100, "TPEX": 880}

    result = run_guard(
        event="push", now=stamp("2026-11-20T23:05:00"),
        database_url="test-only-url",
        get_counts=coverage,
        update=lambda: fetched.append(True),
    )
    assert result["result"] == "recovered"
    assert result["date"] == "2026-11-20"
    assert reads == ["2026-11-20", "2026-11-20"]
    assert len(fetched) == 1


def test_official_fails_or_remains_partial_does_not_claim_recovery():
    def broken():
        raise RuntimeError("官方 API 或寫入失敗")
    with pytest.raises(RuntimeError, match="官方 API"):
        run_guard(
            event="schedule", now=stamp("2026-11-20T20:00:00"),
            database_url="test-only-url",
            get_counts=lambda d,u: {"TWSE": 5, "TPEX": 2},
            update=broken,
        )
    with pytest.raises(RuntimeError, match="仍不完整"):
        run_guard(
            event="schedule", now=stamp("2026-11-20T20:00:00"),
            database_url="test-only-url",
            get_counts=lambda d,u: {"TWSE": 5, "TPEX": 2},
            update=lambda: None,
        )


def test_untrusted_or_off_window_event_has_no_db_or_network_side_effect():
    calls = []
    result = run_guard(
        event="workflow_run", now=stamp("2026-11-21T03:00:00"),
        database_url="test-only-url",
        get_counts=lambda d,u: calls.append("query"),
        update=lambda: calls.append("fetch"),
    )
    assert result["result"] == "skipped" and not calls


def test_calendar_fails_closed_on_unverified_year():
    with pytest.raises(ValueError, match="2027"):
        run_guard(
            event="schedule", now=stamp("2027-01-04T20:00:00"),
            database_url="test-only-url",
            get_counts=lambda d,u: {},
            update=lambda: None,
        )


def test_scheduled_and_recovery_workflow_wakeup_and_lock():
    from pathlib import Path
    yaml = Path(__file__).with_name(".github").joinpath("workflows/update.yml").read_text("utf-8")
    assert 'workflows: ["V2 TWSE Mar-Jun Auto Recovery"]' in yaml
    assert "workflow_run.head_repository.full_name == github.repository" in yaml
    assert "taiwan-stock-daily-verified-snapshot" in yaml
    assert "cancel-in-progress: false" in yaml
    assert "python daily_eod_guard.py" in yaml
    assert "branches: [main]" in yaml
    assert "cron: '30 9 * * 1-5'" in yaml
    assert "cron: '17 11 * * 1-5'" in yaml
    assert "cron: '37 13 * * 1-5'" in yaml
    assert "python history.py" in yaml
    assert "github.event_name == 'workflow_dispatch'" in yaml


def test_tracker_still_triggers_after_successful_daily_snapshot_workflow():
    from pathlib import Path
    yaml = Path(__file__).with_name(".github").joinpath(
        "workflows/v21_paper_tracking.yml"
    ).read_text("utf-8")
    assert 'workflows: ["Update Taiwan Stock Data"]' in yaml
    assert "github.event.workflow_run.conclusion == 'success'" in yaml


def test_updater_target_is_verified_exchange_calendar():
    from pathlib import Path
    daily = Path(__file__).with_name("daily_update.py").read_text("utf-8")
    assert "today = latest_due_market_date(now)" in daily
    assert 'MIN_RECORDS = {"TWSE": 900, "TPEX": 700}' in daily
    assert "parse_twse_backup" in daily
