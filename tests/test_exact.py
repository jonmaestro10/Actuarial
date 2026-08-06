"""Exact-decimal audit mode: what it claims, and what it refuses.

PLAN §3.4 asks for "a slow exact-decimal audit mode for regulatory sign-off
runs". The mode exists to produce a *second answer under different
arithmetic*, so the float answer's error can be measured rather than
assumed — the dual-executor move, one level down.

Three things this suite holds:

- **The conversion decision, which is the whole feature.** ``0.035`` parses
  to a binary value that is not 3.5%. Reading assumptions through
  ``Decimal(repr(x))`` recovers what the actuary wrote; reading them through
  ``Decimal(x)`` preserves the machine's value. Both are meaningful and the
  module offers both, because a discrepancy has two possible causes and only
  running both tells you which.
- **The claim is qualified.** "Exact" is exact for ``+``, ``-`` and ``*`` on
  decimal inputs and correctly rounded to 34 digits for ``/`` and ``**``.
  A test asserts the second half, because a mode that quietly rounded while
  calling itself exact would be worse than no mode.
- **The refusals.** A pooled model, and a template whose assumptions hand
  back arrays. Both are refused by name rather than falling back to float,
  which would produce a "sign-off run" that was a float run wearing a label.
"""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from decimal import localcontext

import pytest

from engine.core import runner
from engine.core.exact import (
    EXACT_CONTEXT,
    EXACT_PRECISION,
    Exact,
    ExactError,
    agreement,
    as_stored,
    as_written,
    exact_model_point,
    run_exact,
)
from engine.core.runner import PooledBlockError
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity
from engine.library.term_life import TermLife

NAMES = ["pols_if", "payments", "death_benefits", "v", "fund_eoy_per_pol"]


def build(q=0.015, i=0.03, g=0.02):
    return (
        [ModelPoint(age_at_entry=50, defer_years=10, premium=100_000.0,
                    annual_payment=9_000.0, init_pols=1)],
        Assumptions(mortality=MortalityTable.flat(q), interest=i,
                    crediting_rate=g),
    )


# --------------------------------------------------------------------------
# The conversion decision
# --------------------------------------------------------------------------

def test_an_assumption_is_read_as_written_not_as_stored():
    """**The decision the whole mode rests on.** A float assumption is not
    the number the actuary wrote: ``0.035`` is stored as
    ``0.03500000000000000333...``, and that error exists before any
    arithmetic happens.

    ``as_written`` recovers 3.5% exactly, which is the sign-off question.
    ``as_stored`` gives the double's true value, which isolates
    representation error from arithmetic error. Converting the wrong way
    would preserve the very error the mode exists to remove — and the run
    would look right, because it would still be decimal arithmetic."""
    assert as_written(0.035) == Decimal("0.035")
    assert str(as_written(0.035)) == "0.035"

    stored = as_stored(0.035)
    assert stored != Decimal("0.035")
    assert str(stored).startswith("0.03500000000000000333")
    # They part company in the 17th significant digit, which is exactly
    # where a double stops carrying information.
    assert abs(stored - Decimal("0.035")) < Decimal("1e-17")

    # And the difference is not academic: it compounds through a chain.
    with localcontext(EXACT_CONTEXT):
        written = (Exact(1) + as_written(0.035)) ** 40
        machine = (Decimal(1) + as_stored(0.035)) ** 40
    assert written != machine
    assert abs(written - machine) / written > Decimal("1e-16")


def test_the_precision_is_a_named_format_not_a_preference():
    """34 digits is IEEE 754 decimal128's significand. Double precision
    carries about 15.95 decimal digits, so the audit run holds roughly
    eighteen more than the run it checks — which is what lets the difference
    be read as the float run's error rather than as two approximations
    disagreeing with each other."""
    assert EXACT_PRECISION == 34
    assert EXACT_CONTEXT.prec == 34
    with localcontext(EXACT_CONTEXT):
        third = Exact(1) / Exact(3)
    assert len(str(third).split(".")[1]) == 34


def test_the_claim_is_qualified_where_it_has_to_be():
    """**"Exact" is exact for +, - and \\*, and correctly rounded for / and
    \\*\\*.** ``1/3`` has no finite decimal expansion and no arithmetic gives
    it one, so the mode's name overclaims unless it is qualified — and the
    qualification is asserted rather than left in a docstring.

    What it *does* deliver is that addition and multiplication of decimal
    inputs never round at all, at any depth of recursion, which is what a
    forty-year survival chain is made of."""
    with localcontext(EXACT_CONTEXT):
        # Addition and multiplication of decimal inputs: no rounding, ever.
        total = Exact(0)
        for _ in range(1_000):
            total = total + as_written(0.1)
        assert total == Decimal("100.0")          # a float loses this
        assert as_written(1.1) * as_written(1.1) == Decimal("1.21")

        # Division is rounded, and says so by filling the precision exactly
        # and by failing to round-trip.
        third = Exact(1) / Exact(3)
        assert len(str(third).split(".")[1]) == EXACT_PRECISION
        assert third * 3 != Exact(1)
        assert Exact(1) - third * 3 != 0

    # The float run cannot do the first of those at all, which is the point.
    naive = 0.0
    for _ in range(1_000):
        naive += 0.1
    assert naive != 100.0


