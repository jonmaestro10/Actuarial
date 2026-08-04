"""Benchmark: interpreted vs vectorized executor on a term-life block.

Usage: python scripts/benchmark.py [n_modelpoints] [proj_len]
"""

import sys
import time

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.library.term_life import TermLife


def build_block(n):
    return from_dicts(
        [
            {"id": f"T{i}", "age_at_entry": 25 + (i * 13) % 40,
             "term_years": 5 + (i * 7) % 35,
             "sum_assured": 50_000.0 + (i * 997) % 450_000,
             "annual_premium": 300.0 + (i * 31) % 2_500,
             "init_pols": 1}
            for i in range(n)
        ]
    )


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    proj_len = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    outputs = ["pols_if", "claims", "premiums", "expenses"]

    qx = {age: min(1.0, 0.0008 * 1.095 ** (age - 25)) for age in range(25, 121)}
    assumptions = Assumptions(
        mortality=MortalityTable(qx), lapse=0.03, interest=0.03,
        expense_per_policy=45.0,
    )
    mps = build_block(n)

    t0 = time.perf_counter()
    vec = run_vectorized(TermLife, mps, assumptions, proj_len, outputs=outputs)
    t_vec = time.perf_counter() - t0

    interp_n = min(n, 2_000)  # interpreter timed on a slice, scaled
    t0 = time.perf_counter()
    interp = run(TermLife, mps[:interp_n], assumptions, proj_len, outputs=outputs)
    t_interp_slice = time.perf_counter() - t0
    t_interp = t_interp_slice * (n / interp_n)

    for i in range(interp_n):
        for name in outputs:
            assert vec.per_mp[i][name] == interp.per_mp[i][name], "executor mismatch"

    print(f"model points: {n:,} | projection: {proj_len} years | vars: {len(outputs)}")
    print(f"interpreted : {t_interp:8.2f} s  ({t_interp_slice:.2f} s for {interp_n:,} mps, scaled)")
    print(f"vectorized  : {t_vec:8.2f} s")
    print(f"speedup     : {t_interp / t_vec:8.1f} x")


if __name__ == "__main__":
    main()
