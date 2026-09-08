"""The selection guard: the confirmation split, and what it refuses.

The temporal holdout stops a candidate being fitted to data it already saw. It
does NOT stop selecting the maximum over many candidates on the same data,
which is its own overfitting and the failure a generational loop is
structurally prone to.
"""
from __future__ import annotations

from avo.core.loop import ready_to_rank
from avo.core.selection import (
    SelectionPolicy,
    confirm,
    is_confirmation_group,
    split_groups,
)
from avo.core.types import Score


def _score(cid, primary, ci, n=5000, pnl=None):
    return Score(cid, primary, ci, n,
                 secondary={} if pnl is None else {"pnl_per_contract": pnl})


# ---------------------------------------------------------------- the split

def test_split_is_deterministic():
    """A split that moved between generations would let a candidate be selected
    on data that later became its own confirmation set."""
    groups = [f"KX{i}" for i in range(500)]
    assert split_groups(groups) == split_groups(groups)


def test_split_holds_back_roughly_the_intended_fraction():
    groups = [f"KXSERIES{i}" for i in range(2000)]
    _, conf = split_groups(groups)
    assert 0.20 < len(conf) / len(groups) < 0.30


def test_split_partitions_cleanly():
    groups = [f"KX{i}" for i in range(300)] * 2
    sel, conf = split_groups(groups)
    assert not set(sel) & set(conf), "a group landed in both halves"
    assert set(sel) | set(conf) == set(groups), "a group landed in neither"


def test_a_series_is_never_split_across_both_halves():
    """Splitting by market rather than series would test nothing: Kalshi's top
    20 series are over half of all observations, so the same series would sit
    on both sides and a series-specific fluke would survive."""
    assert is_confirmation_group("KXBTCD") == is_confirmation_group("KXBTCD")


# ---------------------------------------------------------------- confirming

def test_the_gate_is_evaluated_on_both_halves_not_just_selection():
    """A gate that holds only where the candidate was selected is the same trap
    the skill split exists to catch, one metric over. Added 2026-09-08 after the
    P&L gate turned out to have no confirmation half at all."""
    seen = []

    def gate(score):
        seen.append(score.candidate_id)
        # passes on selection, fails on the held-out half
        return (score.n_observations > 1000, "ok" if score.n_observations > 1000
                else "no")

    v = confirm(_score("c", 0.01, (0.005, 0.02), n=5000),
                _score("c", 0.01, (0.005, 0.02), n=800),
                min_observations=500, gate=gate)
    assert len(seen) == 2, "the gate must be run on both subsets"
    assert v.gate_selection and not v.gate_confirmed
    assert "sel ok" in v.gate_note and "conf no" in v.gate_note


def test_gate_and_skill_verdicts_are_reported_separately():
    """INVARIANT #2 keeps skill as the fitness, so `confirmed` stays about skill.
    Merging the two booleans is how a high-skill money-loser gets promoted."""
    v = confirm(_score("c", 0.01, (0.005, 0.02)),
                _score("c", 0.01, (0.005, 0.02)),
                gate=lambda s: (False, "loses money"))
    assert v.confirmed, "skill confirmation is unaffected by the gate"
    assert not v.gate_confirmed


def test_confirm_without_a_gate_is_unchanged():
    """core/ must not require a gate; experiments that have none still confirm."""
    v = confirm(_score("c", 0.01, (0.005, 0.02)), _score("c", 0.01, (0.005, 0.02)))
    assert v.confirmed and not v.gate_selection and not v.gate_confirmed


def test_confirms_only_when_the_held_out_interval_excludes_zero():
    sel = _score("c", 0.02, (0.01, 0.03))
    assert confirm(sel, _score("c", 0.018, (0.008, 0.028))).confirmed
    v = confirm(sel, _score("c", 0.018, (-0.004, 0.040)))
    assert not v.confirmed and "spans zero" in v.note


