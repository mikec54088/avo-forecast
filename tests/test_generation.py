"""Phase 4 plumbing: backend wrapper, validation, and the generate loop.

No network and no real agent. A FakeBackend writes whatever the test wants it
to write, which lets every failure mode be exercised deterministically --
including the ones that are hard to provoke on purpose from a real agent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
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


def test_resolve_cli_prefers_an_explicit_override(monkeypatch, tmp_path):
    """A scheduled job should be able to say exactly what to run."""
    from avo.core.backends import resolve_cli

    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("CLAUDE_EXE", str(exe))
    assert resolve_cli("claude") == str(exe)

    monkeypatch.setenv("CLAUDE_EXE", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="does not exist"):
        resolve_cli("claude")


def test_resolve_cli_falls_back_to_known_install_dirs(monkeypatch, tmp_path):
    """launchd gives PATH=/usr/bin:/bin:/usr/sbin:/sbin, so which() finds
    nothing for a user-directory install. Measured 2026-09-10: every research
    call from the launchd agent failed this way while the identical call from a
    shell worked -- and the agent still exited 0."""
    import avo.core.backends as B

    monkeypatch.delenv("CLAUDE_EXE", raising=False)
    monkeypatch.setattr(B.shutil, "which", lambda _: None)
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(B, "_CLI_DIRS", (tmp_path,))
    assert B.resolve_cli("claude") == str(exe)


def test_resolve_cli_raises_rather_than_returning_an_unrunnable_name(monkeypatch, tmp_path):
    """Returning the bare name turns one missing binary into N identical
    "backend exited 1" lines with no cause in any of them."""
    import avo.core.backends as B

    monkeypatch.delenv("CLAUDE_EXE", raising=False)
    monkeypatch.setattr(B.shutil, "which", lambda _: None)
    monkeypatch.setattr(B, "_CLI_DIRS", (tmp_path / "nowhere",))
    with pytest.raises(FileNotFoundError, match="minimal PATH"):
        B.resolve_cli("claude")


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


# ------------------------------- one invocation, more than one candidate file

SECOND = GOOD.replace("fake_good", "fake_second").replace("0.02", "0.03")


def test_every_accepted_candidate_is_stamped_not_just_the_first(cdir):
    """The second door into INVARIANT #1.

    generate_once only ever removes REJECTED files, so a second accepted one
    stays on disk, where seed_candidates() globs it into the registry. Stamping
    good[0] alone left it there carrying whatever created_at the agent wrote,
    which is exactly what stamp_created_at exists to prevent. On 2026-09-05 that
    put persistent_quote_favourite into the registry backdated 29 hours, with
    83% of its scored observations already resolved when the file was written.
    """
    a = _run(cdir, {"fake_good.py": GOOD, "fake_second.py": SECOND})
    assert a.accepted
    assert len(a.kept_paths) == 2, "both accepted files must be recorded"
    for path in a.kept_paths:
        src = Path(path).read_text()
        assert "2026-09-01T00:00:00" not in src, f"{path} kept the agent's timestamp"
        assert a.created_at in src, f"{path} was not stamped"


def test_candidates_from_one_invocation_share_one_timestamp(cdir):
    a = _run(cdir, {"fake_good.py": GOOD, "fake_second.py": SECOND})
    stamps = {re.search(r'"created_at"\s*:\s*"([^"]+)"', Path(p).read_text()).group(1)
              for p in a.kept_paths}
    assert len(stamps) == 1, "one invocation implies one creation instant"


def test_a_write_onto_an_existing_candidate_is_refused(cdir):
    """Two attempts wrote ladder_upper_body.py on 2026-09-05 and the second
    silently replaced the first; gen001.json names it twice for two different
    candidates, one of which no longer exists. An overwrite is invisible to
    `after - before`, so it has to be caught by name."""
    (cdir / "fake_good.py").write_text(GOOD)
    a = _run(cdir, {"fake_good.py": SECOND})
    assert not a.accepted
    assert "overwrote" in a.reason
    assert [Path(c).name for c in a.collided] == ["fake_good.py"]


def test_an_overwrite_does_not_hide_a_genuinely_new_candidate(cdir):
    (cdir / "fake_good.py").write_text(GOOD)
    a = _run(cdir, {"fake_good.py": SECOND, "fake_third.py": SECOND.replace(
        "fake_second", "fake_third")})
    assert a.accepted
    assert [Path(k).name for k in a.kept_paths] == ["fake_third.py"]
    assert [Path(c).name for c in a.collided] == ["fake_good.py"]


def test_an_untracked_overwrite_is_reported_as_unrecoverable(cdir, capsys):
    """A tracked file comes back from git; an untracked one was destroyed by the
    write itself. Saying so is the only honest option -- see restore_overwritten."""
    (cdir / "fake_good.py").write_text(GOOD)
    _run(cdir, {"fake_good.py": SECOND})
    assert "unrecoverable" in capsys.readouterr().out


def test_an_unrecoverable_overwrite_leaves_no_candidate_wearing_the_old_name(cdir):
    """Left in place, the agent's unvalidated file is imported by
    seed_candidates() as the candidate it replaced -- unstamped, under a name
    the registry already trusts. Moved aside, it is readable and is not a
    candidate."""
    (cdir / "fake_good.py").write_text(GOOD)
    _run(cdir, {"fake_good.py": SECOND})
    assert not (cdir / "fake_good.py").exists()
    assert (cdir / "fake_good.py.overwritten").read_text() == SECOND
    assert not list(cdir.glob("*.py")), "no .py may survive an unrecoverable overwrite"


def test_a_trial_stashes_every_candidate_an_invocation_produced(cdir, tmp_path):
    """Leaving the second behind would put an unscored candidate in the
    registry, which is the one thing a trial must not do."""
    stash = tmp_path / "stash"
    be = FakeBackend(writes={"fake_good.py": GOOD, "fake_second.py": SECOND},
                     target=cdir)
    run_trial(be, registry.load("kalshi_quant"), "p", cdir, REPO, n=1,
              timeout_s=30, out_dir=stash)
    assert not list(cdir.glob("*.py")), "registry left holding a trial candidate"
    assert {p.name for p in stash.glob("*.py")} == {"fake_good.py", "fake_second.py"}


def test_a_tracked_overwrite_is_restored_from_git(tmp_path):
    """The half that actually recovers data. An untracked overwrite is gone for
    good; a tracked one is still in the object store, so it comes back."""
    import subprocess

    from avo.core.generate import restore_overwritten

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    target = tmp_path / "candidates"
    target.mkdir()
    (target / "kept.py").write_text(GOOD)
    git("add", "-A")
    git("commit", "-qm", "seed")

    (target / "kept.py").write_text("clobbered")
    restored, lost = restore_overwritten(tmp_path, [target / "kept.py"])

    assert restored == ["candidates/kept.py"] and not lost
    assert (target / "kept.py").read_text() == GOOD, "the original did not come back"


# ------------------------------------------------- probe coverage regression

@pytest.mark.parametrize("name,field", [
    ("book_imbalance_tilt", "book depth"),
    ("early_settle_aware", "close time"),
    ("series_base_rate_blend", "series history"),
    ("last_trade_blend", "last price"),
    ("volume_weighted", "volume"),
])
def test_probe_varies_every_field_a_candidate_may_key_on(name, field):
    """Regression for 2026-09-01: the probe swept price and held everything
    else constant, so four legitimate generated candidates -- a depth-weighted
    mid, a microprice, a recency tilt and a distant-close tilt -- were rejected
    for 'never deviating'. Three committed hand-written candidates failed it
    too, which is what proved the probe wrong rather than the candidates.

    Each candidate here reads exactly one such field. If the probe stops
    varying that field, this fails."""
    v = validate_candidate_file(
        REPO / "experiments" / "kalshi_quant" / "candidates" / f"{name}.py",
        PROBE, REPO,
    )
    assert v.accepted, f"probe no longer varies {field}: {v.problems}"


def test_probe_still_rejects_a_candidate_that_truly_takes_no_position():
    """Widening the probe must not weaken the check it exists for."""
    v = validate_candidate_file(
        REPO / "experiments" / "kalshi_quant" / "candidates" / "baseline_market.py",
        PROBE, REPO,
    )
    assert not v.accepted and "never deviates" in v.problems[0]


def test_summary_separates_timeouts_from_validation_failures():
    """They have different fixes: a bigger budget versus a better candidate."""
    from avo.core.generate import Attempt
    att = [Attempt("1", "f", True, "accepted", 300.0),
           Attempt("2", "f", False, "backend timed out after 420s", 420.0),
           Attempt("3", "f", False, "no forecast() function", 100.0)]
    s = summarise(att)
    assert "1 timed out" in s and "1 failed validation" in s
    assert "no forecast() function" in s
    assert "timed out" not in s.split("validation failures:")[1]


# ---------------------------------------------------------------- scope

def test_out_of_scope_repo_writes_are_reverted(tmp_path):
    """A generation agent has the same tool access a person does. The
    2026-09-01 trials left edits outside the candidates directory; a candidate
    generator that quietly edits the scorer would invalidate its own run."""
    import subprocess

    from avo.core.generate import git_dirty, revert_out_of_scope

    repo = tmp_path / "repo"
    (repo / "candidates").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    tracked = repo / "scorer.py"
    tracked.write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)

    before = git_dirty(repo)
    tracked.write_text("agent meddled\n")          # tracked, was clean
    (repo / "stray_note.md").write_text("junk\n")  # untracked, agent-created
    (repo / "candidates" / "ok.py").write_text("x = 1\n")  # in scope

    # Detection is always on.
    from avo.core.generate import out_of_scope_changes
    assert set(out_of_scope_changes(repo, repo / "candidates", before)) == {
        "scorer.py", "stray_note.md"}

    # Reverting is opt-in, and deleting untracked files needs a second opt-in.
    reverted = revert_out_of_scope(repo, repo / "candidates", before)
    assert set(reverted) == {"scorer.py"}, "untracked file deleted without opt-in"
    assert tracked.read_text() == "original\n", "tracked file not restored"
    assert (repo / "stray_note.md").exists(), "untracked file deleted by default"
    assert (repo / "candidates" / "ok.py").exists(), "in-scope file was destroyed"

    revert_out_of_scope(repo, repo / "candidates", before, delete_untracked=True)
    assert not (repo / "stray_note.md").exists()


def test_work_already_in_progress_is_never_destroyed(tmp_path):
    """Only paths clean BEFORE the invocation may be reverted. Wiping a user's
    uncommitted work would be far worse than the stray write."""
    import subprocess

    from avo.core.generate import git_dirty, revert_out_of_scope

    repo = tmp_path / "repo"
    (repo / "candidates").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    wip = repo / "wip.py"
    wip.write_text("committed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)

    wip.write_text("my uncommitted work\n")   # dirty BEFORE the agent runs
    before = git_dirty(repo)
    wip.write_text("my work plus agent edit\n")

    assert revert_out_of_scope(repo, repo / "candidates", before) == []
    assert wip.read_text() == "my work plus agent edit\n"


def test_writes_outside_the_repo_are_reported_not_reverted(tmp_path):
    """The user's memory directory is outside git's reach. Silently rewriting
    a user's own files would be a worse failure than the stray write."""
    import time

    from avo.core.generate import external_writes

    watched = tmp_path / "memory"
    watched.mkdir()
    (watched / "old.md").write_text("before\n")
    time.sleep(0.02)
    cutoff = time.time()
    time.sleep(0.02)
    (watched / "new.md").write_text("written during the run\n")

    found = external_writes([watched], cutoff)
    assert found == [str(watched / "new.md")]
    assert (watched / "new.md").exists(), "external file must NOT be removed"


