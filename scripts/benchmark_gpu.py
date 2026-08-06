"""The device workload, and what this machine can honestly say about it.

Run: ``python scripts/benchmark_gpu.py``

B3 targets the **stochastic and nested-stochastic slabs** — the scenario
dimension — and not the deterministic single-scenario path, which B1 already
serves. This script measures that workload and reports either device numbers
or, on a machine without one, the reduction-order spread that sets the
reconciliation bound.

It prints what it could not measure. A benchmark that silently omitted the
device row on a CPU-only machine would read as though the device were slow.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from engine.core.gpu import (
    DEVICE_STATUS,
    RECONCILIATION_BOUND,
    DeviceReduction,
    backend,
    device_available,
)


def scenario_slab(n_scenarios: int, n_policies: int, n_periods: int):
    """A stochastic slab of the shape the device executor exists for.

    Lognormal and signed rather than uniform-positive. A slab of similar
    positive numbers reduces to the same bits in any order, so benchmarking
    on one would report a spread of exactly zero and prove nothing — the
    cancellation between large opposite-signed partials is where reduction
    order actually shows.
    """
    rng = np.random.default_rng(20260806)
    shape = (n_periods, n_policies, n_scenarios)
    return rng.lognormal(6.0, 2.5, shape) * rng.choice([-1.0, 1.0], shape)


def main() -> int:
    print(f"device status: {DEVICE_STATUS}\n")
    namespace = backend()

    print(f"{'workload':32} {'cells':>12} {'aggregate':>12} "
          f"{'tree vs pairwise':>18}")
    print("-" * 78)
    worst = 0.0
    for scenarios, policies, periods in ((100, 1_000, 40),
                                         (500, 2_000, 40),
                                         (1_000, 5_000, 40)):
        slab = scenario_slab(scenarios, policies, periods)
        cells = slab.size
        start = time.perf_counter()
        pairwise = float(np.sum(slab, axis=(1, 2)).sum())
        elapsed = time.perf_counter() - start
        flat = slab.reshape(-1, 1)
        tree = float(DeviceReduction().sum(flat)[0])
        gap = abs(tree - pairwise) / abs(pairwise)
        worst = max(worst, gap)
        print(f"{f'{scenarios} scen x {policies} pol x {periods}p':32} "
              f"{cells:>12,} {elapsed * 1000:>10.1f}ms {gap:>18.2e}")

    print("-" * 78)
    print(f"worst reduction-order spread: {worst:.2e}, against a "
          f"reconciliation bound of {RECONCILIATION_BOUND:.0e}")
    if worst == 0.0:
        print("the two orders agreed exactly on every workload here, which "
              "says the\nslab was too well behaved to be evidence rather "
              "than that the bound is safe\n")
    else:
        print(f"headroom: {RECONCILIATION_BOUND / worst:,.0f}x\n")

    if not device_available():
        print("NOT MEASURED on this machine:")
        print("  * device throughput against the compiled CPU executor")
        print("  * the reconciliation bound against real silicon")
        print("  * run-to-run determinism on a device")
        print("\nThe contracts in engine/core/gpu.py are asserted against the")
        print("CPU backend and would fail unchanged on a device that broke")
        print("them; what is missing is the measurement, not the machinery.")
        return 0

    print(f"device backend: {namespace.__name__}")  # pragma: no cover
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
