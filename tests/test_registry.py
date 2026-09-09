from avo.core import registry


def test_experiments_are_discoverable():
    names = registry.available()
    assert "kalshi_quant" in names
    assert "kalshi_research" in names


def test_kalshi_research_is_active():
    """Promoted from "planned" on 2026-09-09 (G5, authorized by the human)."""
    assert registry.config("kalshi_research")["experiment"]["status"] == "active"


def test_seed_candidates_load():
    exp = registry.load("kalshi_quant")
    seeds = {c.candidate_id for c in exp.seed_candidates()}
    assert {"baseline_market", "baseline_shrunk", "baseline_base_rate",
            "baseline_sharpened"} <= seeds
