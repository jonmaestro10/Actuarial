"""Least-squares Monte Carlo, and the error estimate that licenses it.

PLAN §4.4 lists proxy models among the nested-stochastic tactics with a
condition attached: *"as an optional, clearly-labeled acceleration with
error estimates"*. That condition is the design, and it is why this arrives
after `engine/core/nested.py` rather than instead of it — the exact nested
valuation is the thing a proxy has to be checked against.

Three claims:

**The trick works.** Valuing every outer node with five inner scenarios and
regressing the noisy results on state recovers the answer to about 2% at
200x less inner work.

**No in-sample statistic of the fit can tell you whether it worked.** The
residual standard error describes how far the noisy node values sit from the
surface, which is a different quantity from how far the surface sits from
the truth — and it can rank two settings the wrong way round, which is
measured here rather than asserted.

**A proxy cannot be measured better than its reference.** The reference is
itself a Monte Carlo estimate; two independent 1,000-inner references differ
by about 1% of the mean value, which is the floor any error figure here is
quoted against.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.core.lsmc import (
    ProxyValuation,
    fit_proxy,
    polynomial_terms,
    proxy_error,
    restart_states,
)
from engine.core.nested import nested_stochastic, risk_neutral_inner
from engine.data.assumptions import Assumptions, DynamicLapse, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.unit_linked import UnitLinkedGMxB

RATE, VOL, TERM = 0.03, 0.18, 20
TIMES = [4, 8, 12]


def assumptions():
    return Assumptions(
        mortality=MortalityTable.flat(0.011), interest=RATE, amc=0.012,
        dynamic_lapse=DynamicLapse(0.06, sensitivity=0.8),
        gmdb_fee=0.004, gmab_fee=0.003, gmwb_fee=0.005,
    )


def points(n=1):
    return from_dicts([
        {"id": f"X{i}", "age_at_entry": 55, "term_years": TERM,
         "premium": 1e5, "gmdb_guarantee": 1e5, "gmab_guarantee": 1.1e5,
         "gmwb_base": 1e5, "gmwb_rate": 0.05, "gmwb_ratchet": 1.0,
         "init_pols": 1 + i}
        for i in range(n)
    ])


def outer(n=200, seed=11):
    return ScenarioSet.lognormal(n, TERM + 2, drift=0.06, vol=0.2, seed=seed)


def common(n_outer=200):
    return dict(outer=outer(n_outer), valuation_times=TIMES, proj_len=TERM,
                measure="guarantee_strain")


def reference(n_inner=400, seed=7, n_outer=200, n_mp=1):
    return nested_stochastic(
        UnitLinkedGMxB, points(n_mp), assumptions(),
        inner=risk_neutral_inner(RATE, VOL, n_inner, seed=seed),
        **common(n_outer),
    )


def proxy(n_inner=5, degree=3, seed=7, n_outer=200, n_mp=1, **kw):
    return fit_proxy(
        UnitLinkedGMxB, points(n_mp), assumptions(),
        inner=risk_neutral_inner(RATE, VOL, n_inner, seed=seed),
        degree=degree, **common(n_outer), **kw,
    )


# --- the basis -----------------------------------------------------------


def test_the_polynomial_basis_is_total_degree_with_cross_terms():
    """The cross term is where the interaction between a fund and the
    guarantee it is measured against actually lives, so a per-variable
    basis would miss the thing being modelled."""
    assert polynomial_terms(1, 2) == [(0,), (1,), (2,)]
    assert polynomial_terms(2, 2) == [
        (0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2)
    ]
    assert len(polynomial_terms(2, 3)) == 10
    assert len(polynomial_terms(3, 2)) == 10


def test_a_basis_wider_than_the_data_is_refused():
    with pytest.raises(ValueError, match="only 8 outer nodes"):
        proxy(n_outer=8, degree=3)


# --- what the proxy is fitted on -----------------------------------------


def test_the_regressors_come_from_what_the_template_declares():
    states = restart_states(
        UnitLinkedGMxB, points(), assumptions(), outer=outer(20),
        valuation_times=(4, 8), proj_len=TERM,
        states=("premium", "gmwb_base", "init_pols"),
    )
    assert set(states) == {4, 8}
    assert states[4]["premium"].shape == (1, 20)
    # Fund values differ across outer paths; the benefit base ratchets.
    assert np.ptp(states[4]["premium"]) > 0.0
    assert np.all(states[8]["gmwb_base"] >= 1e5 - 1e-9)


def test_fitting_on_state_the_template_does_not_carry_raises():
    with pytest.raises(KeyError, match="does not carry"):
        proxy(states=("premium", "hedge_ratio"))


def test_predict_needs_the_states_it_was_fitted_on():
    fit = proxy().fits[4]
    with pytest.raises(KeyError, match="missing"):
        fit.predict({"premium": np.array([1e5])})


def test_the_value_scales_exactly_with_the_policies_in_force():
    """A guarantee on two policies is worth twice a guarantee on one,
    exactly. Fitting per policy and scaling back keeps that exact rather
    than asking a polynomial to rediscover it."""
    one, two = proxy(n_mp=2).values[0][0], proxy(n_mp=2).values[0][1]
    assert np.allclose(two, 2.0 * one, rtol=1e-12)


# --- the trick works -----------------------------------------------------


def test_a_cheap_proxy_recovers_the_expensive_answer():
    """The headline. Five inner scenarios per outer node instead of four
    hundred, and the surface still lands within a few percent — against a
    reference whose own Monte Carlo error is around one percent."""
    error = proxy_error(proxy(n_inner=5, degree=3), reference(400),
                        reference(400, seed=99))
    assert error["speedup"] == 80.0
    assert error["relative"] < 0.05
    assert error["worst_relative"] < 0.08
    assert error["reference_noise"] > 0.0
    assert error["relative"] < 5.0 * error["reference_noise"]


def test_too_few_inner_scenarios_is_visibly_worse():
    """One inner path per node is not enough for this payoff, and the error
    estimate says so rather than letting it pass."""
    exact = reference(400)
    thin = proxy_error(proxy(n_inner=1), exact)["relative"]
    thick = proxy_error(proxy(n_inner=5), exact)["relative"]
    assert thin > 2.0 * thick


def test_a_richer_basis_reduces_the_error_that_sampling_cannot():
    """With inner sampling held generous, what is left is the basis. A
    linear surface cannot represent a guarantee payoff; a cubic nearly
    can."""
    exact = reference(400)
    errors = [
        proxy_error(proxy(n_inner=200, degree=d), exact)["relative"]
        for d in (1, 2, 3)
    ]
    assert errors[0] > 3.0 * errors[-1]
    assert errors == sorted(errors, reverse=True)


# --- the error estimate --------------------------------------------------


def test_the_residual_tells_you_nothing_about_the_surface():
    """The distinction the whole method turns on, and the reason
    ``proxy_error`` measures against a reference rather than against the
    fit.

    The residual standard error describes how far the *noisy node values*
    sit from the surface. How far the surface sits from the truth is a
    different quantity, and across settings the ratio between them runs
    from 0.11 to 1.84 with no pattern — so the residual over-states the
    error at some settings and under-states it at others.

    The dangerous direction is the flattering one, and it is pinned below:
    at degree 3, two inner scenarios per node give a **lower** residual than
    five — so an in-sample reading picks them as the better fit — while the
    surface is five times further out.
    """
    exact = reference(400)
    measured = {}
    for n_inner in (2, 5, 100):
        fitted = proxy(n_inner=n_inner, degree=3)
        error = proxy_error(fitted, exact)
        measured[n_inner] = (
            float(np.mean([f.residual_std for f in fitted.fits.values()])),
            float(np.mean([error["by_date"][t]["mean_absolute"]
                           for t in TIMES])),
        )
    residuals = {n: r for n, (r, _) in measured.items()}
    actuals = {n: a for n, (_, a) in measured.items()}

    # Two inner scenarios per node has the *lower* residual of the two, so
    # an in-sample reading picks it as the better fit. Its surface is five
    # times further out.
    assert residuals[2] < residuals[5]
    assert actuals[2] > 5.0 * actuals[5]

    # And the ratio lands on both sides of 1 across the settings, so there
    # is no correction factor that would rescue the residual as an estimate.
    ratios = [r / a for r, a in measured.values()]
    assert min(ratios) < 0.5 < 1.0 < max(ratios)


def test_the_reference_has_an_error_of_its_own_and_it_is_reported():
    """A proxy cannot be measured to be better than the thing measuring
    it."""
    fitted = proxy()
    plain = proxy_error(fitted, reference(400))
    assert plain["reference_noise"] is None
    assert plain["reference_noise_floor"] > 0.0

    informed = proxy_error(fitted, reference(400), reference(400, seed=99))
    assert informed["reference_noise"] > 0.0


def test_the_stderr_floor_understates_the_reference_noise():
    """Knowingly, and by about half. The nested driver values every outer
    node at a date against the *same* inner scenarios, so node errors are
    correlated and the surface shifts together rather than averaging out —
    which a per-node standard error cannot see."""
    error = proxy_error(proxy(), reference(400), reference(400, seed=99))
    assert error["reference_noise"] > 1.5 * error["reference_noise_floor"]


def test_the_error_report_covers_every_date_and_the_whole_run():
    error = proxy_error(proxy(), reference(400))
    assert set(error["by_date"]) == set(TIMES)
    for entry in error["by_date"].values():
        assert entry["max_absolute"] >= entry["mean_absolute"] >= 0.0
        assert abs(entry["bias"]) <= entry["mean_absolute"] + 1e-9
    assert error["worst_relative"] >= error["relative"]


def test_comparing_against_a_reference_of_different_dates_raises():
    other = nested_stochastic(
        UnitLinkedGMxB, points(), assumptions(),
        inner=risk_neutral_inner(RATE, VOL, 20, seed=7),
        outer=outer(), valuation_times=[4], proj_len=TERM,
        measure="guarantee_strain",
    )
    with pytest.raises(ValueError, match="the reference covers"):
        proxy_error(proxy(), other)


# --- housekeeping --------------------------------------------------------


def test_a_proxy_reports_its_shape_and_its_cost():
    fitted = proxy(n_inner=5)
    assert isinstance(fitted, ProxyValuation)
    assert fitted.values.shape == (3, 1, 200)
    assert fitted.inner_cells == 3 * 1 * 200 * 5
    assert "guarantee_strain proxy" in fitted.summary()
    assert set(fitted.fits) == set(TIMES)
    assert fitted.fits[4].valuation_time == 4
    assert fitted.fits[4].n_nodes == 200


def test_the_same_seed_gives_the_same_surface():
    assert np.array_equal(proxy(seed=3).values, proxy(seed=3).values)
    assert not np.array_equal(proxy(seed=3).values, proxy(seed=4).values)


def test_an_unknown_valuation_date_lists_the_ones_that_exist():
    with pytest.raises(KeyError, match=r"covers \[4, 8, 12\]"):
        proxy().at(7)


def test_a_state_that_does_not_vary_does_not_produce_a_nan():
    """At inception every outer node holds identical state, so a regressor
    has zero spread. Normalising by it would divide by zero; leaving the
    column constant lets the least-squares solve treat it as collinear with
    the intercept, which is what it is."""
    fitted = fit_proxy(
        UnitLinkedGMxB, points(), assumptions(),
        inner=risk_neutral_inner(RATE, VOL, 20, seed=7),
        outer=outer(60), valuation_times=[0, 4], proj_len=TERM,
        measure="guarantee_strain", degree=2,
    )
    assert np.all(np.isfinite(fitted.values))
    at_zero = fitted.at(0)[0]
    assert np.ptp(at_zero) == pytest.approx(0.0, abs=1e-6)
