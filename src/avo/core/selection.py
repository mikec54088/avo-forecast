"""Which candidate to branch from next. PHASE 5 — do not build yet.

Start with: rank by skill, sample the top quartile with some exploration
weight. Rank, do not threshold — relative ordering converges far faster than
absolute performance over a 4-week window.
"""


class SelectionPolicy:
    def choose_parents(self, scored, k: int):
        raise NotImplementedError("Phase 5. See docs/ROADMAP.md.")
