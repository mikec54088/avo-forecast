"""Phase 2 pipeline: snapshots + resolutions -> holdout -> skill.

Synthetic Parquet in tmp_path. No network, no dependency on captured data.

The properties asserted here are the ones whose failure would silently inflate
every score in the project rather than raise: a leaky context, a holdout that
lets earlier truth through, or an entry chosen after the outcome was known.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import T0  # shared fixture module

from avo.core.holdout import HoldoutViolation, assert_clean
from avo.core.types import Candidate, Score
from experiments.kalshi_quant.experiment import (
    KalshiQuantExperiment,
    paper_trade,
    passes_pnl_gate,
)
from experiments.kalshi_quant.observations import (
    SeriesHistory,
    _decision_mask,
    _read_snapshot,
    first_signal,
    is_decision_quote,
    load_paths,
    read_resolutions,
)
from experiments.kalshi_quant.scoring import (
    Observation,
    bootstrap_ci,
    bootstrap_ci_clustered,
)


def _candidate(created_at: datetime, cid: str = "t") -> Candidate:
    return Candidate(
        candidate_id=cid, experiment="kalshi_quant", generation=0, parent_id=None,
        created_at=created_at,
        module_path="experiments.kalshi_quant.candidates.baseline_market",
        rationale="test",
    )


def _paths(root):
    return {p.ticker: p for p in load_paths(root)}


def _hist(root):
    return SeriesHistory.from_resolutions(read_resolutions(root))


class _Always:
    """Disagrees with the market at every quote."""
    @staticmethod
    def forecast(market, context):
        return min(market.implied_prob + 0.05, 0.99)


class _OnceItHasAPath:
    """Acts only once it has seen two earlier quotes -- m1's third decision."""
    @staticmethod
    def forecast(market, context):
        if len(context.price_history) >= 2:
            return min(market.implied_prob + 0.05, 0.99)
        return market.implied_prob


def test_the_pool_is_every_resolved_market_with_a_decision_quote(data_root):
    assert set(_paths(data_root)) == {"m1", "m2", "m5"}, (
        "m3 unresolved, m4 postdates resolution, m6 beyond 72h, m7 one-sided, m8 a parlay")


def test_the_pool_does_not_depend_on_how_late_a_market_was_quoted(data_root):
    """m5's only quote is 2h before it resolved. The old policy dropped it for
    staleness -- and winning favourites' books often go one-sided early, so a
    pool built that way is chosen by outcome."""
    m5 = _paths(data_root)["m5"]
    assert len(m5.decisions) == 1 and m5.decisions[0].observed_at == T0 + timedelta(hours=4)


def test_every_decision_is_knowable_at_its_own_instant(data_root):
    """Before resolution, two-sided, not a parlay, within 72h of close."""
    for p in load_paths(data_root):
        times = [d.observed_at for d in p.decisions]
        assert times == sorted(times), "decisions must be oldest first"
        for d in p.decisions:
            assert d.observed_at < p.resolved_at, "a decision after the outcome"
            assert is_decision_quote(d)


def test_the_two_decision_rules_agree(data_root):
    """_decision_mask (the replay, vectorised) and is_decision_quote (the
    paper runner) are the same rule; if they drift the backtest and live
    trading ask about different quotes."""
    from experiments.kalshi_quant.observations import _row_to_snapshot
    snap = _read_snapshot(str(next((data_root / "snapshots").glob("*/*.parquet"))))
    for c in ("ticker", "event_ticker", "series_ticker", "title"):
        snap[c] = snap[c].astype(str)
    snap = snap.assign(resolved_at=T0 + timedelta(days=30))   # all still open
    vec = list(_decision_mask(snap))
    one = [is_decision_quote(_row_to_snapshot(r)) for r in snap.itertuples(index=False)]
    assert vec == one


def test_the_entry_is_the_first_signal_not_the_last_quote(data_root):
    """THE rule. A candidate that disagrees everywhere trades m1's FIRST
    quote. Picking the last quote before resolution is the hindsight leak
    this policy replaced."""
    e, p = first_signal(_paths(data_root)["m1"], _Always.forecast, _hist(data_root))
    assert e.market.observed_at == T0 and p != e.market.implied_prob


def test_a_later_signal_waits_for_its_own_quote(data_root):
    e, _ = first_signal(_paths(data_root)["m1"], _OnceItHasAPath.forecast, _hist(data_root))
    assert e.market.observed_at == T0 + timedelta(hours=2, minutes=50)
    assert [q.yes_bid for q in e.price_history] == [0.30, 0.50]