def test_summary_reports_out_of_scope_writes():
    from avo.core.generate import Attempt
    att = [Attempt("1", "f", True, "accepted", 1.0, reverted=("scorer.py",),
                   external=("~/.claude/x.md",))]
    s = summarise(att)
    assert "out-of-scope writes" in s and "1 reverted" in s


# ------------------------------------------------- real-market probe

def test_probe_fixture_spans_the_field_space():
    """The probe is only as good as the sample. If the fixture stops covering
    the boundaries or the rare corners, narrow-gate candidates start being
    falsely rejected again and nothing else would notice."""
    import json
    fx = json.loads((REPO / "tests" / "fixtures" / "probe_markets.json").read_text())
    m = fx["markets"]
    assert len(m) >= 100, "sample too small to hit field combinations"
    assert min(r["yes_bid"] for r in m) < 0.01, "no near-zero prices"
    assert max(r["yes_ask"] for r in m) > 0.99, "no near-one prices"
    assert any(r["last_price"] is None for r in m), "no never-traded markets"
    assert any(r["volume"] == 0 for r in m), "no zero-volume markets"
    assert any(r["yes_ask"] - r["yes_bid"] > 0.08 for r in m), "no wide books"
    assert any(r["yes_ask"] - r["yes_bid"] <= 0.02 for r in m), "no tight books"
    assert fx["history"], "no series history; memory candidates cannot be probed"


