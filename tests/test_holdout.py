"""The holdout invariant is what makes results meaningful. Test it hard."""
from datetime import datetime, timedelta, timezone

import pytest

from avo.core.holdout import HoldoutViolation, assert_clean, eligible
from avo.core.types import Candidate

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _cand(created_at=T0):
    return Candidate("c1", "test_exp", 0, None, created_at, "x.y", "test")


class Obs:
    def __init__(self, days):
        self.resolved_at = T0 + timedelta(days=days)


KEY = lambda o: o.resolved_at


def test_future_observations_are_eligible():
    assert len(eligible(_cand(), [Obs(1), Obs(30)], KEY)) == 2


def test_past_observations_are_excluded():
    assert eligible(_cand(), [Obs(-1), Obs(-30)], KEY) == []


def test_simultaneous_is_excluded():
    """Strictly after. Same-instant resolution is not eligible."""
    assert eligible(_cand(), [Obs(0)], KEY) == []


def test_assert_clean_raises_on_leakage():
    with pytest.raises(HoldoutViolation):
        assert_clean(_cand(), [Obs(1), Obs(-1)], KEY)


def test_assert_clean_passes_when_clean():
    assert_clean(_cand(), [Obs(1), Obs(2)], KEY)
