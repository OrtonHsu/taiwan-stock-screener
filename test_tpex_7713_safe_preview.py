"""Offline tests for the manual, read-only 7713 repair preview."""

import pytest

from tpex_7713_safe_preview import (
    CODE, MARKET, TARGET_DATES, check_official_rows,
    markdown_report, prepare_report,
)


def official_rows():
    return [
        (day, MARKET, CODE, "威力德生醫", close, 1000.0, close * 1000.0)
        for day, close in zip(TARGET_DATES, (70.5, 70.8, 71.0))
    ]


def test_expected_three_valid_official_rows():
    rows = check_official_rows(official_rows())
    assert list(rows) == list(TARGET_DATES)
    assert rows[TARGET_DATES[0]][4] == 70.5


def test_fails_closed_when_one_official_date_missing():
    with pytest.raises(ValueError, match="官方缺少"):
        check_official_rows(official_rows()[:-1])


@pytest.mark.parametrize("wrong", [
    ("2026-10-27", "TWSE", CODE, "a", 70.5, 1000, 100),
    ("2026-10-27", MARKET, "0000", "a", 70.5, 1000, 100),
    ("2026-09-27", MARKET, CODE, "a", 70.5, 1000, 100),
    ("2026-10-27", MARKET, CODE, "a", 0, 1000, 100),
    ("2026-10-27", MARKET, CODE, "a", 70.5, -1, 100),
])
def test_rejects_wrong_source_identity_or_price(wrong):
    with pytest.raises(ValueError, match="不符|無效"):
        check_official_rows([wrong] + official_rows()[1:])


def test_rejects_duplicate_official_date():
    with pytest.raises(ValueError, match="重複"):
        check_official_rows(official_rows() + [official_rows()[0]])


def test_all_three_missing_are_candidate_only_not_written():
    result = prepare_report(official_rows(), [])
    assert result["mode"] == "READ_ONLY"
    assert result["official_valid_targets"] == 3
    assert result["missing_dates"] == list(TARGET_DATES)
    assert result["already_stored_dates"] == []
    assert all(x["status"] == "missing_in_database" for x in result["official_quotes"])
    assert "不寫入 Supabase" in markdown_report(result)


def test_already_stored_date_is_not_counted_missing():
    database_rows = [
        (TARGET_DATES[1], MARKET, CODE, "威力德生醫", 70.8, 1000, 70800)
    ]
    result = prepare_report(official_rows(), database_rows)
    assert result["missing_dates"] == [TARGET_DATES[0], TARGET_DATES[2]]
    assert result["already_stored_dates"] == [TARGET_DATES[1]]


def test_other_market_label_requires_manual_review():
    database_rows = [
        (TARGET_DATES[0], "TPEx", CODE, "威力德生醫", 70.5, 1000, 70500)
    ]
    result = prepare_report(official_rows(), database_rows)
    assert result["ready_for_manual_review"] is False
    assert result["other_market_records"] == [
        {"date": TARGET_DATES[0], "market": "TPEx"}
    ]
    assert TARGET_DATES[0] in result["missing_dates"]


def test_rejects_unexpected_db_rows_and_duplicates():
    with pytest.raises(ValueError, match="不符合"):
        prepare_report(official_rows(), [
            ("2026-11-03", MARKET, CODE, "威力德生醫", 70.5, 1000, 70500)
        ])
    row = (TARGET_DATES[0], MARKET, CODE, "威力德生醫", 70.5, 1000, 70500)
    with pytest.raises(ValueError, match="重複"):
        prepare_report(official_rows(), [row, row])
