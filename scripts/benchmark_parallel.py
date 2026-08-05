"""Benchmark: sharding a block across worker processes.

PLAN.md §4.3 asks for batches sharded across cores with results reduced as
streaming aggregations. This measures both halves of that sentence, because
only one of them pays.

Usage: python scripts/benchmark_parallel.py [max_workers]

The result to take away is that *where the reduction happens* decides
whether scale-out is worth anything on a single machine. Shipping per-policy
series back costs more than computing them; reducing in the worker does not.
"""

import sys
import time

import numpy as np

from engine.core.parallel import default_workers, run_parallel, run_parallel_totals
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts, to_batch
from engine.library.term_life import TermLife

OUTPUTS = ["pols_if", "claims", "premiums", "profit_before_tax"]
PERIODS = 60


def timed(fn, reps=3):
    fn()
    return min(
        (lambda t0: (fn(), time.perf_counter() - t0)[1])(time.perf_counter())
        for _ in range(reps)
    )


def main(max_workers=None):
    max_workers = max_workers or default_workers()
    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.01), lapse=0.05, interest=0.03,
        expense_per_policy=50.0,
    )
    print(f"{default_workers()} cores available, benchmarking up to "
          f"{max_workers} workers | {PERIODS} periods | "
          f"{len(OUTPUTS)} outputs\n")
    print(f"{'policies':>9s} {'serial':>9s} {'per-policy':>11s} "
          f"{'totals':>9s} {'per-policy':>11s} {'totals':>8s}")
    print(f"{'':9s} {'':9s} {'':11s} {'':9s} {'speedup':>11s} "
          f"{'speedup':>8s}")
    for n in (10_000, 40_000, 100_000):
        points = to_batch(from_dicts([
            {"id": i, "age_at_entry": 30 + i % 40, "term_years": 25,
             "sum_assured": 1e5, "annual_premium": 800.0, "init_pols": 1}
            for i in range(n)
        ]))
        serial = timed(lambda: run_vectorized(
            TermLife, points, assumptions, PERIODS, outputs=OUTPUTS))
        series = timed(lambda: run_parallel(
            TermLife, points, assumptions, PERIODS, outputs=OUTPUTS,
            workers=max_workers, min_cells=0))
        totals = timed(lambda: run_parallel_totals(
            TermLife, points, assumptions, PERIODS, outputs=OUTPUTS,
            workers=max_workers, min_cells=0))
        print(f"{n:9,d} {serial * 1000:8.0f}ms {series * 1000:10.0f}ms "
              f"{totals * 1000:8.0f}ms {serial / series:10.2f}x "
              f"{serial / totals:7.2f}x")

    print("\nper-policy sharding sends the results back through pipes, which "
          "costs\nmore than the arithmetic that produced them. Reducing in "
          "the worker sends\none number per period per output instead.")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
