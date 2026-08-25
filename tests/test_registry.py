from avo.core import registry


def test_experiments_are_discoverable():
    names = registry.available()
    assert "kalshi_quant" in names
    assert "kalshi_research" in names


def test_planned_experiments_are_marked():
    assert registry.config("kalshi_research")["experiment"]["status"] == "planned"


def test_seed_candidates_load():
    exp = registry.load("kalshi_quant")
    seeds = {c.candidate_id for c in exp.seed_candidates()}
    assert {"baseline_market", "baseline_shrunk", "baseline_base_rate",
            "baseline_sharpened"} <= seeds
