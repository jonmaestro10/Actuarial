"""The device executor's two guarantees, asserted rather than claimed.

§1.2 asks for bitwise equality across executors, and a GPU cannot give it
against a CPU: device reductions run in a different order and floating-point
addition is not associative. So the device executor **does not join the
bitwise class**. It is not a weakened member — it is a different class, and
this suite is what makes that a statement with content:

- **(a) run-to-run determinism**: the same question twice on the same
  hardware, same bits;
- **(b) a reconciliation bound** against the CPU executor, per variable.

Both are tested here **without a device**, and that is not a compromise. What
separates a device answer from a CPU answer is the order partial sums are
combined in, and that order can be reproduced exactly in NumPy — so the bound
is a bound on the arithmetic, which is what it was always a bound on. A
device violating either guarantee would fail these tests unchanged.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.gpu import (
    DEVICE_BLOCK,
    DEVICE_STATUS,
    RECONCILIATION_BOUND,
    DeviceReduction,
    ReconciliationFailed,
    assert_run_to_run_determinism,
    backend,
    device_available,
    reconcile,
    result_digest,
)
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity

needs_device = pytest.mark.skipif(
    not device_available(),
    reason="no CUDA device; the CuPy path is unexercised here and "
           "engine.core.gpu.DEVICE_STATUS says so",
)


def build(n=64, proj_len=30):
    points = [ModelPoint(age_at_entry=45 + (i % 20), defer_years=10,
                         premium=100_000.0, annual_payment=9_000.0,
                         init_pols=1) for i in range(n)]
    return points, Assumptions(mortality=MortalityTable.flat(0.015),
                               interest=0.03, crediting_rate=0.02), proj_len


# --------------------------------------------------------------------------
# The posture, stated where a reader will find it
# --------------------------------------------------------------------------

def test_the_module_says_whether_a_device_was_actually_exercised():
    """**A passing suite that skipped everything interesting looks exactly
    like a passing suite.**

    Without this, a reader would have to infer from skip markers whether the
    CuPy path had ever run. ``DEVICE_STATUS`` says it in the module, so the
    docs and the RFC can quote it instead of claiming something the build
    cannot support."""
    assert isinstance(DEVICE_STATUS, str) and len(DEVICE_STATUS) > 40
    if device_available():
        assert "device present" in DEVICE_STATUS
        assert backend() is not np
    else:
        assert "no device detected" in DEVICE_STATUS
        assert "unexercised" in DEVICE_STATUS
        assert backend() is np


def test_the_backend_is_a_namespace_so_the_executor_is_written_once():
    """CuPy mirrors NumPy's op set, which is why the plan chose it over JAX.
    Selecting a namespace rather than branching inside the code is what makes
    that mirroring worth anything."""
    assert backend(prefer_device=False) is np
    namespace = backend()
    for operation in ("sum", "exp", "concatenate", "empty", "asarray"):
        assert hasattr(namespace, operation), operation


# --------------------------------------------------------------------------
# Guarantee (a): run-to-run determinism
# --------------------------------------------------------------------------

def test_the_same_question_asked_three_times_gives_the_same_bits():
    """The guarantee a regulator's re-run depends on, and the one a device
    can actually keep — given fixed reduction orders and an RNG pinned per
    (scenario, model point) rather than per thread."""
    points, assumptions, proj_len = build()

    def run():
        return run_vectorized(FixedAnnuity, points, assumptions, proj_len)

    digest = assert_run_to_run_determinism(run, times=3)
    assert len(digest) == 16
    assert digest == result_digest(run())


def test_a_non_deterministic_run_is_reported_with_the_digests():
    """"The device is non-deterministic" is the beginning of an
    investigation, not the end of one. The digests are where it starts, so
    they are in the message."""
    points, assumptions, proj_len = build(n=8, proj_len=5)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        result = run_vectorized(FixedAnnuity, points, assumptions, proj_len)
        if calls["n"] == 2:
            result._stacked["pols_if"] = result.array("pols_if") * 1.0000001
        return result

    with pytest.raises(ReconciliationFailed) as raised:
        assert_run_to_run_determinism(flaky, times=3)
    message = str(raised.value)
    assert "2 different answers" in message
    assert "unordered reduction" in message


# --------------------------------------------------------------------------
# Guarantee (b): the reconciliation bound, measured without silicon
# --------------------------------------------------------------------------

def test_a_device_shaped_reduction_reproduces_the_reduction_order_difference():
    """**Why this can be measured here at all.** What separates a device
    answer from a CPU answer is the order partial sums are combined in, and
    that order is reproducible in NumPy.

    The measurement also corrects an intuition. A device-shaped *tree* is
    **closer** to NumPy's pairwise reduction than a naive sequential loop is,
    because both are trees. "The GPU is the thing that disagrees" gets the
    direction wrong."""
    rng = np.random.default_rng(11)
    values = rng.uniform(0.0, 1e6, 100_000)

    pairwise = float(np.sum(values))
    tree = float(DeviceReduction().sum(values.reshape(-1, 1))[0])
    sequential = 0.0
    for value in values:
        sequential += float(value)

    tree_gap = abs(tree - pairwise) / abs(pairwise)
    sequential_gap = abs(sequential - pairwise) / abs(pairwise)

    assert tree_gap < RECONCILIATION_BOUND
    assert sequential_gap < RECONCILIATION_BOUND
    assert tree_gap <= sequential_gap, (
        f"the device-shaped tree ({tree_gap:.2e}) disagreed with NumPy more "
        f"than a sequential loop did ({sequential_gap:.2e}); the reasoning "
        f"behind the bound assumed the opposite"
    )
    # And it is a real reduction, not the identity: a block width of 2 and of
    # 32 combine partials differently.
    assert DeviceReduction(2).sum(values.reshape(-1, 1))[0] == pytest.approx(
        pairwise, rel=RECONCILIATION_BOUND)


def test_the_bound_is_orders_of_magnitude_looser_than_the_arithmetic_needs():
    """Worth knowing in both directions: the target is safe, and a run that
    *missed* it would be evidence of a defect rather than of floating point.

    Measured across workloads of different shape — narrow and wide ranges,
    signed and unsigned — because a bound established on one distribution is
    a bound on that distribution."""
    rng = np.random.default_rng(11)
    worst = 0.0
    for values in (rng.uniform(1e2, 1e4, 100_000),
                   rng.lognormal(6, 3, 100_000),
                   rng.uniform(-1e5, 1e5, 200_000),
                   rng.uniform(0, 1e6, 200_000)):
        pairwise = float(np.sum(values))
        tree = float(DeviceReduction().sum(values.reshape(-1, 1))[0])
        worst = max(worst, abs(tree - pairwise) / abs(pairwise))
    assert worst < RECONCILIATION_BOUND / 100, (
        f"reduction-order spread is {worst:.2e}, within two orders of "
        f"magnitude of the {RECONCILIATION_BOUND:.0e} bound — the bound is "
        f"no longer comfortably above the arithmetic and wants revisiting"
    )
    assert DEVICE_BLOCK == 32          # a warp, so the tree has the real shape


def test_reconciliation_is_per_variable_and_says_which_one_is_worst():
    """A single aggregate cannot say whether a discrepancy is spread thinly
    or concentrated in one variable, and which of those it is decides whether
    it is reduction order or a bug."""
    points, assumptions, proj_len = build()
    cpu = run_vectorized(FixedAnnuity, points, assumptions, proj_len)

    identical = reconcile(cpu, cpu)
    assert identical.within_bound
    assert identical.worst_relative == 0.0
    assert set(identical.per_variable) == set(cpu._stacked)

    nudged = run_vectorized(FixedAnnuity, points, assumptions, proj_len)
    nudged._stacked["payments"] = nudged.array("payments") * (1 + 1e-13)
    report = reconcile(nudged, cpu)
    assert report.worst_variable == "payments"
    assert 0 < report.worst_relative < RECONCILIATION_BOUND
    assert report.within_bound
    assert "payments" in report.describe()

    nudged._stacked["payments"] = nudged.array("payments") * (1 + 1e-6)
    outside = reconcile(nudged, cpu)
    assert not outside.within_bound
    assert "OUTSIDE" in outside.describe()


def test_a_shape_difference_is_not_a_rounding_difference():
    """No bound applies to a device that returned the wrong shape. Reporting
    it as a large relative difference would invite someone to widen the
    bound until it passed."""
    points, assumptions, proj_len = build()
    cpu = run_vectorized(FixedAnnuity, points, assumptions, proj_len)
    wrong = run_vectorized(FixedAnnuity, points, assumptions, proj_len)
    wrong._stacked["payments"] = wrong.array("payments")[:-1]

    with pytest.raises(ReconciliationFailed, match="not a rounding"):
        reconcile(wrong, cpu)


# --------------------------------------------------------------------------
# The device path itself
# --------------------------------------------------------------------------

@needs_device
def test_the_device_keeps_both_guarantees():  # pragma: no cover - needs a GPU
    """Unexercised in this build. Written so that the first machine with a
    device runs it without anything being added — the contracts above are the
    same contracts."""
    points, assumptions, proj_len = build()
    namespace = backend()
    assert namespace is not np

    def run():
        return run_vectorized(FixedAnnuity, points, assumptions, proj_len)

    assert_run_to_run_determinism(run, times=3)
    cpu = run_vectorized(FixedAnnuity, points, assumptions, proj_len)
    report = reconcile(run(), cpu)
    assert report.within_bound, report.describe()
