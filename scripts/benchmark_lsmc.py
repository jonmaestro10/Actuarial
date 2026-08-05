"""Benchmark: an LSMC proxy against the exact nested valuation.

PLAN.md §4.4 admits proxy models "as an optional, clearly-labeled
acceleration with error estimates". This is the error estimate. It runs the
exact nested valuation, fits proxies at a range of settings, and prints what
each one costs and how far out it is — including how far out the *reference*
is, because a proxy cannot be measured to be better than the thing measuring
it.

Usage: python scripts/benchmark_lsmc.py [n_outer] [n_reference_inner]
"""

import sys
import time

from engine.core.lsmc import fit_proxy, proxy_error
from engine.core.nested import nested_stochastic, risk_neutral_inner
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.unit_linked import UnitLinkedGMxB

RATE, VOL, TERM = 0.03, 0.18, 20
TIMES = [4, 8, 12, 16]


def main(n_outer=400, n_reference=1000):
    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.011), interest=RATE, amc=0.012,
        dynamic_lapse=DynamicLapse(0.06, sensitivity=0.8),
        gmdb_fee=0.004, gmab_fee=0.003, gmwb_fee=0.005,
    )
    points = from_dicts([
        {"id": "X0", "age_at_entry": 55, "term_years": TERM, "premium": 1e5,
         "gmdb_guarantee": 1e5, "gmab_guarantee": 1.1e5, "gmwb_base": 1e5,
         "gmwb_rate": 0.05, "gmwb_ratchet": 1.0, "init_pols": 1},
    ])
    shared = dict(
        outer=ScenarioSet.lognormal(n_outer, TERM + 2, drift=0.06, vol=0.2,
                                    seed=11),
        valuation_times=TIMES, proj_len=TERM, measure="guarantee_strain",
    )

    started = time.perf_counter()
    exact = nested_stochastic(
        UnitLinkedGMxB, points, assumptions,
        inner=risk_neutral_inner(RATE, VOL, n_reference, seed=7), **shared)
    reference_seconds = time.perf_counter() - started
    second = nested_stochastic(
        UnitLinkedGMxB, points, assumptions,
        inner=risk_neutral_inner(RATE, VOL, n_reference, seed=99), **shared)

    print(f"reference: {exact.summary()}  [{reference_seconds:.1f} s]")
    noise = proxy_error(
        fit_proxy(UnitLinkedGMxB, points, assumptions,
                  inner=risk_neutral_inner(RATE, VOL, 5, seed=7), degree=3,
                  **shared),
        exact, second)["reference_noise"]
    print(f"the reference's own error, from a second run on another seed: "
          f"{noise * 100:.2f}% of the mean value\n")
    print(f"{'inner':>6s} {'degree':>7s} {'error':>8s} {'worst':>8s} "
          f"{'speedup':>9s} {'seconds':>8s}")
    for n_inner in (1, 2, 5, 20, 100):
        for degree in (2, 3):
            started = time.perf_counter()
            proxy = fit_proxy(
                UnitLinkedGMxB, points, assumptions,
                inner=risk_neutral_inner(RATE, VOL, n_inner, seed=7),
                degree=degree, **shared)
            elapsed = time.perf_counter() - started
            error = proxy_error(proxy, exact, second)
            flag = "  <- at the reference's own noise" if error[
                "at_measurement_floor"] else ""
            print(f"{n_inner:6d} {degree:7d} {error['relative'] * 100:7.2f}% "
                  f"{error['worst_relative'] * 100:7.2f}% "
                  f"{error['speedup']:8.0f}x {elapsed:7.2f}s{flag}")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