def test_a_candidate_that_never_acts_is_scored_at_the_first_quote(data_root):
    """Fixed in advance, never chosen by what happened next."""
    class Never:
        @staticmethod
        def forecast(market, context):
            return market.implied_prob
    e, p = first_signal(_paths(data_root)["m1"], Never.forecast, _hist(data_root))
    assert e.market.observed_at == T0 and p == e.market.implied_prob


def test_series_history_excludes_own_and_future_outcomes(data_root):
    h = _hist(data_root)
    for path in load_paths(data_root):
        for i in range(len(path.decisions)):
            e = path.entry_at(i)
            hist = h.context_for(e).series_history.get(e.market.series_ticker, [])
            assert all(r.ticker != e.ticker for r in hist), "context contains its own outcome"
            assert all(r.resolved_at < e.market.observed_at for r in hist), "future leaked"


def test_series_history_comes_from_every_resolution_not_just_the_pool(data_root):
    """m7 never enters the pool (one-sided book), but that it resolved YES at
    13:30 is a fact any trader knew by 14:00."""
    h = _hist(data_root)
    at = T0 + timedelta(hours=1, minutes=30)
    assert h.as_of("KXTEST", at) == [], "a resolution AT the instant is not yet known"
    assert [r.ticker for r in h.as_of("KXTEST", at + timedelta(seconds=1))] == ["m7"]


def test_holdout_excludes_truth_that_predates_the_candidate(data_root):
    exp, entries = KalshiQuantExperiment(), load_paths(data_root)
    h = _hist(data_root)
    c = _candidate(T0)
    loaded = exp.load_candidate(c)
    assert exp.score(c, loaded, entries=entries, history=h).n_observations == 3

    # after m1 resolved, m2 and m5 remain
    later = _candidate(T0 + timedelta(hours=3, minutes=1))
    assert exp.score(later, loaded, entries=entries, history=h).n_observations == 2

    # after everything resolved, nothing is scoreable
    after = _candidate(T0 + timedelta(days=1))
    s = exp.score(after, loaded, entries=entries, history=h)
    assert s.n_observations == 0
    assert s.primary != s.primary, "skill on no observations must be NaN, not 0"


def test_assert_clean_raises_on_a_dirty_set(data_root):
    entries = load_paths(data_root)
    with pytest.raises(HoldoutViolation):
        assert_clean(_candidate(T0 + timedelta(days=1)), entries, lambda e: e.resolved_at)


def test_market_baseline_scores_exactly_zero(data_root):
    """INVARIANT #2's zero point. If this drifts, every skill number moves."""
    exp, entries = KalshiQuantExperiment(), load_paths(data_root)
    c = _candidate(T0)
    s = exp.score(c, exp.load_candidate(c), entries=entries, history=_hist(data_root))
    assert s.primary == pytest.approx(0.0, abs=1e-12)
    assert s.secondary["candidate_brier"] == pytest.approx(s.secondary["market_brier"])


def test_a_broken_candidate_does_not_kill_the_run(data_root):
    """An agent-written candidate will raise or return nonsense. That must cost
    its own score, not the whole generation."""
    exp, entries = KalshiQuantExperiment(), load_paths(data_root)
    h = _hist(data_root)

    class Boom:
        @staticmethod
        def forecast(market, context):
            raise ValueError("bad candidate")

    class OutOfRange:
        @staticmethod
        def forecast(market, context):
            return 42.0

    for mod in (Boom, OutOfRange):
        s = exp.score(_candidate(T0), mod, entries=entries, history=h)
        assert s.n_observations == 0
        assert "error" in s.notes or "no usable" in s.notes


def test_score_reports_staleness_and_pnl(data_root):
    """A skill number is not readable without these. Measured 2026-08-31,
    baseline_sharpened scored +0.0286 skill while returning -0.0020/contract
    with a series-clustered CI spanning zero -- Brier skill and money pointed
    in opposite directions, and only the secondary metrics showed it."""
    exp, entries = KalshiQuantExperiment(), load_paths(data_root)
    c = _candidate(T0)
    s = exp.score(c, exp.load_candidate(c), entries=entries, history=_hist(data_root))
    for key in ("staleness_median_min", "staleness_p90_min",
                "pnl_per_contract", "pnl_n_fills", "pnl_fill_rate"):
        assert key in s.secondary, f"{key} missing from Score.secondary"
    assert s.secondary["staleness_median_min"] > 0


