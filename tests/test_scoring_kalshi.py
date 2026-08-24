from experiments.kalshi_quant.scoring import Observation, bootstrap_ci, skill_score


def test_matching_market_gives_zero_skill():
    obs = [Observation(0.7, 0.7, 1), Observation(0.3, 0.3, 0)]
    assert abs(skill_score(obs)[2]) < 1e-9


def test_perfect_forecast_gives_skill_one():
    obs = [Observation(1.0, 0.6, 1), Observation(0.0, 0.4, 0)]
    assert abs(skill_score(obs)[2] - 1.0) < 1e-9


def test_worse_than_market_is_negative():
    obs = [Observation(0.2, 0.8, 1), Observation(0.8, 0.2, 0)]
    assert skill_score(obs)[2] < 0


def test_ci_brackets_point_estimate():
    obs = [Observation(0.6, 0.5, 1)] * 40 + [Observation(0.4, 0.5, 0)] * 40
    lo, hi = bootstrap_ci(obs, n=200)
    assert lo <= skill_score(obs)[2] <= hi
