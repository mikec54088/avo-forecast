"""Choosing parents for the next generation, and the guard that makes it honest.

The temporal holdout (INVARIANT #1) stops a candidate being fitted to data it
already saw. It does NOT stop this:

    Run 10 generations x 50 candidates. Score every one honestly out of sample.
    Pick the best. That winner was chosen partly because it got lucky.

Selecting the maximum over many candidates is itself a form of overfitting, and
a generational loop is structurally prone to it -- the more it explores, the
more confident it becomes that it found something. It was already visible at
n=26 on 2026-09-03: microprice_fair_value had the best skill in the set
(+0.0164), an i.i.d. interval excluding zero, and no profit at all. Naive
statistics would have promoted it.

The guard is a CONFIRMATION SET: a fixed slice of series that selection never
sees. Candidates are ranked on the rest; a winner is only believed if it also
holds up on series it was never chosen for.

Split by series rather than at random, because the failure mode is specific:
the winner works on whichever series happened to dominate the sample. Random
market-level splits leave the same series on both sides and test nothing --
Kalshi's top 20 series are over half of all observations.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from avo.core.types import Score

CONFIRMATION_FRACTION = 0.25
CONFIRMATION_SALT = "avo-confirmation-v1"


def is_confirmation_group(group: str, fraction: float = CONFIRMATION_FRACTION) -> bool:
    """Is this group reserved for confirmation rather than selection?

    Deterministic on the group name, so the split is identical across runs,
    machines and generations. That stability is the point: a split that moved
    between generations would let a candidate be selected on data that later
    became its own confirmation set, which is the leak this exists to prevent.

    The salt is versioned. Changing it reshuffles the split and invalidates
    every confirmation result ever recorded, so it should be treated the way
    the fitness definition is.
    """
    digest = hashlib.sha256((CONFIRMATION_SALT + group).encode()).digest()
    return (int.from_bytes(digest[:4], "big") / 2**32) < fraction


def split_groups(groups: Sequence[str]) -> tuple[list[str], list[str]]:
    """(selection_groups, confirmation_groups)."""
    uniq = sorted(set(groups))
    conf = [g for g in uniq if is_confirmation_group(g)]
    conf_set = set(conf)
    return [g for g in uniq if g not in conf_set], conf


@dataclass(frozen=True)
class Verdict:
    """What a candidate earned, and whether it survived confirmation.

    `confirmed` is about SKILL, because INVARIANT #2 makes Brier skill the
    fitness. `gate_confirmed` reports whether the experiment's own gate -- for
    kalshi_quant, whether the edge survives crossing the spread -- also holds on
    series selection never saw. The two are reported side by side rather than
    merged: on this data they disagree constantly, and collapsing them into one
    boolean is how a high-skill money-loser gets promoted.
    """

    candidate_id: str
    selection: Score
    confirmation: Score | None = None
    confirmed: bool = False
    note: str = ""
    gate_selection: bool = False
    gate_confirmed: bool = False
    gate_note: str = ""

    def __str__(self) -> str:
        s = f"{self.candidate_id:<26} sel {self.selection.primary:+.4f}"
        if self.confirmation is not None:
            s += f"  conf {self.confirmation.primary:+.4f}"
        return f"{s}  {'CONFIRMED' if self.confirmed else self.note}"


def confirm(
    selection: Score,
    confirmation: Score,
    min_observations: int = 500,
    gate: Callable[[Score], tuple[bool, str]] | None = None,
) -> Verdict:
    """Does a candidate that looked good on selection data hold up off it?

    Requires the confirmation interval to exclude zero AND point the same way
    as selection. A candidate whose confirmation interval merely overlaps
    selection is not confirmed -- overlapping with a wide interval is what
    noise does.

    This deliberately rejects candidates that are probably fine. The asymmetry
    is intended: a false positive gets bred from and compounds through every
    later generation, while a false negative costs one candidate.
    """
    cid = selection.candidate_id
    # The experiment's gate, evaluated on BOTH halves. A gate that holds only
    # where the candidate was selected is the same trap the skill split exists
    # to catch, one metric over.
    g_sel, g_conf, g_note = False, False, ""
    if gate is not None:
        g_sel, sel_why = gate(selection)
        g_conf, conf_why = gate(confirmation)
        g_note = f"sel {sel_why}; conf {conf_why}"

    def _v(confirmed: bool, note: str) -> Verdict:
        return Verdict(cid, selection, confirmation, confirmed, note,
                       g_sel, g_conf, g_note)

    if confirmation.n_observations < min_observations:
        return _v(False, f"too few confirmation observations "
                         f"({confirmation.n_observations} < {min_observations})")
    lo, hi = confirmation.primary_ci
    if lo != lo or hi != hi:
        return _v(False, "no confirmation interval")
    if selection.primary > 0 and lo > 0:
        return _v(True, "")
    if selection.primary < 0 and hi < 0:
        return _v(True, "")
    return _v(False, f"not confirmed: [{lo:+.4f},{hi:+.4f}] spans zero "
                     "or disagrees with selection")


class SelectionPolicy:
    """Rank scored candidates and choose parents for the next generation.

    Sorts on `primary` alone -- INVARIANT #2 makes Brier skill the fitness and
    P&L a reported gate rather than a selection criterion. Skill is used
    because it converges faster and is harder to game; the gate is what stops a
    high-skill unprofitable candidate being mistaken for a discovery.
    """

    def __init__(
        self,
        require_positive: bool = True,
        min_observations: int = 500,
        exclude: Sequence[str] = (),
    ):
        self.require_positive = require_positive
        self.min_observations = min_observations
        # Diagnostic instruments, never parents. See eligible().
        self.exclude = set(exclude)

    def eligible(self, scored: Sequence[Score]) -> list[Score]:
        """Candidates fit to breed from.

        Controls are excluded, and that is not a detail. The first live loop on
        2026-09-05 chose baseline_sharpened and control_middle_only as parents:
        one is a tripwire that earns skill precisely because the fitness
        denominator is biased and makes no money, the other trades the band the
        market prices correctly and exists to fail. Both score mildly positive,
        so a policy that only looks at skill picks them -- and then spends a
        generation optimising toward the traps the P&L gate exists to catch.

        A control is an instrument. Improving it destroys its use.
        """
        out = []
        for s in scored:
            if s.candidate_id in self.exclude:
                continue
            if s.primary != s.primary:                     # NaN
                continue
            if s.n_observations < self.min_observations:
                continue
            if self.require_positive and s.primary <= 0:
                continue
            out.append(s)
        return out

    def choose_parents(self, scored: Sequence[Score], k: int) -> list[Score]:
        """The best k by skill, breaking ties toward more observations.

        Falls back to the least-bad candidates when nothing scores positive. A
        generation where everything failed still has to breed from something,
        and refusing to choose would stall the loop at exactly the point -- an
        all-negative generation -- where exploring differently matters most.
        """
        pool = self.eligible(scored)
        if not pool:
            pool = [s for s in scored
                    if s.candidate_id not in self.exclude
                    and s.primary == s.primary
                    and s.n_observations >= self.min_observations]
        pool.sort(key=lambda s: (-s.primary, -s.n_observations))
        return pool[:k]