def test_paper_trade_is_flat_when_the_candidate_agrees_with_the_market(data_root):
    """baseline_market forecasts the midpoint exactly, so it never has a reason
    to trade. Zero fills is the correct answer, not a degenerate one."""
    entries = [p.entry_at(0) for p in load_paths(data_root)]
    out = paper_trade(entries, [e.market.implied_prob for e in entries])
    assert out["pnl_n_fills"] == 0.0
    assert out["pnl_fill_rate"] == 0.0


def test_paper_trade_pays_the_spread(data_root):
    """A candidate that is right about direction can still lose money. This is
    the whole reason P&L is reported alongside skill: INVARIANT #4 makes fills
    pessimistic, and the spread plus fee is ~2 probability points."""
    entries = [p.entry_at(0) for p in load_paths(data_root) if p.outcome == 1]
    assert entries, "fixture needs a yes-resolving market"
    # Forecast 1.0 on a market that resolved yes: maximally correct.
    out = paper_trade(entries, [1.0] * len(entries))
    if out["pnl_n_fills"]:
        # Won every trade, yet the return per contract is capped at 1 - price
        # paid, and price paid crossed the ask and added the fee.
        assert out["pnl_per_contract"] < 1.0


def _score_with(secondary: dict) -> Score:
    return Score(candidate_id="t", primary=0.0, primary_ci=(0.0, 0.0),
                 n_observations=1000, secondary=secondary)


def _pnl_se(returns_by_series):
    """Run paper_trade's SE path over synthetic per-series returns."""
    pnl = [x for v in returns_by_series for x in v]
    mean = sum(pnl) / len(pnl)
    k = len(returns_by_series)
    ss = sum((sum(v) - len(v) * mean) ** 2 for v in returns_by_series)
    return mean, (ss * k / (k - 1)) ** 0.5 / len(pnl)


def test_pnl_se_reduces_to_the_iid_error_when_every_fill_is_its_own_series():
    """With one fill per cluster there is no within-cluster correlation left to
    absorb, so the cluster-robust error must collapse onto the ordinary standard
    error of the mean. An estimator that fails this is not measuring the mean."""
    import statistics
    xs = [0.4, -0.6, 0.1, 0.9, -0.3, 0.55, -0.15, 0.2]
    mean, se = _pnl_se([[x] for x in xs])
    n = len(xs)
    iid = statistics.stdev(xs) / n**0.5          # uses the same 1/(n-1) correction
    assert mean == pytest.approx(sum(xs) / n)
    assert se == pytest.approx(iid, rel=1e-12)


def test_pnl_se_describes_the_same_quantity_as_the_point_estimate():
    """The defect fixed on 2026-09-08: the reported mean was pooled over FILLS
    while the reported error came from the unweighted mean of SERIES means. On
    real data the two disagreed in sign -- persistent_quote_favourite's pooled
    mean was +0.0149 against -0.0121 unweighted -- so `mean +/- 1.96*se` was an
    interval for neither. One big series and many singletons reproduces it."""
    groups = [[0.05] * 100] + [[-0.30]] * 10
    pooled = sum(x for v in groups for x in v) / sum(len(v) for v in groups)
    unweighted = sum(sum(v) / len(v) for v in groups) / len(groups)
    assert pooled > 0 > unweighted, "fixture must reproduce the sign disagreement"

    mean, se = _pnl_se(groups)
    assert mean == pytest.approx(pooled), "the point estimate is the pooled mean"
    assert mean != pytest.approx(unweighted)
    # The interval must contain the estimate it is attached to. Under the old
    # pairing it did not even describe the same sign of effect.
    assert mean - 1.96 * se < mean < mean + 1.96 * se
    assert not (mean - 1.96 * se <= unweighted <= mean + 1.96 * se), (
        "the unweighted mean lies outside this interval, which is the point: "
        "pairing it with the pooled estimate described two different things")


def test_pnl_se_is_not_inflated_by_singleton_series():
    """43 of persistent_quote_favourite's 92 series held exactly one fill, and a
    single fill's 'series mean' is ~+/-0.5 regardless of how little it was
    traded. Weighting those equally with a 40-fill series is what ran the old
    interval 1.6-2.0x wide."""
    import random
    rng = random.Random(0)
    dense = [[rng.gauss(0.02, 0.1) for _ in range(50)] for _ in range(4)]
    singles = [[0.5], [-0.5]] * 10
    groups = dense + singles

    _, se_new = _pnl_se(groups)
    sm = [sum(v) / len(v) for v in groups]
    m = sum(sm) / len(groups)
    se_old = (sum((x - m) ** 2 for x in sm) / (len(groups) - 1) / len(groups)) ** 0.5

    # Singletons are 20 of 24 series but only 20 of 220 fills. Equal-weighting
    # them is what inflated the shipped estimator; weighting by how much each
    # series was actually traded is what fixes it.
    assert se_new < se_old / 2


