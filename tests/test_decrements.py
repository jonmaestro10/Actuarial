"""Multiple decrements: how competing exits split, and what cannot change.

tests/test_frequency.py established that running the same assumptions
monthly instead of annually leaves the same policies in force at every
anniversary but shifts exits from mortality to lapse — the decrements
interleave rather than being applied whole in sequence. It converged
towards an answer it had no way to write down. ``Decrements`` writes it
down, and this file closes that loop: the sequential method at frequency
``m`` approaches the ``constant_force`` method at frequency 1, first order
in ``1/m``.

The file is organised around one invariant and three closed forms.

**The invariant.** Every method agrees on total survival, ``Π (1 - q'_k)``.
They disagree about *who* left, never about *how many*. So switching method
cannot move an in-force count, only the split — and ``Σ_j q_j`` must come
back to ``1 - Π (1 - q'_k)`` exactly, for any number of decrements.

**The closed forms.** Each method is exact under its own statement about
when in the year people leave, so each is checked against that statement
rather than against the others.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.decrements import METHODS, Decrements
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.library.term_life import TermLife

Q, W = 0.05, 0.10          # deliberately large: the methods differ visibly
THIRD = 0.03


def rates(**kw):
    return dict(kw)


# --- the invariant every method shares -----------------------------------


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    "given",
    [
        {"mortality": Q, "lapse": W},
        {"mortality": Q, "lapse": W, "disability": THIRD},
        {"mortality": 0.4, "lapse": 0.3, "disability": 0.2, "retirement": 0.1},
        {"mortality": 0.0, "lapse": 0.0},
        {"mortality": 1e-9, "lapse": 0.5},
    ],
)
def test_the_exits_account_for_exactly_the_people_who_left(method, given):
    """``Σ_j q_j == 1 - Π_k (1 - q'_k)``.

    This is the whole contract between a decrement basis and a projection:
    if it fails, the in-force roll-forward and the cause-by-cause exits
    tell different stories about the same block."""
    dep = Decrements(method).dependent(given)
    survival = Decrements.survival(given)
    assert sum(dep.values()) == pytest.approx(1.0 - survival, abs=1e-15)


@pytest.mark.parametrize("method", METHODS)
def test_the_invariant_holds_on_random_rates(method):
    rng = np.random.default_rng(20260804)
    for n in (2, 3, 4):
        given = {f"d{i}": rng.uniform(0.0, 1.0, size=500) for i in range(n)}
        dep = Decrements(method).dependent(given)
        total = sum(dep.values())
        assert np.allclose(
            total, 1.0 - Decrements.survival(given), rtol=0, atol=1e-14
        )
        for rate in dep.values():
            assert np.all(rate >= 0.0)


@pytest.mark.parametrize("method", METHODS)
def test_every_method_agrees_on_survival(method):
    """Not merely close — the same expression, so the same bits."""
    given = {"mortality": Q, "lapse": W, "disability": THIRD}
    dep = Decrements(method).dependent(given)
    assert Decrements.survival(given) == (1.0 - Q) * (1.0 - W) * (1.0 - THIRD)
    assert len(dep) == 3


@pytest.mark.parametrize("method", METHODS)
def test_one_decrement_leaves_its_own_rate_untouched(method):
    """With nothing to compete against, every method must return the
    independent rate itself — bit for bit, since a single-decrement model
    is the deferred annuity and switching its basis must not move a
    number.

    ``constant_force`` reaches this only because it is short-circuited.
    Left to its own formula it would evaluate ``1 - exp(-(-log1p(-q)))``,
    a round trip through two transcendentals that costs a bit or two on a
    small rate — accurate enough, but not the identity, and the identity
    is the useful guarantee here."""
    given = {"mortality": np.array([0.0, 1e-8, Q, 0.5, 1.0])}
    dep = Decrements(method).dependent(given)
    assert np.array_equal(dep["mortality"], given["mortality"])


# --- sequential ----------------------------------------------------------


def test_sequential_is_the_order_the_templates_always_applied():
    dep = Decrements("sequential").dependent({"mortality": Q, "lapse": W})
    assert dep["mortality"] == Q
    assert dep["lapse"] == (1.0 - Q) * W


def test_sequential_depends_on_the_order_and_the_others_do_not():
    """The reason to have the other two: an answer that changes when you
    reorder the multiplications is an artefact of the code, not a
    statement about the block."""
    forward = {"mortality": Q, "lapse": W}
    reversed_ = {"lapse": W, "mortality": Q}
    seq = Decrements("sequential")
    assert seq.dependent(forward)["mortality"] != seq.dependent(reversed_)["mortality"]
    for method in ("udd", "constant_force"):
        d = Decrements(method)
        assert d.dependent(forward)["mortality"] == d.dependent(reversed_)["mortality"]
        assert d.dependent(forward)["lapse"] == d.dependent(reversed_)["lapse"]


def test_sequential_brackets_the_other_methods():
    """Applying a decrement first over-counts it; applying it last
    under-counts it. Any defensible answer sits between."""
    first = Decrements("sequential").dependent({"mortality": Q, "lapse": W})
    last = Decrements("sequential").dependent({"lapse": W, "mortality": Q})
    for method in ("udd", "constant_force"):
        middle = Decrements(method).dependent({"mortality": Q, "lapse": W})
        assert last["mortality"] < middle["mortality"] < first["mortality"]


# --- udd -----------------------------------------------------------------


def test_udd_is_the_textbook_two_decrement_formula():
    """``q_1 = q'_1 (1 - q'_2 / 2)`` — Bowers, and here an exact
    consequence of the integral rather than a quoted result."""
    dep = Decrements("udd").dependent({"mortality": Q, "lapse": W})
    assert dep["mortality"] == pytest.approx(Q * (1.0 - W / 2.0), rel=1e-15)
    assert dep["lapse"] == pytest.approx(W * (1.0 - Q / 2.0), rel=1e-15)


def test_udd_is_exactly_the_midpoint_of_the_two_sequential_orderings():
    """With two decrements, ``q'(1 - q''/2)`` is the mean of ``q'`` and
    ``q'(1 - q'')``. A pleasing check that the integral is doing what the
    ordering argument suggests it should."""
    first = Decrements("sequential").dependent({"mortality": Q, "lapse": W})
    last = Decrements("sequential").dependent({"lapse": W, "mortality": Q})
    udd = Decrements("udd").dependent({"mortality": Q, "lapse": W})
    assert udd["mortality"] == pytest.approx(
        (first["mortality"] + last["mortality"]) / 2.0, rel=1e-15
    )


def test_udd_is_the_textbook_three_decrement_formula():
    """``q_1 = q'_1 [1 - (q'_2 + q'_3)/2 + q'_2 q'_3 / 3]`` — the
    polynomial expansion has to reproduce this, which is the check that
    the general convolution is right and not merely plausible."""
    dep = Decrements("udd").dependent(
        {"mortality": Q, "lapse": W, "disability": THIRD}
    )
    expected = Q * (1.0 - (W + THIRD) / 2.0 + W * THIRD / 3.0)
    assert dep["mortality"] == pytest.approx(expected, rel=1e-15)


def test_udd_matches_numerical_integration_for_four_decrements():
    """Beyond three there is no formula to quote, so the expansion is
    checked against the integral it claims to evaluate."""
    given = {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}
    dep = Decrements("udd").dependent(given)
    s = np.linspace(0.0, 1.0, 2_000_001)
    for name, rate in given.items():
        others = np.prod(
            [1.0 - s * v for k, v in given.items() if k != name], axis=0
        )
        assert dep[name] == pytest.approx(rate * np.trapezoid(others, s), rel=1e-10)


# --- constant force ------------------------------------------------------


def test_constant_force_splits_exits_in_proportion_to_the_forces():
    mu_q, mu_w = -np.log1p(-Q), -np.log1p(-W)
    exits = 1.0 - (1.0 - Q) * (1.0 - W)
    dep = Decrements("constant_force").dependent({"mortality": Q, "lapse": W})
    assert dep["mortality"] == pytest.approx(mu_q / (mu_q + mu_w) * exits, rel=1e-15)
    assert dep["lapse"] == pytest.approx(mu_w / (mu_q + mu_w) * exits, rel=1e-15)


def test_constant_force_leaves_nobody_when_nobody_can_leave():
    dep = Decrements("constant_force").dependent(
        {"mortality": np.zeros(4), "lapse": np.zeros(4)}
    )
    assert np.array_equal(dep["mortality"], np.zeros(4))
    assert np.array_equal(dep["lapse"], np.zeros(4))


def test_a_certain_decrement_takes_everyone():
    """A mortality table reaches ``q = 1`` at its limiting age, so an
    infinite force is a real input, not a pathological one."""
    dep = Decrements("constant_force").dependent(
        {"mortality": 1.0, "lapse": 0.3}
    )
    assert dep["mortality"] == 1.0
    assert dep["lapse"] == 0.0


def test_two_certain_decrements_split_equally():
    """The symmetric limit, chosen because there is no other defensible
    one and a ``nan`` is not an answer."""
    dep = Decrements("constant_force").dependent(
        {"mortality": 1.0, "lapse": 1.0, "disability": 0.2}
    )
    assert dep["mortality"] == 0.5
    assert dep["lapse"] == 0.5
    assert dep["disability"] == 0.0


def test_a_certain_decrement_is_handled_element_wise():
    """Rates arrive per model point, so the limiting age is reached by
    some rows and not others in the same call.

    Row 0 is compared to tolerance rather than exactly: with one rate
    zero the answer passes through ``1 - (1 - 0.3)``, which is 0.3 plus an
    ulp. That is the reconciliation trade-off recorded in
    ``_constant_force`` — total exits are computed from the same product
    the in-force roll-forward uses, not from the more accurate
    ``expm1`` form."""
    dep = Decrements("constant_force").dependent(
        {"mortality": np.array([0.0, Q, 1.0]),
         "lapse": np.array([0.3, W, 0.3])}
    )
    assert dep["mortality"][0] == 0.0
    assert dep["mortality"][2] == 1.0
    assert dep["lapse"][0] == pytest.approx(0.3, rel=1e-15)
    assert dep["lapse"][2] == 0.0
    assert 0.0 < dep["mortality"][1] < Q


# --- closing the loop test_frequency.py left open ------------------------


def sequential_deaths_at_frequency(q, w, m):
    """A year of the sequential method at ``m`` steps, by hand.

    Deliberately not routed through the engine: the frequencies that
    matter here run to 100,000, and ``Assumptions`` only admits those
    dividing 12. The per-step rates are the constant-force splits the
    engine uses, so this is the same recursion at a finer step.
    """
    q_step = 1.0 - (1.0 - q) ** (1.0 / m)
    w_step = 1.0 - (1.0 - w) ** (1.0 / m)
    in_force, deaths = 1.0, 0.0
    for _ in range(m):
        deaths += in_force * q_step
        in_force *= (1.0 - q_step) * (1.0 - w_step)
    return deaths


def test_sequential_converges_to_constant_force_as_the_step_shrinks():
    """The answer tests/test_frequency.py was converging on, stated.

    A constant hazard for each cause is exactly what an infinitely fine
    sequential projection assumes, so the two must meet — and the
    ``constant_force`` method reaches it in one step at frequency 1.
    """
    limit = Decrements("constant_force").dependent(
        {"mortality": Q, "lapse": W}
    )["mortality"]
    gaps = [abs(sequential_deaths_at_frequency(Q, W, m) - limit)
            for m in (1, 10, 100, 1_000, 10_000, 100_000)]
    assert gaps == sorted(gaps, reverse=True)
    # First order, so the gap is ~2.5e-3/m: at 100,000 steps that is 2.5e-8,
    # not zero, and quoting the rate rather than a round number is the point.
    assert gaps[-1] < 1e-7
    # Each tenfold refinement cuts the gap tenfold.
    for coarse, fine in zip(gaps, gaps[1:]):
        assert 9.0 < coarse / fine < 11.0


def test_the_engine_shows_the_same_approach_at_the_frequencies_it_admits():
    """The same convergence through the real projection, over the
    frequencies that divide 12 — the gap scaled by frequency is stable,
    which is what first-order convergence looks like from the inside."""
    limit = Decrements("constant_force").dependent(
        {"mortality": Q, "lapse": W}
    )["mortality"]
    point = ModelPoint(id="T", age_at_entry=45, term_years=20,
                       sum_assured=1.0, annual_premium=0.0, init_pols=1)
    scaled = []
    for freq in (1, 2, 4, 12):
        model = TermLife(
            point,
            Assumptions(mortality=MortalityTable.flat(Q), lapse=W,
                        interest=0.0, freq=freq,
                        fractional_ages="constant_force"),
            20 * freq,
        )
        deaths = sum(float(model.pols_death(t)) for t in range(freq))
        scaled.append((deaths - limit) * freq)
    assert all(s > 0 for s in scaled)          # approached from above
    assert max(scaled) / min(scaled) < 1.02    # the O(1/m) constant


# --- through a product ---------------------------------------------------


def term_points(n=4):
    return from_dicts([
        {"id": f"T{i}", "age_at_entry": 40 + 5 * i, "term_years": 20,
         "sum_assured": 100_000.0 * (i + 1), "annual_premium": 900.0 * (i + 1),
         "init_pols": 1}
        for i in range(n)
    ])


def term_assumptions(method=None):
    return Assumptions(
        mortality=MortalityTable.flat(Q), lapse=W, interest=0.03,
        expense_per_policy=50.0, decrements=method,
    )


def test_the_default_is_the_order_the_template_always_applied():
    """The reason every existing golden value still holds: an assumption
    set built without naming a decrement basis gets the old behaviour,
    operand for operand."""
    assert term_assumptions().decrements.method == "sequential"
    model = TermLife(term_points(1)[0], term_assumptions(), 25)
    # Stops one short of the term: at t = 19 the in-force mask zeroes
    # t + 1, so the roll-forward comparison would be vacuous there.
    for t in range(19):
        assert model.pols_death(t) == model.pols_if(t) * model.q_x(t)
        assert model.pols_lapse(t) == (
            model.pols_if(t) * (1.0 - model.q_x(t)) * model.lapse_rate(t)
        )
        assert model.pols_if(t + 1) == (
            model.pols_if(t) * (1.0 - model.q_x(t))
            * (1.0 - model.lapse_rate(t))
        )


@pytest.mark.parametrize("method", METHODS)
def test_the_method_cannot_move_the_in_force_count(method):
    """The invariant, seen through a whole projection: the survival
    factor is the same product under every method, so the block runs off
    identically and only the attribution of exits changes."""
    points = term_points()
    default = run_vectorized(TermLife, points, term_assumptions(), 25,
                             outputs=["pols_if"])
    other = run_vectorized(TermLife, points, term_assumptions(method), 25,
                           outputs=["pols_if"])
    assert np.allclose(
        np.asarray(default.array("pols_if")),
        np.asarray(other.array("pols_if")),
        rtol=1e-15, atol=0,
    )


def test_the_method_does_move_the_claims():
    """If it did not, none of this would be worth doing."""
    points = term_points()
    claims = {}
    for method in METHODS:
        result = run_vectorized(TermLife, points, term_assumptions(method), 25,
                                outputs=["claims"])
        claims[method] = float(np.asarray(result.array("claims")).sum())
    assert claims["sequential"] > claims["udd"] > claims["constant_force"]
    # And by an amount worth arguing about, not a rounding difference.
    assert (claims["sequential"] - claims["constant_force"]) / claims["udd"] > 0.04


def test_exits_and_survivors_reconcile_over_the_whole_projection():
    """Every policy is accounted for: those still in force, plus everyone
    who died or lapsed along the way, equals the starting block — under
    each method, and regardless of how they were attributed.

    Stopped well inside the 20-year term on purpose. Policies reaching the
    end of the term are not an exit under any decrement basis; they simply
    stop being covered, and including them would test the in-force mask
    rather than the decrement split."""
    horizon = 10
    points = term_points()
    for method in METHODS:
        result = run_vectorized(
            TermLife, points, term_assumptions(method), horizon + 1,
            outputs=["pols_if", "pols_death", "pols_lapse"],
        )
        in_force = np.asarray(result.array("pols_if"))
        exits = (np.asarray(result.array("pols_death"))[:horizon].sum(axis=0)
                 + np.asarray(result.array("pols_lapse"))[:horizon].sum(axis=0))
        assert np.allclose(
            exits + in_force[horizon], in_force[0], rtol=1e-13
        ), method


@pytest.mark.parametrize("method", METHODS)
def test_the_two_executors_agree_bitwise_under_every_method(method):
    points = term_points()
    outputs = ["pols_if", "pols_death", "pols_lapse", "claims"]
    assumptions = term_assumptions(method)
    interpreted = run(TermLife, points, assumptions, 25, outputs=outputs)
    vectorized = run_vectorized(TermLife, points, assumptions, 25,
                                outputs=outputs)
    for name in outputs:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


def test_the_run_registry_separates_the_methods():
    from engine.core.registry import record_run

    points = term_points()
    records = {}
    for method in METHODS:
        _, record = record_run(TermLife, points, term_assumptions(method), 25,
                               outputs=["claims"])
        records[method] = record
    ids = {r.run_id for r in records.values()}
    digests = {r.results_digest for r in records.values()}
    assert len(ids) == len(METHODS)
    assert len(digests) == len(METHODS)


# --- validation ----------------------------------------------------------


def test_an_unknown_method_raises():
    with pytest.raises(ValueError, match="method must be one of"):
        Decrements("whatever")
    with pytest.raises(ValueError, match="method must be one of"):
        Assumptions(mortality=MortalityTable.flat(Q), decrements="whatever")


def test_a_rate_outside_zero_to_one_raises():
    with pytest.raises(ValueError, match=r"outside \[0, 1\]"):
        Decrements("udd").dependent({"mortality": 1.2, "lapse": W})


def test_no_decrements_raises():
    with pytest.raises(ValueError, match="no decrements"):
        Decrements("udd").dependent({})
    with pytest.raises(ValueError, match="no decrements"):
        Decrements.survival({})


def test_a_decrements_object_can_be_passed_directly():
    a = Assumptions(mortality=MortalityTable.flat(Q),
                    decrements=Decrements("udd"))
    assert a.decrements.method == "udd"
    assert repr(a.decrements) == "Decrements('udd')"
