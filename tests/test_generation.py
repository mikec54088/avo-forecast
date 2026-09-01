"""Phase 4 plumbing: backend wrapper, validation, and the generate loop.

No network and no real agent. A FakeBackend writes whatever the test wants it
to write, which lets every failure mode be exercised deterministically --
including the ones that are hard to provoke on purpose from a real agent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from avo.core import registry
from avo.core.backends import SubprocessBackend, SubprocessResult
from avo.core.generate import generate_once, run_trial, summarise
from avo.core.validate import new_candidate_files, validate_candidate_file

REPO = Path(__file__).resolve().parents[1]
PROBE = registry.load("kalshi_quant").validation_probe()

GOOD = '''
MANIFEST = {"candidate_id": "fake_good", "generation": 1, "parent_id": None,
            "created_at": "2026-09-01T00:00:00+00:00", "rationale": "test"}
def forecast(market, context):
    p = market.implied_prob
    return min(max(p + (0.02 if p > 0.5 else -0.02), 0.001), 0.999)
'''


@dataclass
class FakeBackend:
    """Writes `writes` (name -> source) into the candidates dir when run."""

    name: str = "fake:1"
    writes: dict = field(default_factory=dict)
    exit_code: int = 0
    timed_out: bool = False
    target: Path | None = None

    def run(self, prompt, workdir, timeout_s):
        written = []
        for fname, src in self.writes.items():
            p = self.target / fname
            p.write_text(src)
            written.append(str(p))
        return SubprocessResult("out", self.exit_code, written,
                                elapsed_s=1.0, timed_out=self.timed_out)


@pytest.fixture
def cdir(tmp_path):
    d = tmp_path / "candidates"
    d.mkdir()
    return d


def _run(cdir, writes, **kw):
    be = FakeBackend(writes=writes, target=cdir, **kw)
    return generate_once(be, registry.load("kalshi_quant"), "p", cdir, REPO, timeout_s=30)


# ---------------------------------------------------------------- validation

def test_accepts_a_well_formed_candidate(cdir):
    a = _run(cdir, {"fake_good.py": GOOD})
    assert a.accepted, a.reason
    assert Path(a.kept_path).exists()


@pytest.mark.parametrize("src,expected", [
    ('def forecast(m, c): return 0.5', "no MANIFEST"),
    ('MANIFEST = {"candidate_id": "x", "created_at": "t"}', "no forecast"),
    ('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
     'def forecast(m, c): return "half"', "not a float"),
    ('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
     'def forecast(m, c): return 1.5', "outside [0,1]"),
    ('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
     'def forecast(m, c): return m.implied_prob', "never deviates"),
    ('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
     'def forecast(m, c): raise ValueError("boom")', "raised"),
    ('import nonexistent_module_xyz', "failed to import"),
    ('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
     'def forecast(m, c): return float("nan")', "NaN"),
])
def test_rejects_each_way_a_candidate_can_be_wrong(cdir, src, expected):
    a = _run(cdir, {"bad.py": src})
    assert not a.accepted
    assert expected in a.reason, a.reason


def test_rejects_a_candidate_that_escapes_the_unit_interval_at_the_boundary(cdir):
    """An additive shift that forgets to clip looks fine mid-range and fails at
    the extremes. The probe covers 0.001 and 0.999 for exactly this."""
    src = ('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
           'def forecast(m, c): return m.implied_prob - 0.02')
    a = _run(cdir, {"unclipped.py": src})
    assert not a.accepted and "outside [0,1]" in a.reason


def test_rejects_nondeterminism(cdir):
    src = ('import random\n'
           'MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
           'def forecast(m, c): return random.random()')
    a = _run(cdir, {"rand.py": src})
    assert not a.accepted and "not deterministic" in a.reason


def test_an_infinite_loop_is_rejected_not_hung(tmp_path):
    """The reason validation runs in a subprocess at all."""
    p = tmp_path / "loop.py"
    p.write_text('MANIFEST = {"candidate_id": "x", "created_at": "t"}\n'
                 'def forecast(m, c):\n    while True: pass')
    v = validate_candidate_file(p, PROBE, REPO, timeout_s=5)
    assert not v.accepted and "timed out" in v.problems[0]


# ---------------------------------------------------------------- housekeeping

def test_invalid_candidates_are_removed_from_the_registry_directory(cdir):
    """An invalid file left behind would be picked up by seed_candidates() and
    break every later run."""
    _run(cdir, {"bad.py": "def forecast(m, c): return 0.5"})
    assert not (cdir / "bad.py").exists()


def test_keep_rejects_leaves_the_file_for_inspection(cdir):
    be = FakeBackend(writes={"bad.py": "def forecast(m, c): return 0.5"}, target=cdir)
    generate_once(be, registry.load("kalshi_quant"), "p", cdir, REPO, keep=True)
    assert (cdir / "bad.py").exists()


def test_a_valid_candidate_survives_a_sibling_being_rejected(cdir):
    a = _run(cdir, {"fake_good.py": GOOD, "bad.py": "x = 1"})
    assert a.accepted
    assert (cdir / "fake_good.py").exists()
    assert not (cdir / "bad.py").exists()


def test_backend_failures_are_reported_not_raised(cdir):
    assert not _run(cdir, {}, exit_code=1).accepted
    assert "timed out" in _run(cdir, {}, timed_out=True).reason
    assert "no candidate file" in _run(cdir, {}).reason


def test_missing_backend_executable_is_a_result_not_a_crash(tmp_path):
    be = SubprocessBackend(name="nope", argv=["definitely-not-a-real-binary-xyz"])
    r = be.run("prompt", str(tmp_path), 10)
    assert r.exit_code == 127 and not r.ok and "not found" in r.stderr


def test_only_candidate_modules_count_as_candidates(tmp_path):
    """An agent may also write notes, tests, or touch __init__.py."""
    c = tmp_path / "candidates"
    c.mkdir()
    files = [str(c / "real.py"), str(c / "__init__.py"), str(c / "notes.md"),
             str(tmp_path / "elsewhere.py")]
    assert new_candidate_files(files, c) == [str(c / "real.py")]


# ---------------------------------------------------------------- trial

def test_trial_stashes_accepted_candidates_out_of_the_registry(cdir, tmp_path):
    """Twenty unscored candidates left in the registry would corrupt the next
    real generation."""
    out = tmp_path / "stash"
    be = FakeBackend(writes={"fake_good.py": GOOD}, target=cdir)
    attempts = run_trial(be, registry.load("kalshi_quant"), "p", cdir, REPO,
                         n=2, out_dir=out)
    assert all(a.accepted for a in attempts)
    assert not list(cdir.glob("*.py")), "candidate left in the registry directory"
    assert (out / "fake_good.py").exists()
    assert list(out.glob("trial-*.json")), "trial log not written"


def test_summary_reports_the_gate_and_groups_failures():
    from avo.core.generate import Attempt
    att = [Attempt("1", "f", True, "accepted", 1.0),
           Attempt("2", "f", False, "no forecast() function", 1.0),
           Attempt("3", "f", False, "no forecast() function", 1.0)]
    s = summarise(att)
    assert "1/3" in s and "33%" in s and "2x" in s and "80%" in s


# ---------------------------------------------------------------- created_at

STALE = '''
MANIFEST = {"candidate_id": "fake_stale", "generation": 1, "parent_id": None,
            "created_at": "2020-01-01T00:00:00+00:00", "rationale": "test"}
def forecast(market, context):
    p = market.implied_prob
    return min(max(p + (0.02 if p > 0.5 else -0.02), 0.001), 0.999)
'''


def test_created_at_is_stamped_by_the_harness_not_trusted_from_the_agent(cdir):
    """INVARIANT #1 is enforced entirely by created_at, and the first live
    invocation copied its siblings' timestamp instead of using its own. A stale
    timestamp is silent -- it produces a better-looking score, not an error --
    so the harness overwrites it rather than asking nicely."""
    a = _run(cdir, {"fake_stale.py": STALE})
    assert a.accepted
    assert a.created_at, "no timestamp was stamped"
    assert "2020" not in Path(a.kept_path).read_text(), "stale timestamp survived"
    assert a.created_at in Path(a.kept_path).read_text()


@pytest.mark.parametrize("manifest", [
    'MANIFEST = {"created_at": "2020-01-01T00:00:00+00:00", "candidate_id": "x"}',
    "MANIFEST = {'created_at': '2020-01-01T00:00:00+00:00', 'candidate_id': 'x'}",
    'MANIFEST = {\n    "candidate_id": "x",\n    "created_at" : "2020-01-01T00:00:00+00:00",\n}',
])
def test_stamping_survives_whatever_quoting_the_agent_used(tmp_path, manifest):
    from avo.core.generate import stamp_created_at
    p = tmp_path / "c.py"
    p.write_text(manifest + "\ndef forecast(m, c): return 0.5\n")
    ts = stamp_created_at(p)
    assert ts and "2020" not in p.read_text()


def test_stamping_reports_when_it_could_not_find_the_field(tmp_path):
    """Silently succeeding on a file with no created_at would hide the failure
    the stamping exists to prevent."""
    from avo.core.generate import stamp_created_at
    p = tmp_path / "c.py"
    p.write_text("MANIFEST = {'candidate_id': 'x'}\n")
    assert stamp_created_at(p) == ""