def test_pnl_stats_records_the_cluster_count():
    """A Liang-Zeger error is only trustworthy with enough clusters, and since
    selection sorts parents on the lower bound built from it, the count has to
    be readable next to the SE rather than inferred. ladder_leader passed the
    gate on 231 fills, which looks thin until you see the 86 series."""
    from experiments.kalshi_quant.experiment import pnl_stats
    st = pnl_stats({"A": [0.1, 0.2], "B": [0.3], "C": [-0.1]}, n_considered=100)
    assert st["pnl_n_series"] == 3.0
    assert st["pnl_n_fills"] == 4.0
    assert pnl_stats({}, n_considered=100)["pnl_n_series"] == 0.0


def test_fee_follows_kalshis_published_taker_schedule():
    """Replaces a flat 1c placeholder that carried a TODO since 2026-08-23. The
    flat rate was wrong in the direction that flatters results: the real charge
    peaks near 0.50 and only drops below 1c out in the tails."""
    from experiments.kalshi_quant.scoring import (
        fee_per_contract_cents,
        trading_fee_dollars,
    )

    # 0.07 * 100 * 0.72 * 0.28 = 1.4112 -> rounded UP to the cent
    assert trading_fee_dollars(0.72, 100) == pytest.approx(1.42)
    assert fee_per_contract_cents(0.72, 100) == pytest.approx(1.42)

    # peaks at the midpoint, symmetric about it, cheap in the tails
    mid = fee_per_contract_cents(0.50, 1000)
    assert mid > fee_per_contract_cents(0.20, 1000)
    assert mid > fee_per_contract_cents(0.80, 1000)
    assert fee_per_contract_cents(0.30, 1000) == pytest.approx(
        fee_per_contract_cents(0.70, 1000), rel=1e-9)
    assert fee_per_contract_cents(0.05, 1000) < 1.0 < mid

    assert trading_fee_dollars(0.5, 0) == 0.0


def test_fee_rounding_is_per_order_so_small_fills_pay_more():
    """The cent is rounded up once per ORDER, so a 1-contract fill pays the
    whole cent. That penalises tiny fills, which is realistic."""
    from experiments.kalshi_quant.scoring import fee_per_contract_cents

    assert fee_per_contract_cents(0.72, 1) > fee_per_contract_cents(0.72, 100)
    assert fee_per_contract_cents(0.72, 1) == pytest.approx(2.0)


def test_simulate_fill_charges_the_real_fee_on_the_crossed_price():
    """INVARIANT #4: the fee applies to the price we actually cross at, never
    the midpoint, and a fill is never free."""
    from datetime import datetime, timedelta, timezone

    from experiments.kalshi_quant.scoring import fee_per_contract_cents, simulate_fill
    from experiments.kalshi_quant.types import MarketSnapshot

    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    m = MarketSnapshot("T", "E", "S", "t", now, now + timedelta(hours=1),
                       yes_bid=0.70, yes_ask=0.72, last_price=0.71,
                       volume=100.0, open_interest=50.0,
                       yes_bid_size=4000.0, yes_ask_size=4000.0)

    f = simulate_fill(m, "yes", 100)
    assert f.filled and f.contracts == 100
    assert f.price_cents == pytest.approx(72.0 + fee_per_contract_cents(0.72, 100))
    assert f.price_cents > m.yes_ask_cents > m.implied_prob * 100

    # the NO side crosses at 1 - bid, and is charged on that price
    n = simulate_fill(m, "no", 100)
    assert n.price_cents == pytest.approx(30.0 + fee_per_contract_cents(0.30, 100))


def test_pnl_gate_passes_only_on_a_clustered_interval_above_zero():
    """Requiring the mean alone would pass noise. On 2026-08-31 the 20-90 min
    band had a positive mean whose series-clustered interval spanned zero."""
    ok, why = passes_pnl_gate(_score_with(
        {"pnl_n_fills": 5000.0, "pnl_per_contract": 0.02, "pnl_se_clustered": 0.005}))
    assert ok, why
    ok, why = passes_pnl_gate(_score_with(
        {"pnl_n_fills": 5000.0, "pnl_per_contract": 0.02, "pnl_se_clustered": 0.02}))
    assert not ok and "spans 0" in why


def test_pnl_gate_fails_a_loser_and_says_so():
    ok, why = passes_pnl_gate(_score_with(
        {"pnl_n_fills": 5000.0, "pnl_per_contract": -0.05, "pnl_se_clustered": 0.005}))
    assert not ok and "loses money" in why