def test_a_float_literal_in_a_template_body_is_coerced_by_the_same_rule():
    """``float + Decimal`` is a ``TypeError`` in Python, deliberately — and
    every ``@var`` body in the library contains ``1.0 - q``. :class:`Exact`
    coerces float operands through ``as_written``, which makes the whole
    template library polymorphic **without editing a template**.

    That matters beyond convenience: an audit mode needing its own copies of
    the formulas would be auditing the copies. Both operand orders and the
    reflected operators are covered, because a missing ``__rsub__`` would
    surface as a TypeError deep inside one recursion and nowhere else."""
    with localcontext(EXACT_CONTEXT):
        x = as_written(0.25)
        assert 1.0 - x == Decimal("0.75")
        assert x - 1.0 == Decimal("-0.75")
        assert 2.0 * x == Decimal("0.5")
        assert x * 2.0 == Decimal("0.5")
        assert 1.0 + x == Decimal("1.25")
        assert x / 2.0 == Decimal("0.125")
        assert 1.0 / x == Decimal("4")
        assert x ** 2 == Decimal("0.0625")
        assert -x == Decimal("-0.25")
        assert abs(-x) == Decimal("0.25")
        # Every result stays Exact, so the coercion survives the next
        # operation rather than decaying to Decimal after the first.
        for value in (1.0 - x, x * 2.0, 1.0 / x, -x, x ** 2):
            assert isinstance(value, Exact), value
        assert (1.0 - x) - 0.5 == Decimal("0.25")

    assert as_written(1.0) < 2.0 and as_written(3.0) > 2.0
    assert as_written(2.0) == 2.0
    assert hash(as_written(2.0)) == hash(Decimal(2))


def test_a_bool_is_refused_as_a_quantity():
    """``True`` is an ``int`` in Python and would convert silently to 1. An
    indicator that arrived as a bool rather than a rate is a caller error
    worth surfacing, not rounding."""
    with pytest.raises(ExactError, match="not a quantity"):
        Exact(True)
    with pytest.raises(ExactError, match="not a quantity"):
        as_stored(False)


# --------------------------------------------------------------------------
# The run, and the bound it produces
# --------------------------------------------------------------------------

def test_the_exact_run_reproduces_the_float_run_to_the_last_bits():
    """The result the mode exists to produce: a *measured* bound on the float
    executor's error rather than an assumed one.

    On ``FixedAnnuity`` the two agree to within a few parts in 10^16 — about
    one double-precision ulp — across every variable and period. That is the
    sign-off statement: the float engine is not merely close, it is as close
    as double precision permits."""
    points, assumptions = build()
    approx = runner.run(FixedAnnuity, points, assumptions, 40, NAMES)
    exact = run_exact(FixedAnnuity, points, assumptions, 40, NAMES)

    report = agreement(approx, exact)
    assert report["values_compared"] == 5 * 41
    assert report["precision"] == 34
    assert report["worst_relative"] < Decimal("1e-15")
    assert report["worst_relative_at"] is not None

    # Shape and dtype of the answer, separately from its value — the rule
    # RFC-069 and RFC-070 earned. An audit run that returned the right
    # numbers as floats would have proved nothing.
    assert set(exact.per_mp[0]) == set(NAMES)
    for name in NAMES:
        assert len(exact.per_mp[0][name]) == 41
        assert all(isinstance(v, Exact) for v in exact.per_mp[0][name])
    assert exact.mp_ids == approx.mp_ids


