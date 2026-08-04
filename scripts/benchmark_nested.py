"""Benchmark: a nested stochastic valuation of a GMxB block.

PLAN.md §4.4 calls nested stochastic "the real killer workload". This
measures it at a shape somebody might actually run, and reports the cost in
the unit that matters — inner policy-scenario cells — rather than in a
speedup against something arbitrary.

Usage: python scripts/benchmark_nested.py [n_policies] [n_outer] [n_inner]

The point to take away is the *shape* of the cost. Doubling the outer
scenarios doubles the work; doubling the valuation dates doubles it;
doubling the inner scenarios doubles it. What it does not do is multiply:
the outer states at one date are batched into a single projection, so the
number of projections is the number of valuation dates, not the number of
outer nodes.
"""

import sys
import time

import numpy as np

from engine.core.nested import nested_stochastic, risk_neutral_inner
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.unit_linked import UnitLinkedGMxB

RATE, VOL, TERM = 0.03, 0.18, 20


def main(n_policies=200, n_outer=100, n_inner=200):
    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.011), interest=RATE, amc=0.012,
        dynamic_lapse=DynamicLapse(0.06, sensitivity=0.8),
        gmdb_fee=0.004, gmab_fee=0.003, gmwb_fee=0.005,
    )
    points = from_dicts([
        {"id": f"X{i}", "age_at_entry": 50 + i % 20, "term_years": TERM,
         "premium": 100_000.0, "gmdb_guarantee": 100_000.0,
         "gmab_guarantee": 110_000.0, "gmwb_base": 100_000.0,
         "gmwb_rate": 0.05, "gmwb_ratchet": float(i % 2), "init_pols": 1}
        for i in range(n_policies)
    ])
    outer = ScenarioSet.lognormal(n_outer, TERM + 2, drift=0.06, vol=0.2,
                                  seed=11)
    times = list(range(0, TERM, 4))

    started = time.perf_counter()
    run = nested_stochastic(
        UnitLinkedGMxB, points, assumptions, outer=outer,
        inner=risk_neutral_inner(RATE, VOL, n_inner, seed=7),
        valuation_times=times, proj_len=TERM, measure="guarantee_strain",
    )
    elapsed = time.perf_counter() - started

    print(f"policies {n_policies:,} | outer {n_outer} | inner {n_inner} | "
          f"valuation dates {len(times)}")
    print(f"{run.summary()}")
    print(f"{run.inner_projections} inner projections in {elapsed:.2f} s "
          f"({run.inner_cells / elapsed / 1e6:.1f}M inner cells/s)\n")
    # The spread is taken *within* a model point, across outer paths. Taken
    # over the whole array it would also pick up the difference between a
    # 50-year-old and a 69-year-old, which is not path divergence and is not
    # zero at inception.
    print(" date   mean guarantee value   spread across outer   mean stderr")
    for t in times:
        values, errors = run.at(t), run.stderr[times.index(t)]
        print(f" {t:4d}   {values.mean():20,.0f}   "
              f"{np.ptp(values, axis=1).mean():19,.0f}   {errors.mean():11,.0f}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