def test_probe_accepts_a_candidate_gated_on_two_conditions_at_once(tmp_path):
    """The 2026-09-01 regression, pinned. `near_close_shoulders` needed a
    shoulder price AND a near close together; the synthetic probe varied one
    field at a time from a mid-price base, so that pair never occurred and the
    candidate was rejected for 'never deviating'.

    The bias was not random: it rejected narrow conditional strategies and
    passed blunt always-act ones -- a selection pressure toward crude
    candidates, built into the harness."""
    p = tmp_path / "narrow.py"
    p.write_text(
        'MANIFEST = {"candidate_id": "narrow", "created_at": "2026-09-02T00:00:00+00:00"}\n'
        "def forecast(market, context):\n"
        "    p = market.implied_prob\n"
        "    hours = (market.close_time - context.now).total_seconds() / 3600.0\n"
        "    if hours > 1.0:\n"
        "        return p\n"
        "    if 0.65 < p <= 0.95:\n"
        "        return min(p + 0.04, 0.999)\n"
        "    return p\n"
    )
    v = validate_candidate_file(p, PROBE, REPO)
    assert v.accepted, f"narrow-gate candidate falsely rejected again: {v.problems}"


def test_probe_uses_the_real_market_type_not_a_stub():
    """A stub that drifts from MarketSnapshot could pass something the scorer
    would reject. The probe imports the real dataclass, so a candidate reading
    a field that does not exist fails here rather than mid-run."""
    p = REPO / "tests" / "fixtures" / "probe_stub_check.py"
    p.write_text(
        'MANIFEST = {"candidate_id": "stub", "created_at": "2026-09-02T00:00:00+00:00"}\n'
        "def forecast(market, context):\n"
        "    return market.this_field_does_not_exist\n"
    )
    try:
        v = validate_candidate_file(p, PROBE, REPO)
        assert not v.accepted
        assert "raised" in v.problems[0] and "AttributeError" in v.problems[0]
    finally:
        p.unlink(missing_ok=True)


