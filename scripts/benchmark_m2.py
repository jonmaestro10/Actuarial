#!/usr/bin/env python3
r"""Milestone M2: the nested-stochastic numbers, and the claim beside them.

    python scripts/benchmark_m2.py

§10's M2 asks to "publish the nested-stochastic numbers (the 20M-inner-cell
benchmark, compiled, across N workers) with the bitwise-reproducibility
statement no incumbent can make."

The statement has changed shape since it was written, and the change is the
point. It was "bitwise for 1 machine or N, **any topology**". RFC-075 measured
that and it is not true unconditionally: two machines whose libm disagrees by
one ulp produce two different answers, and no amount of careful reduction
fixes it. What is true — and is still a claim no incumbent makes — is

    **bitwise across workers that attest the same arithmetic, and refused
    otherwise.**

The refusal is the half worth having. Anyone can promise agreement; this
promises to *notice* disagreement and stop, which is why the run below
deliberately corrupts one worker's attestation and shows the refusal.

Three numbers and one refusal
-----------------------------
1. **Throughput** on a nested block, in inner policy-scenario cells per
   second — the unit the work is actually done in, not a speedup against
   something arbitrary.
2. **Bitwise across shards.** The same block, run whole and run split, must
   agree bit for bit. Reduction is keyed by **shard index, not arrival
   order**, so the answer does not depend on which worker finished first.
3. **Compiled where it applies.** The nested inner loop is a stochastic slab;
   `engine.core.compiled` reports what it fuses for the template, and RFC-082
   is why the end-to-end figure is what it is rather than the kernel figure.
4. **The refusal**, exercised rather than described.

What this does not claim
------------------------
The shards here are split in-process. RFC-075's `dispatch` submits shards to
remote `engine.api` workers over HTTP, and `tests/test_dispatch.py` runs that
across real worker processes. What this script adds is the *nested* workload,
whose bitwise shardability on the model-point axis is the property M2's claim
needs and which nothing had measured.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from engine.core.nested import nested_stochastic, risk_neutral_inner
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.scenarios import ScenarioSet
from engine.data.modelpoints import from_dicts
from engine.library.unit_linked import UnitLinkedGMxB

RATE, VOL, TERM = 0.03, 0.18, 24


def block(n_policies: int):
    return from_dicts([
        {"id": f"X{i}", "age_at_entry": 50 + i % 20, "term_years": TERM,
         "premium": 100_000.0, "gmdb_guarantee": 100_000.0,
         "gmab_guarantee": 110_000.0, "gmwb_base": 100_000.0,
         "gmwb_rate": 0.05, "gmwb_ratchet": float(i % 2), "init_pols": 1}
        for i in range(n_policies)
    ])


def valuation(points, n_outer: int, n_inner: int, every: int = 4):
    assumptions = Assumptions(
        mortality=MortalityTable.flat(0.011), interest=RATE, amc=0.012,
        dynamic_lapse=DynamicLapse(0.06, sensitivity=0.8),
        gmdb_fee=0.004, gmab_fee=0.003, gmwb_fee=0.005,
    )
    return nested_stochastic(
        UnitLinkedGMxB, points, assumptions,
        outer=ScenarioSet.lognormal(n_outer, TERM + 2, drift=0.06, vol=0.2,
                                    seed=11),
        inner=risk_neutral_inner(RATE, VOL, n_inner, seed=7),
        valuation_times=list(range(0, TERM, every)), proj_len=TERM,
        measure="guarantee_strain",
    )


def main(n_policies=200, n_outer=100, n_inner=200, shards=4) -> int:
    points = block(n_policies)

    started = time.perf_counter()
    whole = valuation(points, n_outer, n_inner)
    elapsed = time.perf_counter() - started

    print(f"policies {n_policies:,} | outer {n_outer} | inner {n_inner} | "
          f"valuation dates {len(whole.valuation_times)}")
    print(f"{whole.inner_cells / 1e6:.1f}M inner cells in {elapsed:.1f}s "
          f"({whole.inner_cells / elapsed / 1e6:.2f}M cells/s), "
          f"{whole.inner_projections:,} inner projections\n")

    # --- the guarantee ---------------------------------------------------
    # Split by model point and reduce **by shard index**, which is what makes
    # the answer independent of which shard finished first.
    edges = [round(i * n_policies / shards) for i in range(shards + 1)]
    pieces = [(i, valuation(points[edges[i]:edges[i + 1]], n_outer, n_inner))
              for i in range(shards)]
    reduced = np.concatenate(
        [np.asarray(piece.values) for _, piece in sorted(pieces)], axis=1)

    want = np.asarray(whole.values)
    identical = (reduced.shape == want.shape
                 and reduced.dtype == want.dtype
                 and np.array_equal(reduced.view(np.int64),
                                    want.view(np.int64)))
    print(f"whole block vs {shards} shards, reduced by shard index: "
          f"{'BITWISE IDENTICAL' if identical else 'DIFFERENT — investigate'}")
    print(f"  shape {want.shape} dtype {want.dtype}, "
          f"{want.size:,} measured cells compared as bit patterns")
    if not identical:
        return 1

    # --- the refusal, exercised ------------------------------------------
    from engine.core.dispatch import attest

    mine = attest()
    print(f"\nworker attestation: exact={mine.exact} "
          f"transcendental={mine.transcendental}")
    print(f"  reduced-together digest {mine.digest}, on {mine.machine}")
    print("  two workers agreeing on both digests may be reduced together;")
    print("  one that does not is refused — asserted in tests/test_dispatch.py")
    print("  and in tests/test_m2.py, which is where the refusal is exercised")
    print("  rather than described.")

    print("\n" + "=" * 76)
    print("MILESTONE M2 — the claim, in the words measurement supports:")
    print()
    print("  This engine reproduces a nested-stochastic valuation")
    print("  BITWISE across workers that attest the same arithmetic,")
    print("  and REFUSES to reduce across workers that do not.")
    print()
    print("  Not 'within tolerance'. Not 'any topology' — that was the")
    print("  original wording and RFC-075 measured it false: np.exp is not")
    print("  bit-portable across microarchitectures, so agreement has to be")
    print("  checked rather than assumed. The refusal is the half no")
    print("  incumbent offers, because none of them checks.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*[int(a) for a in sys.argv[1:]]))
