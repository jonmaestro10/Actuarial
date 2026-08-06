"""A float assumption is not the number the actuary wrote, and the error is
there before any arithmetic happens.

Run: ``python scripts/findings/representation_error.py``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decimal import Decimal, localcontext

from engine.core import runner
from engine.core.exact import (
    EXACT_CONTEXT,
    Exact,
    agreement,
    as_stored,
    as_written,
    run_exact,
)
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="representation-error",
    claim=("0.035 is stored as 0.03500000000000000333..., so a basis has an "
           "error before any arithmetic runs. Read as written it is exactly "
           "3.5%, and the two readings diverge from the 17th digit onward."),
    source="docs/rfc-051-exact-decimal.md",
)


def demonstrate() -> dict:
    rate = 0.035
    points = [ModelPoint(age_at_entry=50, defer_years=10, premium=100_000.0,
                         annual_payment=9_000.0, init_pols=1)]
    basis = Assumptions(mortality=MortalityTable.flat(0.015), interest=rate,
                        crediting_rate=0.02)
    names = ["pols_if", "v", "payments"]

    approx = runner.run(FixedAnnuity, points, basis, 40, names)
    written = run_exact(FixedAnnuity, points, basis, 40, names)
    stored = run_exact(FixedAnnuity, points, basis, 40, names,
                       reader=as_stored)

    with localcontext(EXACT_CONTEXT):
        chain_written = (Exact(1) + as_written(rate)) ** 40
        chain_stored = (Decimal(1) + as_stored(rate)) ** 40

    report = agreement(approx, written)
    return {
        "assumption": rate,
        "as_written": str(as_written(rate)),
        "as_stored": str(as_stored(rate)),
        "compounded_40_years": {
            "as_written": str(chain_written),
            "as_stored": str(chain_stored),
            "relative_gap": float(abs(chain_written - chain_stored)
                                  / chain_written),
        },
        "float_engine_vs_34_digit_decimal": {
            "worst_relative": float(report["worst_relative"]),
            "worst_relative_at": report["worst_relative_at"],
            "values_compared": report["values_compared"],
        },
        "representation_error_alone": {
            "discount_factor_at_40": {
                "as_written": str(written.per_mp[0]["v"][40]),
                "as_stored": str(stored.per_mp[0]["v"][40]),
            },
        },
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