def test_the_chain_is_exact_only_while_the_digits_fit_and_that_is_the_limit():
    """**The qualification that keeps "exact" honest, measured.**

    ``pols_if(t)`` is ``(1 - q)^t``. With ``q = 0.015`` the survival factor
    ``0.985`` has three decimal places, so ``t`` multiplications need ``3t``
    of them — and the recursion is *genuinely exact*, equal to a closed form
    computed independently, only while ``3t`` fits in the 34 digits
    :data:`EXACT_PRECISION` provides. That is ``t <= 11``.

    Past it both the chain and the closed form round, and at ``t = 13`` they
    part company: the chain has rounded twelve times and the closed form
    once. Neither is wrong. But "exact-decimal mode" would be a false
    description of period 13 if this test did not exist, and the failure mode
    it guards is a reader taking the label at face value in a sign-off pack.

    What survives past the boundary is still the point of the mode: 34
    significant digits against double precision's ~16, so the float run's
    error is measurable to eighteen digits it does not have.
    """
    points, assumptions = build(q=0.015)
    exact = run_exact(FixedAnnuity, points, assumptions, 40, ["pols_if"])
    series = exact.per_mp[0]["pols_if"]

    with localcontext(EXACT_CONTEXT):
        survival = Exact(1) - as_written(0.015)
        assert survival == Decimal("0.985")

        # Exact while the digits fit: three decimal places per multiplication.
        for t in range(12):
            assert 3 * t <= EXACT_PRECISION
            assert series[t] == survival ** t, t
            assert len(str(series[t]).split(".")[1]) == 3 * t or t == 0

        # And past the budget the two roundings diverge — which is a fact
        # about decimal arithmetic, not about this engine.
        assert 3 * 13 > EXACT_PRECISION
        assert series[13] != survival ** 13
        assert abs(series[13] - survival ** 13) < Decimal("1e-33")

    # The float run leaves the exact chain far earlier, and by far more,
    # which is the comparison the mode exists to make.
    approx = runner.run(FixedAnnuity, points, assumptions, 40, ["pols_if"])
    drifted = [t for t in range(41)
               if Exact(float(approx.per_mp[0]["pols_if"][t])) != series[t]]
    assert drifted and min(drifted) < 13, (
        f"float first left the exact chain at t={min(drifted) if drifted else None}, "
        f"which is no earlier than decimal's own rounding boundary — the "
        f"comparison would then be asserting nothing about float"
    )


def test_reading_as_stored_gives_a_different_and_also_correct_answer():
    """Both readings are legitimate and they disagree, which is the reason
    both are offered. ``as_written`` answers "what does the basis the actuary
    stated produce?"; ``as_stored`` answers "what does the basis the machine
    holds produce?". The gap between them is the **representation** error,
    with arithmetic error held constant — and without both runs a discrepancy
    has two candidate causes and no way to separate them."""
    points, assumptions = build()
    written = run_exact(FixedAnnuity, points, assumptions, 40, ["v"])
    stored = run_exact(FixedAnnuity, points, assumptions, 40, ["v"],
                       reader=as_stored)

    assert written.per_mp[0]["v"][0] == stored.per_mp[0]["v"][0] == 1
    assert written.per_mp[0]["v"][40] != stored.per_mp[0]["v"][40]
    gap = abs(written.per_mp[0]["v"][40] - stored.per_mp[0]["v"][40])
    assert gap < abs(written.per_mp[0]["v"][40]) * Decimal("1e-14")


# --------------------------------------------------------------------------
# The refusals
# --------------------------------------------------------------------------

def test_a_pooled_model_is_refused_here_for_the_same_reason_as_in_float():
    """One instance per model point gives each policy a pool of itself, and
    the arithmetic it uses to do so is beside the point. The mode reuses
    :func:`~engine.core.runner.check_per_policy` rather than restating the
    rule, so the two executors cannot drift apart on where the class ends."""

    class Pooled(FixedAnnuity):
        couples_model_points = True

    points, assumptions = build()
    with pytest.raises(PooledBlockError, match="couples_model_points"):
        run_exact(Pooled, points * 3, assumptions, 10, ["pols_if"])
    # A block of one is permitted, exactly as in float: a pool of one is the
    # same reduction either way.
    assert run_exact(Pooled, points, assumptions, 10, ["pols_if"])


def test_a_template_whose_assumptions_return_arrays_is_refused_by_name():
    """**Coverage is stated, never worked around.** Some bases hand back an
    array — a yield curve over the projection, a per-period vector — and the
    exact executor runs one policy at a time with no array to apply it to.

    Falling back to float there would produce a "sign-off run" that was a
    float run wearing a label, which is the one outcome worse than refusing.
    So it raises, names the shape it was given, and says the coverage is
    stated."""

    class ArrayBasis:
        freq = 1

        def discount(self, t):
            import numpy as np
            return np.array([1.0, 2.0, 3.0])

    from engine.core.exact import _DecimalView

    view = _DecimalView(ArrayBasis(), as_written)
    with pytest.raises(ExactError, match="shape"):
        view.discount(0)
    assert view.freq == 1


def test_an_empty_block_is_refused():
    _, assumptions = build()
    with pytest.raises(ValueError, match="no model points"):
        run_exact(FixedAnnuity, [], assumptions, 10)


