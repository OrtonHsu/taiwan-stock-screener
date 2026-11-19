"""Offline tests for 7713 transactional repair gating. No live DB needed."""

import pytest

from tpex_7713_guarded_repair import (
    CONFIRMATION, INSERT_SQL, assess_existing, main,
    matches_official, run_transaction, suspicious_amounts,
)
from tpex_7713_safe_preview import TARGET_DATES


def official_rows(*, anomalous=False):
    result = []
    for day, price, volume in zip(TARGET_DATES, (72.0, 71.6, 71.1), (1000, 4000, 1000)):
        amount = price * volume
        if anomalous and day == TARGET_DATES[0]:
            amount = 81000.0
        result.append((day, "TPEX", "7713", "威力德生醫", price, volume, amount))
    return result


class FakeConn:
    def __init__(self, rows=None, *, conflict_on=None):
        self.rows = list(rows or [])
        self.queries = []
        self.current = []
        self.conflict_on = conflict_on

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.queries.append(query)
        if "pg_advisory_xact_lock" in query:
            self.current = [(1,)]
        elif query.strip().startswith("SELECT date, market, code"):
            self.current = list(self.rows)
        elif query.strip().startswith("INSERT INTO"):
            if params[0] == self.conflict_on:
                self.current = []
            elif any((r[0], r[1], r[2]) == (params[0], params[1], params[2])
                     for r in self.rows):
                self.current = []
            else:
                self.rows.append(params)
                self.current = [(params[0],)]
        else:
            raise AssertionError("Unexpected SQL: " + query)

    def fetchall(self):
        return self.current

    def fetchone(self):
        return self.current[0] if self.current else None


def test_missing_three_produces_plan_without_writing():
    rows, report = assess_existing(official_rows(), [])
    assert len(rows) == 3
    assert report["missing_dates"] == list(TARGET_DATES)
    db = FakeConn()
    outcome = run_transaction(db, official_rows(), write=False)
    assert outcome["mode"] == "DRY_RUN"
    assert outcome["candidate_inserts"] == 3
    assert outcome["inserted"] == 0
    assert outcome["ready_for_insert"] is True
    assert db.rows == []
    assert all("INSERT" not in x for x in db.queries)


def test_existing_identical_row_is_skipped_and_unchanged():
    current = official_rows()[0]
    result, report = assess_existing(official_rows(), [current])
    assert len(result) == 2
    assert report["already_stored_dates"] == [TARGET_DATES[0]]
    assert matches_official(current, official_rows()[0])


def test_existing_quote_mismatch_is_rejected_without_overwrite():
    current = list(official_rows()[0])
    current[4] = 99.0
    with pytest.raises(ValueError, match="拒絕覆蓋"):
        assess_existing(official_rows(), [tuple(current)])


def test_other_market_label_stops_unsafe_insert():
    current = list(official_rows()[0])
    current[1] = "TPEx"
    with pytest.raises(ValueError, match="其他市場別"):
        assess_existing(official_rows(), [tuple(current)])


def test_odd_amount_on_oct27_blocks_write_but_allows_preview():
    official = official_rows(anomalous=True)
    warnings = suspicious_amounts(official)
    assert [x["date"] for x in warnings] == ["2026-10-27"]
    assert warnings[0]["implied_avg"] == 81.0
    db = FakeConn()
    preview = run_transaction(db, official, write=False)
    assert preview["candidate_inserts"] == 3
    assert preview["inserted"] == 0
    assert preview["ready_for_insert"] is False
    with pytest.raises(RuntimeError, match="禁止寫入"):
        run_transaction(db, official, write=True)
    assert not any("INSERT INTO" in x for x in db.queries)


def test_transaction_uses_insert_on_conflict_and_checks_final_state():
    assert "ON CONFLICT (date, market, code) DO NOTHING" in INSERT_SQL
    assert "UPDATE" not in INSERT_SQL
    db = FakeConn()
    report = run_transaction(db, official_rows(), write=True)
    assert report["mode"] == "WRITE"
    assert report["inserted"] == 3
    assert report["after"]["missing_dates"] == []
    assert len(db.rows) == 3
    assert len([q for q in db.queries if "INSERT INTO" in q]) == 3
    rerun = run_transaction(db, official_rows(), write=True)
    assert rerun["inserted"] == 0
    assert len(db.rows) == 3


def test_conflict_during_write_aborts_instead_of_silent_partial_success():
    db = FakeConn(conflict_on=TARGET_DATES[1])
    with pytest.raises(RuntimeError, match="重複鍵衝突"):
        run_transaction(db, official_rows(), write=True)


def test_write_requires_exact_user_confirmation_before_any_connection():
    with pytest.raises(RuntimeError, match="人工確認"):
        main(["--write"])
    with pytest.raises(RuntimeError, match="人工確認"):
        main(["--write", "--confirmation", "WRONG"])
    assert len(CONFIRMATION) > 10
