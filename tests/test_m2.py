"""Milestone M2's claim, asserted rather than published.

M2 says: publish the nested-stochastic numbers with the bitwise-reproducibility
statement no incumbent can make. A published claim is worth what its test is
worth, so both halves are here — the agreement, and the refusal that makes the
agreement mean something.

The wording matters and is checked. The original was "bitwise for 1 machine or
N, **any topology**", and RFC-075 measured that false: `np.exp` is not
bit-portable across microarchitectures, so agreement has to be *checked* rather
than assumed. What survives is narrower and still unmatched — bitwise across
workers that attest the same arithmetic, and refused otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _script():
    path = ROOT / "scripts" / "benchmark_m2.py"
    spec = importlib.util.spec_from_file_location("_benchmark_m2", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m2 = _script()


# --------------------------------------------------------------------------
# The agreement
# --------------------------------------------------------------------------

def test_a_nested_valuation_is_bitwise_across_a_model_point_split():
    """**The property M2's claim rests on, and nothing had measured it.**

    RFC-075 established that a *flat* projection reduces bitwise across
    shards. A nested stochastic valuation is a different workload — inner
    projections launched per outer state per valuation date — and whether it
    shards without moving a bit was assumed rather than checked.

    Small enough to run on every commit. The shape is what is being asserted,
    not the scale: if splitting the block moved a bit it would move it here.
    """
    points = m2.block(12)
    whole = np.asarray(m2.valuation(points, 6, 16, every=8).values)
    halves = [m2.valuation(points[:6], 6, 16, every=8),
              m2.valuation(points[6:], 6, 16, every=8)]
    reduced = np.concatenate([np.asarray(p.values) for p in halves], axis=1)

    assert reduced.shape == whole.shape, "the split changed the shape"
    assert reduced.dtype == whole.dtype, "the split changed the dtype"
    assert np.array_equal(reduced.view(np.int64), whole.view(np.int64)), (
        "a nested valuation split by model point is not bitwise identical to "
        "the same block run whole; M2's claim does not hold for this workload"
    )


# --------------------------------------------------------------------------
# The refusal, which is the half worth having
# --------------------------------------------------------------------------

def test_workers_whose_arithmetic_differs_are_refused_rather_than_reduced():
    """**The claim no incumbent makes.**

    Anyone can promise agreement. This promises to *notice* disagreement and
    stop — which is the only version of the promise that survives `np.exp`
    not being bit-portable across microarchitectures.
    """
    from engine.core.dispatch import Attestation, DispatchReport, attest

    mine = attest()
    same = Attestation(exact=mine.exact, transcendental=mine.transcendental,
                       numpy=mine.numpy, machine="a-different-box")
    differs = Attestation(exact=mine.exact, transcendental="0" * 16,
                          numpy=mine.numpy, machine="a-different-box")

    # A different *machine* is fine; a different transcendental digest is not.
    # That distinction is the whole design: where a shard ran must not move a
    # number, and what its arithmetic does must.
    assert same.digest == mine.digest, (
        "the attestation digest includes the machine, so two identical "
        "workers on different hosts would refuse to reduce together"
    )
    assert differs.digest != mine.digest, (
        "a worker with different transcendental arithmetic attests the same "
        "as one that agrees, so dispatch could not tell them apart"
    )

    agreeing = DispatchReport(attestations={0: mine, 1: same})
    mismatched = DispatchReport(attestations={0: mine, 1: differs})
    assert agreeing.workers_agreed
    assert not mismatched.workers_agreed, (
        "dispatch would reduce across workers whose arithmetic differs; the "
        "refusal that makes M2's claim worth making is not firing"
    )


def test_the_published_claim_says_attest_and_not_any_topology():
    """Guards the wording reverting to the one measurement refuted.

    "Bitwise for 1 machine or N, any topology" is the original and is false.
    It is also the more impressive sentence, which is exactly why it needs a
    test — the pressure is always toward the stronger claim.
    """
    text = (ROOT / "scripts" / "benchmark_m2.py").read_text(encoding="utf-8")
    assert "attest the same arithmetic" in text
    assert "REFUSES to reduce" in text
    assert "any topology" in text and "measured it false" in text, (
        "the script no longer records that 'any topology' was the original "
        "claim and was refuted; without that, a reader cannot tell a narrowed "
        "claim from a weak one"
    )
