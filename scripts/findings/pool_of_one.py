"""A pooled model run one policy at a time gives every policy a pool of itself.

Run: ``python scripts/findings/pool_of_one.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.core.runner import PooledBlockError, run
from engine.core.vector import run_vectorized
from engine.report.evidence import default_specimens
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="pool-of-one",
    claim=("A @pool variable reduces across the block. Evaluated one policy "
           "at a time it reduces over one policy, so every policy sees a "
           "pool consisting of itself — and the run completes, returning "
           "plausible numbers nothing downstream would question."),
    source="docs/rfc-061-pooled-equivalence.md",
)


def _pooled_specimen():
    for specimen in default_specimens():
        cls = specimen["model_cls"]
        if specimen.get("scenarios") is None and (
                cls.pooled_names()
                or getattr(cls, "couples_model_points", False)):
            return specimen
    raise SystemExit("no pooled specimen in the worked examples")


def demonstrate() -> dict:
    """Run the wrong reading **on purpose**, so the gap is a number.

    The engine refuses this now — that is the finding's fix. So the refusal
    is provoked first, and then the per-policy reduction is reproduced by
    hand, because a finding whose consequence is never computed is a
    warning rather than a demonstration.
    """
    specimen = _pooled_specimen()
    cls = specimen["model_cls"]
    points = list(specimen["modelpoints"])
    pooled = cls.pooled_names()
    name = pooled[0] if pooled else cls.var_names()[0]
    call = dict(assumptions=specimen["assumptions"],
                proj_len=specimen["proj_len"])

    refused = None
    try:
        run(cls, points, outputs=[name], **call)
    except PooledBlockError as exc:
        refused = str(exc)

    # The block, reduced correctly: one instance over the whole population.
    correct = run_vectorized(cls, points, outputs=[name], **call)
    block = [float(v) for v in correct.array(name)[:, 0]]

    # The same variable with each policy alone — a pool of one, each time.
    alone = []
    for point in points:
        one = run_vectorized(cls, [point], outputs=[name], **call)
        alone.append([float(v) for v in one.array(name)[:, 0]])

    first_policy_alone = alone[0]
    worst = max(
        (abs(a - b) / abs(b) if b else abs(a - b))
        for a, b in zip(first_policy_alone, block)
    )
    return {
        "template": cls.__name__,
        "pooled_variable": name,
        "policies_in_block": len(points),
        "refused_by": refused.split(".")[0] if refused else None,
        "block_reduction_first_periods": block[:5],
        "pool_of_one_first_periods": first_policy_alone[:5],
        "worst_relative_difference": worst,
        "plausible": all(abs(v) < 1e12 for v in first_policy_alone),
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
