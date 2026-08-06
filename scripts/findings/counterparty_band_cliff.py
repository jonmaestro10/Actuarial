"""Article 200's lower band boundary is a cliff; the upper one is not.

Run: ``python scripts/findings/counterparty_band_cliff.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np

from engine.report.counterparty import probability_of_default, type_1_capital
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="counterparty-band-cliff",
    claim=("Solvency II Article 200's lower band boundary moves the capital "
           "requirement by 14 percentage points of total loss-given-default "
           "— a 66.7% jump — for an arbitrarily small change in the book."),
    source="docs/rfc-028-counterparty.md",
    regulation=("Commission Delegated Regulation (EU) 2015/35, Article 200, "
                "consolidated 02015R0035 — EN — 30.07.2020 — 007.001"),
)


def demonstrate() -> dict:
    """Walk a real book across the boundary, one counterparty at a time.

    Equal credit quality step 4 counterparties sharing 1,000 of
    loss-given-default. Adding counterparties lowers the standard deviation
    of the loss distribution — diversification — so the book drifts *down*
    across the 7% boundary and the requirement drops discontinuously.
    """
    total_lgd = 1000.0
    rows = []
    for n in range(30, 46):
        pd = probability_of_default([4] * n)
        result = type_1_capital(pd, np.full(n, total_lgd / n))
        rows.append({
            "counterparties": n,
            "sigma_over_lgd": float(result.sigma / result.total_lgd),
            "capital": float(result.capital),
            "multiple_of_sigma": float(result.capital / result.sigma),
        })

    # The step: the last row at 3x and the first at 5x, or the reverse.
    jumps = [
        (a, b) for a, b in zip(rows, rows[1:])
        if round(a["multiple_of_sigma"], 6) != round(b["multiple_of_sigma"], 6)
    ]
    before, after = jumps[0]
    return {
        "walk": rows,
        "boundary_crossed_between": (before["counterparties"],
                                     after["counterparties"]),
        "sigma_over_lgd_either_side": (before["sigma_over_lgd"],
                                       after["sigma_over_lgd"]),
        "capital_either_side": (before["capital"], after["capital"]),
        "relative_jump": abs(before["capital"] - after["capital"])
                         / min(before["capital"], after["capital"]),
        "lower_boundary_gap_as_fraction_of_lgd": 5 * 0.07 - 3 * 0.07,
        "upper_boundary_gap_as_fraction_of_lgd": 1.0 - 5 * 0.20,
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
