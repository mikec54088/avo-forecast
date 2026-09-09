"""The variation prompt: what the generating agent is told.

Rewritten 2026-09-09 after three generations circled the same dead region. The
prompt is where the loop's learning either reaches the agent or does not, so
its content is tested like code: ordering, mechanisms, the measurability floor,
and which exemplar it points at.
"""
from __future__ import annotations

from pathlib import Path

from avo.core import registry
from avo.core.types import Score

EXP = registry.load("kalshi_quant")


def _score(cid, primary, pnl, fills, se=0.001):
    return Score(cid, primary, (primary - 0.001, primary + 0.001), 5000,
                 secondary={"pnl_per_contract": pnl, "pnl_n_fills": float(fills),
                            "pnl_se_clustered": se})


def test_tried_list_is_ordered_by_money_verdict_not_skill():
    """Sorted by skill, the staleness-artifact family leads the list and reads
    as the best work so far. It is the worst: proven to lose money."""
    loser = _score("high_skill_loser", 0.0145, -0.0123, 20_000, se=0.002)
    unproven = _score("unproven", 0.0005, +0.05, 100)
    winner = _score("winner", 0.0007, +0.0546, 375, se=0.010)
    text = EXP.variation_prompt(None, [loser, unproven, winner])
    assert text.index("high_skill_loser") < text.index("unproven") < text.index("winner")
    assert "LOSES MONEY (proven)" in text.split("high_skill_loser")[1].split("\n")[0]


def test_prompt_carries_failure_mechanisms_not_just_scores():
    text = EXP.variation_prompt(None, [])
    for phrase in ["function of the midpoint", "staleness artifact",
                   "gated on PRICE", "collapsed on held-out", "non-monotone",
                   "mutually exclusive"]:
        assert phrase in text, f"mechanism missing: {phrase}"


def test_prompt_states_a_measurability_floor():
    text = EXP.variation_prompt(None, [])
    assert "at least 1% of markets" in text
    assert "unfalsifiable" in text


def test_prompt_names_the_unexplored_inputs_and_the_dead_ones():
    text = EXP.variation_prompt(None, [])
    for f in ["sibling_sum", "yes_bid_size", "close_time", "series_history"]:
        assert f in text
    assert "liquidity (always 0.0)" in text
    assert "is_mve" in text


def test_house_style_exemplar_is_not_a_midpoint_transform():
    """The old exemplar, favourite_longshot.py, read nothing but implied_prob
    and taught exactly the pattern that is proven empty."""
    text = EXP.variation_prompt(None, [])
    assert "favourite_longshot.py for the house style" not in text
    assert "neglected_leg.py" in text
    src = Path("experiments/kalshi_quant/candidates/neglected_leg.py").read_text()
    assert "siblings" in src, "exemplar must read something beyond the midpoint"


def test_prompt_forbids_scratch_files_in_the_candidates_dir():
    """Generation 3 left 13 pickles in the registry directory."""
    assert "scratch" in EXP.variation_prompt(None, [])
