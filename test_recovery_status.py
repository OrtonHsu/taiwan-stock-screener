from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import auto_backfill
import market_backfill
import twse_mar_jun_auto as recovery
from twse_recovery_status import build_status, report_status

NOW = datetime(2026, 11, 19, 1, tzinfo=timezone.utc)
MONTHS = (date(2026, 3, 1),)


@pytest.mark.parametrize("month,expected", [
    (date(2026, 3, 1), True), (date(2026, 8, 1), True),
    (date(2026, 9, 1), True), (date(2026, 10, 1), False),
    (date(2026, 11, 1), False),
])
def test_verified_listing_only_skips_prior_months(month, expected):
    assert market_backfill.verified_before_listing("7867", month) is expected


def test_unknown_listing_is_never_assumed_to_have_no_data():
    assert not market_backfill.verified_before_listing("2330", MONTHS[0])


def test_status_partitions_pending_and_does_not_double_count_candidate_codes():
    progress = {("1101", "2026-03"): 22, ("1102", "2026-03"): 22}
    counts = {("1101", "2026-03"): 22, ("1102", "2026-03"): 21}
    failures = {("1103", "2026-03"): (1, NOW - timedelta(hours=1)),
                ("1104", "2026-03"): (3, NOW - timedelta(days=2))}
    status = build_status(["1101", "1101", "1102", "1103", "1104", "7867"],
                          MONTHS, progress, counts, failures, NOW)
    assert status["candidate_codes"] == 5
    assert status["states"] == dict(completed=1, pre_listing=1, due=1,
                                    cooldown=1, manual_review=1)
    assert sum(status["states"].values()) == status["total_code_months"]
    assert status["unresolved_months"] == 3
    assert status["finished_codes"] == 2
    assert status["due_by_code"] == {"1102": ["2026-03"]}
    assert len(status["marker_mismatches"]) == 1
    assert not status["recovery_counts_complete"]


@pytest.mark.parametrize("age,due", [(23.99, False), (24, True), (25, True)])
def test_cooldown_boundary(age, due):
    status = build_status(["1101"], MONTHS, {}, {},
                          {("1101", "2026-03"): (1, NOW - timedelta(hours=age))}, NOW)
    assert bool(status["due_by_code"]) is due


def test_success_clears_old_failure_from_pending_counts():
    key = ("1101", "2026-03")
    status = build_status(["1101"], MONTHS, {key: 22}, {key: 22},
                          {key: (3, NOW)}, NOW)
    assert status["states"]["completed"] == 1
    assert status["recovery_counts_complete"]


def test_zero_marker_or_prices_without_marker_remain_due():
    status = build_status(["1101", "1102"], MONTHS,
                          {("1101", "2026-03"): 0}, {("1102", "2026-03"): 22}, {}, NOW)
    assert status["states"]["due"] == 2
    assert not status["recovery_counts_complete"]


def test_prelisting_quotes_require_manual_review():
    status = build_status(["7867"], MONTHS, {}, {("7867", "2026-03"): 1}, {}, NOW)
    assert status["states"]["manual_review"] == 1
    assert not status["due_by_code"]


def test_empty_universe_is_not_complete():
    assert not build_status([], MONTHS, {}, {}, {}, NOW)["recovery_counts_complete"]


@pytest.mark.parametrize("expected,actual,code,complete", [
    (22, 22, "1101", True), (22, 21, "1101", False),
    (0, 0, "1101", False), (0, 0, "7867", True),
    (-1, 0, "1101", False),
])
def test_legacy_completion_checks_stored_prices(expected, actual, code, complete):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [(expected,), (actual,)]
    assert market_backfill.is_completed(conn, code, "2026-08") is complete


def test_legacy_selector_uses_taiwan_month_and_rejects_missing_prices(monkeypatch):
    monkeypatch.setattr(auto_backfill, "taiwan_today", lambda: date(2026, 11, 1))
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [
        ("1101", "2026-10", 19, 19),
        ("1102", "2026-10", 19, 18),
        ("1103", "2026-10", 0, 0),
    ]
    assert auto_backfill.get_completed_codes(conn, 2) == {"1101"}
    assert cursor.execute.call_args.args[1] == (["2026-10"], ["2026-10"])


def test_summary_keeps_single_batch_success_separate_from_full_completion(tmp_path, monkeypatch):
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    status = build_status(["1101"], MONTHS, {}, {}, {}, NOW)
    report_status(status, "DRY_RUN", checked=1)
    text = path.read_text()
    assert "DRY_RUN" in text
    assert "整體回補尚未完成" in text
    assert "不會寫入資料庫" in text


def setup_main(monkeypatch, args):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused-test-database")
    monkeypatch.setattr("sys.argv", ["twse_mar_jun_auto.py", *args])
    monkeypatch.setattr(recovery, "get_codes_with_retries", lambda: ["1101"])
    monkeypatch.setattr(recovery, "MONTHS", MONTHS)
    monkeypatch.setattr(recovery, "MONTH_KEYS", ["2026-03"])
    monkeypatch.setattr(recovery, "read_state", lambda *args: ({}, {}, {}))
    monkeypatch.setattr(recovery.time, "sleep", lambda *_: None)
    conn = MagicMock()
    monkeypatch.setattr(recovery.psycopg, "connect", lambda *a, **kw: conn)
    return conn.__enter__.return_value


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected database write or monthly network request")


def test_status_only_cannot_download_or_write(monkeypatch):
    conn = setup_main(monkeypatch, ["--status-only"])
    monkeypatch.setattr(recovery, "ensure_failure_table", forbidden)
    monkeypatch.setattr(recovery, "fetch_month", forbidden)
    monkeypatch.setattr(recovery, "write_month", forbidden)
    recovery.main()
    assert conn.read_only is True


def test_dry_run_cannot_write(monkeypatch):
    conn = setup_main(monkeypatch, [])
    monkeypatch.setattr(recovery, "ensure_failure_table", forbidden)
    monkeypatch.setattr(recovery, "fetch_month", lambda *a: {})
    monkeypatch.setattr(recovery, "parse_month", lambda *a: [()] * 22)
    monkeypatch.setattr(recovery, "write_month", forbidden)
    monkeypatch.setattr(recovery, "clear_failure", forbidden)
    recovery.main()
    assert conn.read_only is True


def test_all_failed_batch_is_not_green(monkeypatch):
    setup_main(monkeypatch, [])
    monkeypatch.setattr(recovery, "fetch_month", lambda *a: {})
    monkeypatch.setattr(recovery, "parse_month", lambda *a: [])
    monkeypatch.setattr(recovery, "record_failure", forbidden)
    with pytest.raises(RuntimeError, match="Every selected month failed"):
        recovery.main()


def test_status_only_write_conflict_rejected_before_connect(monkeypatch):
    monkeypatch.setattr("sys.argv", ["script", "--status-only", "--write"])
    monkeypatch.setattr(recovery.psycopg, "connect", forbidden)
    with pytest.raises(SystemExit):
        recovery.main()
