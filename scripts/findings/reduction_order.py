"""A sum has no safe length: floating-point addition is not associative, so
two correct implementations disagree from twelve elements onward.

Run: ``python scripts/findings/reduction_order.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from engine.core.bitwise import ORDER_DEPENDENT, classify
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="reduction-order",
    claim=("NumPy sums in pairwise blocks and a sequential loop sums in "
           "order. They first disagree at twelve elements, and past that "
           "which lengths agree depends on the values rather than on the "
           "length — so 'reduce only small blocks' has no threshold to use."),
    source="docs/rfc-072-bitwise-boundary.md",
)


def _sequential(values: np.ndarray) -> float:
    total = 0.0
    for value in values:
        total += float(value)
    return total


def demonstrate() -> dict:
    """Compare NumPy's pairwise reduction with a sequential one.

    No compiler needed: the disagreement is between two association orders,
    not between two libraries, which is why it is a property of the
    arithmetic rather than of any implementation.
    """
    rng = np.random.default_rng(7)
    disagreeing, agreeing = [], []
    for n in range(1, 400):
        values = rng.uniform(0.0, 1e6, n)
        (disagreeing if np.sum(values) != _sequential(values)
         else agreeing).append(n)

    at_scale = {}
    for n in (1_000, 10_000, 100_000):
        values = rng.uniform(0.0, 1e6, n)
        pairwise, sequential = float(np.sum(values)), _sequential(values)
        at_scale[str(n)] = {
            "pairwise": pairwise,
            "sequential": sequential,
            "identical": pairwise == sequential,
            "relative_difference": abs(pairwise - sequential) / pairwise,
        }

    return {
        "first_disagreement_at": min(disagreeing),
        "lengths_1_to_399_that_disagree": len(disagreeing),
        "lengths_above_the_first_that_still_agree":
            len([n for n in agreeing if n > min(disagreeing)]),
        "example_agreeing_above_the_boundary":
            sorted(n for n in agreeing if n > min(disagreeing))[:10],
        "at_scale": at_scale,
        "classified_as": {op: classify(op) for op in ("sum", "mean", "cumsum")},
        "is_never_compiled": all(classify(op) == "reduce"
                                 for op in ORDER_DEPENDENT),
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