def test_pnl_gate_refuses_to_judge_too_few_fills():
    """A lucky handful of trades must not read as an edge."""
    ok, why = passes_pnl_gate(_score_with(
        {"pnl_n_fills": 10.0, "pnl_per_contract": 0.5, "pnl_se_clustered": 0.001}))
    assert not ok and "too few fills" in why
    ok, why = passes_pnl_gate(_score_with({"pnl_n_fills": 0.0}))
    assert not ok and "no position" in why


def test_pnl_gate_and_skill_can_disagree(data_root):
    """The reason the gate exists. A candidate may post positive Brier skill
    and still fail on money; nothing in scoring.py would reveal that."""
    exp, entries = KalshiQuantExperiment(), load_paths(data_root)
    c = _candidate(T0)
    s = exp.score(c, exp.load_candidate(c), entries=entries, history=_hist(data_root))
    ok, why = passes_pnl_gate(s)
    assert isinstance(ok, bool) and why


def test_clustered_ci_is_wider_when_observations_cluster():
    """The whole point. Two series with opposite behaviour: resampling
    observations individually always draws from both and looks stable;
    resampling series can draw one twice and reveals the spread.

    Measured on real data 2026-08-31: 40,726 observations in 571 series gave
    an i.i.d. interval of [+0.0095,+0.0128] and a clustered one of
    [+0.0078,+0.0155] -- 2.3x wider."""
    obs, clusters = [], []
    for _ in range(400):
        obs.append(Observation(0.9, 0.5, 1))   # series A: candidate right
        clusters.append("A")
        obs.append(Observation(0.9, 0.5, 0))   # series B: candidate wrong
        clusters.append("B")
    iid = bootstrap_ci(obs, n=300)
    clu = bootstrap_ci_clustered(obs, clusters, n=300)
    assert (clu[1] - clu[0]) > (iid[1] - iid[0])


def test_clustered_ci_needs_more_than_one_cluster():
    """One series is not a sample of series; say so rather than inventing an
    interval from a single cluster."""
    obs = [Observation(0.6, 0.5, 1)] * 50
    lo, hi = bootstrap_ci_clustered(obs, ["only"] * 50, n=50)
    assert lo != lo and hi != hi  # NaN


def test_score_reports_both_intervals(data_root):
    exp, entries = KalshiQuantExperiment(), load_paths(data_root)
    c = _candidate(T0)
    s = exp.score(c, exp.load_candidate(c), entries=entries, history=_hist(data_root))
    assert "skill_ci_iid_lo" in s.secondary and "skill_ci_iid_hi" in s.secondary
    assert "series-clustered" in s.notes


# ------------------------------------------------- price history & siblings

def _m1_last(root):
    p = _paths(root)["m1"]
    return p.entry_at(len(p.decisions) - 1)


def test_price_history_is_strictly_before_the_decision(data_root):
    """An observation at the decision instant IS the decision, not its history."""
    m1 = _m1_last(data_root)
    assert len(m1.price_history) == 2, "earlier quotes of the same market missing"
    assert all(p.observed_at < m1.market.observed_at for p in m1.price_history)
    assert [p.yes_bid for p in m1.price_history] == [0.30, 0.50], "not oldest-first"


def test_price_history_lets_a_candidate_see_direction(data_root):
    m1 = _m1_last(data_root)
    path = [p.implied_prob for p in m1.price_history] + [m1.market.implied_prob]
    assert path == sorted(path), "history does not reconstruct the path"


def test_siblings_never_include_a_quote_from_after_the_decision(data_root):
    """m1f is in m1's event but quoted after m1 resolved. In a mutually
    exclusive event a post-resolution sibling quote IS the outcome."""
    for p in load_paths(data_root):
        for i in range(len(p.decisions)):
            e = p.entry_at(i)
            assert all(s.observed_at <= e.market.observed_at for s in e.siblings)
            assert all(s.observed_at < e.resolved_at for s in e.siblings)
            assert all(s.ticker != e.ticker for s in e.siblings), "self listed as sibling"


def test_siblings_are_found_within_the_page_tolerance(data_root):
    assert {s.ticker for s in _m1_last(data_root).siblings} == {"m1s"}
    first = _paths(data_root)["m1"].entry_at(0)
    assert "m1s" not in {s.ticker for s in first.siblings}, (
        "m1s was quoted after m1's first decision")


def test_context_carries_the_new_fields(data_root):
    ctx = _hist(data_root).context_for(_m1_last(data_root))
    assert len(ctx.price_history) == 2
    assert len(ctx.siblings) == 1
    assert ctx.sibling_sum == pytest.approx(0.275)