def test_refuses_when_confirmation_disagrees_in_sign():
    """Positive on selection, negative off it, is the signature of a winner
    picked for luck rather than edge."""
    v = confirm(_score("c", 0.02, (0.01, 0.03)), _score("c", -0.02, (-0.03, -0.01)))
    assert not v.confirmed


def test_refuses_to_judge_a_thin_confirmation_set():
    v = confirm(_score("c", 0.02, (0.01, 0.03)), _score("c", 0.02, (0.01, 0.03), n=10))
    assert not v.confirmed and "too few" in v.note


def test_the_microprice_case_would_not_have_been_confirmed():
    """Real 2026-09-03 numbers. Best skill of 26 candidates, an i.i.d. interval
    excluding zero, a clustered interval spanning it, and no profit. Naive
    statistics would have promoted it; this must not."""
    sel = _score("microprice_fair_value", 0.0164, (-0.0065, 0.0514), 9173, pnl=-0.0182)
    assert not confirm(sel, _score("microprice_fair_value", 0.004,
                                   (-0.011, 0.020), 3000)).confirmed


# ---------------------------------------------------------------- policy

def test_parents_are_the_best_by_skill():
    scored = [_score("a", 0.01, (0.005, 0.015)), _score("b", 0.03, (0.02, 0.04)),
              _score("c", 0.02, (0.01, 0.03))]
    assert [p.candidate_id for p in SelectionPolicy().choose_parents(scored, 2)] == ["b", "c"]


def test_thin_and_nan_candidates_are_not_eligible():
    scored = [_score("thin", 0.9, (0.8, 1.0), n=5),
              _score("nan", float("nan"), (float("nan"), float("nan"))),
              _score("real", 0.01, (0.005, 0.015))]
    assert [p.candidate_id for p in SelectionPolicy().choose_parents(scored, 3)] == ["real"]


def test_an_all_negative_generation_still_yields_parents():
    """Every one of 26 candidates failed on 2026-09-03. Refusing to choose
    would stall the loop at exactly the point -- nothing worked -- where trying
    something different matters most."""
    scored = [_score("a", -0.01, (-0.02, -0.005)), _score("b", -0.05, (-0.06, -0.04))]
    assert [p.candidate_id for p in SelectionPolicy().choose_parents(scored, 1)] == ["a"]


def test_loop_refuses_to_rank_a_cohort_with_no_observations():
    """The loop's most seductive failure: generate a second batch before the
    first has a real score, rank on noise, breed from the winner."""
    assert not ready_to_rank([_score("just_written", 0.5, (0.4, 0.6), n=12)])
    assert ready_to_rank([_score("aged", 0.01, (0.0, 0.02), n=10_000)])


def test_controls_are_never_chosen_as_parents():
    """The first live loop on 2026-09-05 picked baseline_sharpened and
    control_middle_only as parents. One is a tripwire that earns skill because
    the fitness denominator is biased and makes no money; the other trades the
    band the market prices correctly and exists to fail. Both score mildly
    positive, so a skill-only policy picks them -- and spends a generation
    optimising toward the traps the P&L gate exists to catch.

    A control is an instrument. Improving it destroys its use."""
    scored = [_score("control_middle_only", 0.05, (0.04, 0.06)),
              _score("baseline_sharpened", 0.04, (0.03, 0.05)),
              _score("real_candidate", 0.01, (0.005, 0.015))]
    policy = SelectionPolicy(exclude=["control_middle_only", "baseline_sharpened"])
    assert [p.candidate_id for p in policy.choose_parents(scored, 3)] == ["real_candidate"]


def test_controls_stay_excluded_even_when_nothing_scores_positive():
    """The fallback path must not quietly reintroduce them."""
    scored = [_score("control_middle_only", -0.001, (-0.002, 0.0)),
              _score("real_candidate", -0.02, (-0.03, -0.01))]
    policy = SelectionPolicy(exclude=["control_middle_only"])
    assert [p.candidate_id for p in policy.choose_parents(scored, 2)] == ["real_candidate"]