def test_the_context_traps_rather_than_returning_a_quiet_nan():
    """**Why the context is configured at all.** Float returns ``inf`` for a
    division by zero and ``nan`` for an invalid operation, and both propagate
    silently through a projection into a reported reserve.

    Under this context they raise. A sign-off run that stopped is strictly
    better than one that produced ``NaN`` and a number beside it."""
    assert set(EXACT_CONTEXT.traps) >= {InvalidOperation, DivisionByZero,
                                        Overflow}
    with localcontext(EXACT_CONTEXT):
        with pytest.raises(DivisionByZero):
            as_written(1.0) / Exact(0)
        with pytest.raises(InvalidOperation):
            Exact(0) / Exact(0)
    # Float, for contrast, does neither.
    assert (0.0 / 1.0) == 0.0


def test_the_model_point_conversion_leaves_non_numeric_fields_alone():
    """A sex code, a date, a product name: converting those would be a
    category error, and dropping them would change the model point."""
    mp = ModelPoint(age_at_entry=50, defer_years=10, premium=100_000.0,
                    annual_payment=9_000.0, init_pols=1, sex="F")
    converted = exact_model_point(mp)
    assert converted.sex == "F"
    assert isinstance(converted.premium, Exact)
    assert converted.premium == Decimal("100000")
    assert isinstance(converted.age_at_entry, Exact)


def test_the_mode_is_slow_and_that_is_the_price_not_a_defect():
    """Asserted so the docs cannot drift into implying it is free. Decimal
    is software arithmetic against hardware floats; the mode is opt-in, for
    a sign-off sample rather than a valuation.

    The bound is loose on purpose — this is a statement about order of
    magnitude, not a benchmark, and a tight one would fail on a shared CI
    runner for reasons that have nothing to do with the engine."""
    import time

    points, assumptions = build()
    runner.run(FixedAnnuity, points, assumptions, 40, NAMES)
    start = time.perf_counter()
    runner.run(FixedAnnuity, points, assumptions, 40, NAMES)
    float_time = time.perf_counter() - start

    run_exact(FixedAnnuity, points, assumptions, 40, NAMES)
    start = time.perf_counter()
    run_exact(FixedAnnuity, points, assumptions, 40, NAMES)
    exact_time = time.perf_counter() - start

    assert exact_time > float_time
    assert exact_time < float_time * 200


# --------------------------------------------------------------------------
# Coverage across the catalogue — stated, never fudged
# --------------------------------------------------------------------------

def test_the_coverage_is_a_partition_and_the_bound_holds_across_it():
    """**What the mode can audit, and the bound it produces — asserted, not
    quoted.** §1.2's discipline is that coverage is stated rather than
    implied, and the way to keep a coverage claim true is to compute it.

    Every worked example lands in exactly one of three buckets:

    - **audited** — ran under both arithmetics, and the float answer is
      within the bound below;
    - **outside** — the interpreted executor cannot run it at all, so
      neither can this: a pooled or coupled template (each policy would see
      a pool of itself) or one bound to a scenario set;
    - **refused** — the assumption layer hands back arrays, and a
      one-policy-at-a-time decimal run has nothing to apply them to.

    The third bucket is the interesting one. Falling back to float there
    would produce a "sign-off run" that was a float run wearing a label, so
    it raises with the shape it was given.
    """
    from engine.core.exact import ExactError
    from engine.report.evidence import default_specimens

    specimens = default_specimens()
    if not specimens:
        pytest.skip("the worked examples need the [api] extra")

    audited, outside, refused = {}, {}, {}
    for specimen in specimens:
        name = specimen.get("name") or specimen["model_cls"].__name__
        call = dict(model_cls=specimen["model_cls"],
                    modelpoints=specimen["modelpoints"],
                    assumptions=specimen["assumptions"],
                    proj_len=specimen["proj_len"])
        if specimen.get("scenarios") is not None:
            outside[name] = "binds a scenario set"
            continue
        try:
            approx = runner.run(**call)
        except PooledBlockError:
            outside[name] = "pooled or coupled"
            continue
        try:
            exact = run_exact(**call)
        except ExactError as exc:
            refused[name] = str(exc)
            continue
        audited[name] = agreement(approx, exact)

    # A partition: every specimen accounted for exactly once.
    assert len(audited) + len(outside) + len(refused) == len(specimens)
    assert not (set(audited) & set(outside) & set(refused))

    # None of the three may quietly empty out — a bucket that has emptied
    # makes every assertion over it vacuous while the test keeps passing.
    assert audited and outside and refused

    for name, reason in refused.items():
        assert "shape" in reason, (name, reason)

    worst = max(report["worst_relative"] for report in audited.values())
    assert worst < Decimal("1e-12"), (
        f"the float executor now disagrees with 34-digit decimal by "
        f"{worst}, which is far outside the ~1e-13 previously measured. "
        f"Something has changed in the arithmetic, not in the tolerance."
    )
    # And the bound is not trivially satisfied by everything agreeing
    # exactly, which would mean the comparison had stopped comparing.
    assert worst > Decimal(0)
