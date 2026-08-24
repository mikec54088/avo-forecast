"""Stagnation detection and redirection. PHASE 6 — do not build yet.

Deliberately last. Its heuristics must distinguish 'the search has plateaued
across generations' from 'we are 9 days into a 30-day evaluation window'.
Tuning that against imagination instead of real generational data is wasted
work and produces a supervisor that intervenes on noise.
"""


class Supervisor:
    def assess(self, run_state):
        raise NotImplementedError("Phase 6. See docs/ROADMAP.md.")
