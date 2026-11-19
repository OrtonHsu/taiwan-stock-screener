"""Regression checks for the V2 TWSE Mar-Jun scheduled recovery safeguards.

This checks workflow configuration, not GitHub's cron delivery guarantees.
"""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / ".github/workflows/v2_twse_mar_jun_auto.yml"
LEGACY = ROOT / ".github/workflows/auto_backfill.yml"


def test_staggered_primary_and_second_schedule_are_valid_utc_times():
    yml = TARGET.read_text(encoding="utf-8")
    assert "- cron: '23 */2 * * *'" in yml
    assert "- cron: '53 */2 * * *'" in yml
    taipei = ZoneInfo("Asia/Taipei")
    # 10:23 and 10:53 UTC == 18:23 and 18:53 in Taiwan.
    assert datetime(2026, 11, 19, 10, 23, tzinfo=timezone.utc).astimezone(
        taipei
    ).strftime("%H:%M") == "18:23"
    assert datetime(2026, 11, 19, 10, 53, tzinfo=timezone.utc).astimezone(
        taipei
    ).strftime("%H:%M") == "18:53"


def test_v2_concurrency_does_not_compete_with_legacy_twse_workflow():
    v2 = TARGET.read_text(encoding="utf-8")
    old = LEGACY.read_text(encoding="utf-8")
    assert "group: v2-twse-mar-jun-recovery" in v2
    assert "group: market-history-backfill" in old
    assert "group: market-history-backfill" not in v2
    assert "cancel-in-progress: false" in v2


def test_scheduled_writes_still_require_repo_switch_and_are_bounded():
    yml = TARGET.read_text(encoding="utf-8")
    assert "vars.TWSE_MAR_JUN_AUTO_ENABLED == 'true'" in yml
    assert "github.event_name != 'schedule'" in yml
    assert "github.event_name == 'schedule' && 'true' || inputs.write" in yml
    assert "python twse_mar_jun_auto.py --batch-size 5 --write" in yml
    assert "python twse_mar_jun_auto.py --batch-size 5" in yml
    assert "TRIGGER=" in yml and "RUN_ID=" in yml


def test_monthly_insert_remains_non_overwriting():
    code = (ROOT / "twse_mar_jun_pilot.py").read_text(encoding="utf-8")
    assert "ON CONFLICT (date, market, code) DO NOTHING" in code