def test_scope_enforcement_does_not_revert_by_default(cdir):
    """2026-09-03: revert_out_of_scope destroyed a half-built Phase 5 --
    selection.py and memory.py reverted to stubs, loop.py and its tests deleted
    -- because they were written while a generation was in flight.

    It compares `git status` before and after a fifteen-minute invocation, so
    it cannot tell an agent's edit from a concurrent human one. Detection is
    always on and reported; acting on it is opt-in.

    The proper fix is isolation, not cleanup: run the agent in a separate git
    worktree so it cannot reach the main tree at all."""
    import inspect

    from avo.core.generate import generate_once, revert_out_of_scope

    assert inspect.signature(generate_once).parameters["revert_scope"].default is False
    assert (inspect.signature(revert_out_of_scope)
            .parameters["delete_untracked"].default is False)


# ------------------------------------------------- quota is not a candidate fault

class _FakeExperiment:
    name = "fake"

    def seed_candidates(self):
        return []

    def variation_prompt(self, parent, prior):
        return "write a candidate"


def _run_generation_with(monkeypatch, tmp_path, fake_generate_once, n):
    """Drive run_generation with a stubbed backend and no scoring."""
    from avo.core import loop as loop_mod
    from avo.core.memory import RunMemory
    from avo.core.selection import SelectionPolicy

    monkeypatch.setattr(loop_mod, "generate_once", fake_generate_once)
    memory = RunMemory("fake-run", root=tmp_path)
    return loop_mod.run_generation(
        _FakeExperiment(), SimpleNamespace(name="fake"), memory,
        tmp_path / "candidates", tmp_path, generation=1, n_candidates=n,
        policy=SelectionPolicy(), entries=[], history=None, prior=[])


def test_quota_exhaustion_is_distinguished_from_a_bad_candidate():
    """The CLI exits 1 for a bad prompt and 1 for an exhausted window alike, so
    only the reason string can tell them apart."""
    from avo.core.generate import is_quota_exhausted
    assert is_quota_exhausted(
        "backend exited 1: You've hit your session limit \u00b7 resets 11:50pm")
    assert is_quota_exhausted("backend exited 1: Credit balance too low")
    assert is_quota_exhausted("BACKEND EXITED 1: USAGE LIMIT REACHED")
    # ...and an ordinary rejection is not quota.
    assert not is_quota_exhausted(
        "forecast() never deviates from implied_prob across 204 real markets")
    assert not is_quota_exhausted("no candidate file was written")
    assert not is_quota_exhausted("backend timed out after 1800s")
    assert not is_quota_exhausted("")


def test_a_generation_stops_instead_of_burning_the_rest_of_the_batch(
        monkeypatch, tmp_path):
    """Generations 3 and 4 each spent seven further invocations against an
    exhausted window, ~30s apart, every one rejected identically, and recorded
    0/8 and 1/8 as though the agent had written bad candidates. It was never
    asked."""
    calls = []

    def fake(backend, experiment, prompt, cdir, root, **kw):
        calls.append(prompt)
        return SimpleNamespace(
            accepted=False,
            reason="backend exited 1: You've hit your session limit, resets 10:20am",
            kept_paths=(), kept_path="")

    rec = _run_generation_with(monkeypatch, tmp_path, fake, n=8)
    assert len(calls) == 1, f"kept invoking after quota ran out: {len(calls)} calls"
    assert rec.candidate_ids == []
    assert "out of budget" in rec.notes


def test_an_ordinary_rejection_does_not_stop_the_batch(monkeypatch, tmp_path):
    """Only quota aborts. A candidate that merely fails validation must not
    cost the generation its remaining slots."""
    calls = []

    def fake(backend, experiment, prompt, cdir, root, **kw):
        calls.append(prompt)
        return SimpleNamespace(accepted=False, reason="forecast() never deviates",
                               kept_paths=(), kept_path="")

    rec = _run_generation_with(monkeypatch, tmp_path, fake, n=4)
    assert len(calls) == 4
    assert "out of budget" not in rec.notes
