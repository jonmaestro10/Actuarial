"""An analysis of surplus depends on the order the drivers are peeled off,
so a decomposition quoted without that range is an opinion, not a measurement.

Run: ``python scripts/findings/aos_ordering.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from itertools import permutations

from engine.report.experience import (
    contribution_range,
    isolated,
    order_sensitivity,
    sequential,
    shapley,
)
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="aos-ordering",
    claim=("Peeling drivers off in a different order attributes different "
           "amounts to each. The drivers interact, so the split is not a "
           "property of the book alone — and a decomposition quoted without "
           "its order sensitivity presents a choice as a measurement."),
    source="docs/rfc-024-experience.md",
)

#: A surplus that is deliberately **not** additive in its drivers: mortality
#: and lapse both act on the same in-force, so improving one changes what
#: the other is worth. That interaction is the whole subject.
DRIVERS = ("mortality", "lapse", "interest")


def _surplus(state) -> float:
    survivors = 1000.0
    fund = 0.0
    for _ in range(20):
        survivors *= (1.0 - state["mortality"]) * (1.0 - state["lapse"])
        fund = (fund + survivors * 100.0) * (1.0 + state["interest"])
    return fund


def demonstrate() -> dict:
    expected = {"mortality": 0.010, "lapse": 0.08, "interest": 0.030}
    actual = {"mortality": 0.008, "lapse": 0.05, "interest": 0.042}

    def evaluate(chosen):
        return _surplus({name: (actual if name in chosen else expected)[name]
                         for name in DRIVERS})

    orders = {}
    for order in permutations(DRIVERS):
        decomposition = sequential(evaluate, list(order))
        orders[" -> ".join(order)] = {
            name: float(value)
            for name, value in decomposition.contributions.items()
        }

    fair = shapley(evaluate, list(DRIVERS))
    alone = isolated(evaluate, list(DRIVERS))
    spread = contribution_range(evaluate, list(DRIVERS))
    sensitivity = order_sensitivity(evaluate, list(DRIVERS))

    per_driver = {
        name: {
            "lowest_over_all_orders": min(o[name] for o in orders.values()),
            "highest_over_all_orders": max(o[name] for o in orders.values()),
            "shapley": float(fair.contributions[name]),
        }
        for name in DRIVERS
    }
    for name, row in per_driver.items():
        row["spread"] = row["highest_over_all_orders"] - row["lowest_over_all_orders"]

    return {
        "total_surplus": evaluate(set(DRIVERS)) - evaluate(set()),
        "by_order": orders,
        "per_driver": per_driver,
        "isolated_residual": float(alone.residual),
        "contribution_range": {k: [float(v) for v in vs]
                               for k, vs in spread.items()},
        "order_sensitivity": {k: float(v) for k, v in sensitivity.items()},
        "orders_evaluated": len(orders),
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
