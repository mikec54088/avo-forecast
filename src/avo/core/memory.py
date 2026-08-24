"""Persistent memory across contexts. PHASE 5 — do not build yet.

Two stores, deliberately separate:
  run memory  — authoritative, ephemeral, per-run. Candidates, scores, lineage.
  priors      — namespaced BY EXPERIMENT, read-only at run start, written only
                after a successful run, via a distillation step.

Never share priors across experiments. Retrieval will surface superficially
similar but wrong precedents and prime bad hypotheses early, when the search is
most steerable — and a bad conclusion has no natural expiry.
"""


class RunMemory:
    def __init__(self, run_id: str, root: str) -> None:
        raise NotImplementedError("Phase 5. See docs/ROADMAP.md.")


class PriorsStore:
    def __init__(self, experiment: str, root: str) -> None:
        raise NotImplementedError("Phase 5+. Do not build until run #3.")
