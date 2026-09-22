"""The entries table must not be usable while capture has outrun it.

The bug this is written against, measured 2026-09-22: the table was built
2026-09-20T01:49Z and nothing rebuilt it, because `avo entries build` was a
manual command with no schedule. _meta_ok() checks the POLICY only, so the
stale store loaded happily and:

  - unclimbed_tight, unclimbed_far and control_news_fade_band, all written
    after that timestamp, scored zero observations and would have scored zero
    forever -- INVARIANT #1 admits only ground truth resolving after
    created_at, and none had been ingested;
  - gen006, gen007 and gen008 each read exactly 157,108 observations with
    identical fill counts, so three "no candidate is new and no verdict has
    moved" skips were arithmetic rather than evidence about the market.

No network: these build partition directories on disk and read them back.
"""
from __future__ import annotations

import pytest

from experiments.kalshi_quant import entries_store


def mk(root, entries_dates=(), resolution_dates=()):
    for d in entries_dates:
        (root / "entries" / "entries" / f"date={d}").mkdir(parents=True)
    for d in resolution_dates:
        (root / "resolutions" / f"date={d}").mkdir(parents=True)
    return root


def test_it_reports_how_far_behind_capture_the_table_is(tmp_path):
    mk(tmp_path, ["2026-09-18", "2026-09-20"], ["2026-09-20", "2026-09-22"])
    assert entries_store.freshness(tmp_path) == ("date=2026-09-20", "date=2026-09-22")
    assert entries_store.stale_by_days(tmp_path) == 2


def test_a_current_table_is_not_stale(tmp_path):
    mk(tmp_path, ["2026-09-22"], ["2026-09-22"])
    assert entries_store.stale_by_days(tmp_path) == 0.0


def test_one_day_behind_is_normal(tmp_path):
    """The newest resolution partition is still being written during the day,
    so one day behind must not trip the guard -- only a missed rebuild should."""
    mk(tmp_path, ["2026-09-21"], ["2026-09-22"])
    from experiments.kalshi_quant.experiment import MAX_ENTRIES_STALENESS_DAYS
    assert entries_store.stale_by_days(tmp_path) <= MAX_ENTRIES_STALENESS_DAYS


def test_a_table_ahead_of_resolutions_is_not_negative(tmp_path):
    mk(tmp_path, ["2026-09-23"], ["2026-09-22"])
    assert entries_store.stale_by_days(tmp_path) == 0.0


@pytest.mark.parametrize("ent, res", [
    ((), ("2026-09-22",)),          # cold store
    (("2026-09-22",), ()),          # no resolutions captured yet
    ((), ()),                       # nothing at all
])
def test_a_missing_side_is_not_treated_as_stale(tmp_path, ent, res):
    """A cold table is a different problem with a different message; the guard
    must not claim staleness when there is nothing to compare against."""
    mk(tmp_path, ent, res)
    assert entries_store.stale_by_days(tmp_path) == 0.0


def test_a_stray_directory_cannot_silently_disable_the_guard(tmp_path):
    """"date=tmp" sorts above every real partition, so taking the max of raw
    names would make a stray directory the newest one, parse-fail, and report
    the table as fresh. That is the same quiet failure the guard exists to
    catch, so unparseable names are skipped rather than ranked."""
    mk(tmp_path, ["2026-09-20"], ["2026-09-22"])
    (tmp_path / "resolutions" / "date=tmp").mkdir()
    assert entries_store.freshness(tmp_path)[1] == "date=2026-09-22"
    assert entries_store.stale_by_days(tmp_path) == 2
