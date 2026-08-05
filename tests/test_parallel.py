"""Sharding a block across worker processes.

PLAN §4.3 asks for batches sharded across cores and nodes with results
reduced as streaming aggregations; §8 lists multi-node scale-out among the
Phase 2 exits. This is the sharding and the reduction, across cores on one
machine.

The hard part of scale-out is not the dispatch. It is deciding what may be
split, proving the split cannot move a number, and reducing the pieces in an
order that does not depend on which finished first. That is what this file
pins:

**A shard is a chunk in another process.** Model points are independent, so
per-policy results are bitwise identical for any number of workers.

**Except where they are not independent.** A ``@pool`` variable reduces
across the model-point axis, so a reduction over a shard would be a
reduction over the wrong population. Refused, not silently run.

**Totals regroup, and the size of that is stated.** Summing shards and then
summing the shard totals is a different association from summing the block,
so a four-worker total can differ from a two-worker total in the last bit.

The measurement that shaped the module is in RFC-008: shipping per-policy
series back is a *loss* at every size, because the results are the payload.
Reducing in the worker is 2.3x on four cores.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.parallel import (
    MIN_PARALLEL_CELLS,
    default_workers,
    reduce_totals,
    run_parallel,
    run_parallel_totals,
    shard_bounds,
)
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.basis import ValuationBasis
from engine.data.modelpoints import from_dicts
from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve
from engine.library.term_life import TermLife
from engine.library.variable_payout_annuity import VariablePayoutAnnuity

OUTPUTS = ["pols_if", "claims", "premiums", "profit_before_tax"]


def assumptions():
    return Assumptions(mortality=MortalityTable.flat(0.01), lapse=0.05,
                       interest=0.03, expense_per_policy=50.0)


def block(n=600):
    return from_dicts([
        {"id": f"T{i}", "age_at_entry": 30 + i % 40, "term_years": 25,
         "sum_assured": 100_000.0 + 37.0 * i, "annual_premium": 800.0,
         "init_pols": 1}
        for i in range(n)
    ])


# --- sharding ------------------------------------------------------------


def test_shards_are_contiguous_cover_everything_and_are_balanced():
    for n, workers in ((10, 4), (100, 7), (3, 8), (1, 1), (9, 3)):
        bounds = shard_bounds(n, workers)
        assert bounds[0][0] == 0
        assert bounds[-1][1] == n
        assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:]))
        sizes = [stop - start for start, stop in bounds]
        assert sum(sizes) == n
        assert max(sizes) - min(sizes) <= 1      # as even as they divide
        assert len(bounds) == min(workers, n)


def test_a_worker_count_below_one_raises():
    with pytest.raises(ValueError, match="must be >= 1"):
        shard_bounds(10, 0)
    with pytest.raises(ValueError, match="must be >= 1"):
        run_parallel(TermLife, block(4), assumptions(), 10, workers=0)


def test_the_default_is_one_worker_per_core():
    assert default_workers() >= 1


# --- a shard is a chunk in another process -------------------------------


@pytest.mark.parametrize("workers", [1, 2, 3, 4])
def test_per_policy_results_are_bitwise_for_any_worker_count(workers):
    """The claim that licenses sharding at all, and the same argument that
    licenses chunking: model points are independent, so evaluating one has
    no effect on any other."""
    points = block(600)
    reference = run_vectorized(TermLife, points, assumptions(), 30,
                               outputs=OUTPUTS)
    sharded = run_parallel(TermLife, points, assumptions(), 30,
                           outputs=OUTPUTS, workers=workers, min_cells=0)
    for name in OUTPUTS:
        assert np.array_equal(np.asarray(reference.array(name)),
                              np.asarray(sharded.array(name))), name


def test_the_model_points_come_back_in_their_original_order():
    """Reassembled by index, never by completion order — otherwise the
    result of a run would depend on which worker happened to finish
    first."""
    points = block(600)
    sharded = run_parallel(TermLife, points, assumptions(), 30,
                           outputs=["claims"], workers=4, min_cells=0)
    assert sharded.mp_ids == [f"T{i}" for i in range(600)]
    # Sum assured rises with index, so claims must too at a fixed period.
    claims = np.asarray(sharded.array("claims"))[5]
    assert np.all(np.diff(claims) > 0)


def test_a_small_block_stays_in_process():
    """Sending a shard costs a pickle of the model points and the
    assumptions. Below the threshold that is more work than the projection,
    so it is not done."""
    points = block(50)
    assert 50 * 31 < MIN_PARALLEL_CELLS
    reference = run_vectorized(TermLife, points, assumptions(), 30,
                               outputs=OUTPUTS)
    stayed = run_parallel(TermLife, points, assumptions(), 30,
                          outputs=OUTPUTS, workers=4)
    for name in OUTPUTS:
        assert np.array_equal(np.asarray(reference.array(name)),
                              np.asarray(stayed.array(name)))


# --- what may not be sharded ---------------------------------------------


def pooled_setup():
    import datetime as dt

    basis = ValuationBasis(
        mortality=MortalityBasis(
            {"M": {a: min(0.0005 * 1.1 ** (a - 20), 1.0)
                   for a in range(20, 121)}},
            year_start=2020, use_improvement=False,
        ),
        curve=YieldCurve([0.03], freq=1),
    )
    points = from_dicts([
        {"id": f"M{i}", "dob": dt.date(1950 + i, 1, 1), "sex": "M",
         "valuation": dt.date(2021, 1, 1), "pension": 1_200.0,
         "account_value": 15_000.0, "init_lives": 1}
        for i in range(6)
    ])
    return points, basis


def test_a_pooled_model_is_refused_rather_than_sharded():
    """A reduction over a shard reduces over the wrong population, and
    produces plausible numbers while doing it. That is the case worth
    refusing rather than warning about."""
    points, basis = pooled_setup()
    for runner in (run_parallel, run_parallel_totals):
        with pytest.raises(ValueError, match="couples its model points"):
            runner(VariablePayoutAnnuity, points, basis, 20, workers=2)


def test_a_model_that_declares_coupling_is_refused_too():
    class Coupled(TermLife):
        couples_model_points = True

    with pytest.raises(ValueError, match="couples its model points"):
        run_parallel(Coupled, block(4), assumptions(), 10, workers=2)


# --- totals, and the regrouping ------------------------------------------


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_block_totals_match_a_single_process_run(workers):
    points = block(600)
    reference = run_vectorized(TermLife, points, assumptions(), 30,
                               outputs=OUTPUTS)
    totals = run_parallel_totals(TermLife, points, assumptions(), 30,
                                 outputs=OUTPUTS, workers=workers,
                                 min_cells=0)
    for name in OUTPUTS:
        expected = np.asarray(reference.array(name)).sum(axis=1)
        assert totals[name].shape == (31,)
        assert np.allclose(totals[name], expected, rtol=1e-14, atol=0)


def test_totals_are_reproducible_for_a_fixed_worker_count():
    """Which is the guarantee that can be made. Shards are contiguous and
    reduced in shard order, so the same worker count gives the same answer
    every time — bitwise."""
    points = block(600)
    first = run_parallel_totals(TermLife, points, assumptions(), 30,
                                outputs=OUTPUTS, workers=3, min_cells=0)
    again = run_parallel_totals(TermLife, points, assumptions(), 30,
                                outputs=OUTPUTS, workers=3, min_cells=0)
    for name in OUTPUTS:
        assert np.array_equal(first[name], again[name])


def test_a_change_of_worker_count_may_move_a_total_by_an_ulp():
    """Stated rather than discovered. Summing shards and then summing the
    shard totals is a different association from summing the block, so the
    result can differ in the last bit — at machine epsilon, and often not at
    all, but it is a difference and RFC-003's determinism claim needs the
    worker count recorded beside it."""
    points = block(600)
    totals = {
        workers: run_parallel_totals(TermLife, points, assumptions(), 30,
                                     outputs=OUTPUTS, workers=workers,
                                     min_cells=0)
        for workers in (1, 2, 3, 4)
    }
    reference = totals[1]
    for workers, other in totals.items():
        for name in OUTPUTS:
            scale = max(float(np.abs(reference[name]).max()), 1e-30)
            worst = float(np.abs(other[name] - reference[name]).max()) / scale
            assert worst < 1e-14, f"{name} at {workers} workers: {worst}"


def test_reducing_sums_in_shard_order():
    parts = [
        {"a": np.array([1.0, 2.0]), "b": np.array([10.0, 20.0])},
        {"a": np.array([0.5, 0.25]), "b": np.array([1.0, 2.0])},
    ]
    totals = reduce_totals(parts)
    assert np.array_equal(totals["a"], np.array([1.5, 2.25]))
    assert np.array_equal(totals["b"], np.array([11.0, 22.0]))


def test_reducing_nothing_raises():
    with pytest.raises(ValueError, match="no shard results"):
        reduce_totals([])
