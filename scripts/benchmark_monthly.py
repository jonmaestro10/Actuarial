"""Benchmark: a monthly payout-annuity block on the VPLA basis.

The term-life benchmark (scripts/benchmark.py) measures the executors
against each other on an annual toy template. This one measures the shape
of work PLAN.md §4 actually cares about: a block of annuitants projected
monthly for 60 years on fractional-age mortality with a generational
improvement scale — 720 periods per policy, every one of them a calendar
calculation.

Usage: python scripts/benchmark_monthly.py [n_annuitants] [years]

It also sweeps the executor's chunk size. Chunking changes no number — model
points are independent, so a chunked run is bitwise identical — but it
decides whether the working set lives in cache or in main memory, which on
this workload is worth roughly 3-4x.
"""

import sys
import time
from datetime import date

import numpy as np

from engine.core.vector import default_chunk_size, run_vectorized
from engine.data.basis import ValuationBasis
from engine.data.modelpoints import ModelPoint
from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve
from engine.library.payout_annuity import PayoutAnnuity

MIN_AGE, MAX_AGE = 18, 115
YEAR_START = 2014
VALUATION = date(2021, 1, 1)


def build_basis(freq):
    rates = {
        sex: {
            age: min(
                0.0004 * 1.09 ** (age - MIN_AGE) * (1.0 if sex == "M" else 0.85),
                1.0,
            )
            for age in range(MIN_AGE, MAX_AGE + 1)
        }
        for sex in ("M", "F")
    }
    generational = {
        sex: {
            year: {
                age: 0.008 + 0.00001 * age
                for age in range(MIN_AGE, MAX_AGE + 1)
            }
            for year in range(YEAR_START + 1, YEAR_START + 17)
        }
        for sex in ("M", "F")
    }
    return ValuationBasis(
        mortality=MortalityBasis(rates, year_start=YEAR_START,
                                 improvement=generational),
        curve=YieldCurve([0.04], freq=freq),
    )


def build_block(n):
    rng = np.random.default_rng(20260804)
    points = []
    for i in range(n):
        born = date(
            1940 + int(rng.integers(0, 30)), int(rng.integers(1, 13)),
            int(rng.integers(1, 29)),
        )
        spouse = date(
            1945 + int(rng.integers(0, 25)), int(rng.integers(1, 13)),
            int(rng.integers(1, 29)),
        )
        points.append(
            ModelPoint(
                id=i, dob=born, sex="M" if i % 2 else "F", valuation=VALUATION,
                annual_payment=12_000.0, init_lives=1, certain_years=0.0,
                joint_percent=0.6 if i % 3 else 0.0,
                spouse_dob=spouse, spouse_sex="F" if i % 2 else "M",
            )
        )
    return points


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25_000
    years = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    freq = 12
    n_periods = years * freq
    basis = build_basis(freq)
    points = build_block(n)
    outputs = ["payments", "v"]

    print(f"annuitants: {n:,} | {years} years monthly = {n_periods:,} periods "
          f"| cells: {n * n_periods:,}")
    print(f"basis: fractional-age UDD, generational improvement, "
          f"reversionary benefits on {sum(1 for p in points if p.joint_percent):,}")
    print()

    default = default_chunk_size(n_periods)
    baseline = None
    for chunk in (n, 4 * default, default, default // 4 or 1):
        start = time.perf_counter()
        result = run_vectorized(
            PayoutAnnuity, points, basis, proj_len=n_periods - 1,
            outputs=outputs, chunk_size=chunk,
        )
        elapsed = time.perf_counter() - start
        payments = result.array("payments")
        if baseline is None:
            baseline, reference = elapsed, payments
            note = "one block"
        else:
            assert np.array_equal(payments, reference), "chunking moved a number"
            note = f"{baseline / elapsed:.1f}x vs one block, bitwise identical"
        label = "default" if chunk == default else ""
        print(f"chunk {min(chunk, n):>7,} {label:<8} {elapsed:7.2f} s "
              f"({elapsed / n * 1000:6.3f} ms/annuitant)   {note}")

    pv = float(np.sum(result.array("payments") * result.array("v")))
    print()
    print(f"total present value: {pv:,.2f}")


if __name__ == "__main__":
    main()
