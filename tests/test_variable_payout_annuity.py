"""The pooled product, and the ``@pool`` variable that makes it expressible.

docs/vpla-review.md §7.1 recorded that VPLA's pension adjustment is a
reduction across the model-point axis inside the time loop, and that the
``@var`` DSL had no way to spell it. This is the test file for the thing
that closed that gap, so it is organised around the two properties that
define the product rather than around the code:

- **The pool balances.** After every revaluation the members' account values
  sum to the pool's assets, exactly. Assets equal liabilities, always.
- **It is neutral when experience matches assumption.** Earn the valuation
  rate, lose exactly the expected members, and no pension moves. Deceased
  members' reserves reach the survivors through the adjustment itself, with
  no separate mortality-credit term.

Both were established first against the reference implementation of the real
system (tests/test_vpla_reconciliation.py); here they are asserted of the
engine's own template, and the adjustment is pinned to a closed form the
product implies.
"""

from datetime import date

import numpy as np
import pytest

from engine.core.model import Model, pool, var
from engine.core.stochastic import run_stochastic
from engine.core.vector import run_vectorized
from engine.data.basis import ValuationBasis
from engine.data.modelpoints import ModelPoint
from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve
from engine.data.scenarios import ScenarioSet
from engine.library.annuities import prospective_annuity_factors
from engine.library.variable_payout_annuity import VariablePayoutAnnuity
from vpla_reference import valuation_step

MIN_AGE, MAX_AGE = 18, 115
YEAR_START = 2014
INTEREST = 0.04
VALUATION = date(2021, 1, 1)
REL = 1e-12

RATES = {
    sex: {
        age: min(0.0004 * 1.09 ** (age - MIN_AGE) * (1.0 if sex == "M" else 0.85), 0.6)
        for age in range(MIN_AGE, MAX_AGE + 1)
    }
    for sex in ("M", "F")
}
MORTALITY = MortalityBasis(RATES, year_start=YEAR_START, use_improvement=False)

MEMBERS = [
    (date(1956, 1, 1), "M", 1_200.0, 1),
    (date(1946, 6, 30), "F", 2_400.0, 3),
    (date(1951, 3, 15), "M", 900.0, 2),
    (date(1938, 12, 15), "F", 3_000.0, 1),
]


def basis(freq, revalue_every=1):
    return ValuationBasis(
        mortality=MORTALITY, curve=YieldCurve([INTEREST], freq=freq),
        revalue_every=revalue_every,
    )


def build_pool(freq, n, pensions=None):
    """A pool that starts in balance: every member's account value is their
    own reserve, so any adjustment is experience and nothing else."""
    curve = YieldCurve([INTEREST], freq=freq)
    discount = curve.discount_factors(n)
    survival = MORTALITY.survival_curve(
        [dob for dob, _, _, _ in MEMBERS], [VALUATION] * len(MEMBERS),
        [sex for _, sex, _, _ in MEMBERS], freq, n,
    )
    factors = prospective_annuity_factors(discount, survival, freq)
    pensions = pensions or [pension for _, _, pension, _ in MEMBERS]
    return [
        ModelPoint(
            id=f"M{i}", dob=dob, sex=sex, valuation=VALUATION,
            pension=pension, account_value=pension * factors[i, 0] * freq,
            init_lives=lives,
        )
        for i, ((dob, sex, _, lives), pension) in enumerate(zip(MEMBERS, pensions))
    ], factors


def per_period(annual_rate, freq):
    """Scenario returns are per period, so an annual rate has to be
    converted before it can be compared with the valuation rate."""
    return (1.0 + annual_rate) ** (1.0 / freq) - 1.0


def run(freq, n, fund_return, revalue_every=1, n_scenarios=1, outputs=None):
    points, _ = build_pool(freq, n)
    scenarios = ScenarioSet.flat(
        per_period(fund_return, freq), n_scenarios=n_scenarios, horizon=n
    )
    return points, run_stochastic(
        VariablePayoutAnnuity, points, basis(freq, revalue_every), scenarios,
        n - 1,
        outputs=outputs or ["pension", "adjustment", "assets", "liability",
                            "account_value", "lives", "payments"],
    )


def solvent(result, scenario=0):
    """Periods where the pool still owes somebody something. Past the point
    where every member is dead the adjustment is undefined, not zero."""
    liability = result.array("liability").sum(axis=1)[:, scenario]
    return liability > liability[0] * 1e-12


# --- the defining properties ----------------------------------------------


@pytest.mark.parametrize("freq", [1, 4, 12])
def test_the_pool_is_neutral_when_experience_matches_assumption(freq):
    """The product's whole point. No pension moves, at any frequency, for as
    long as the pool has members — and the reserves of those who die reach
    the survivors through the adjustment, with no separate term."""
    n = 45 * freq
    _, result = run(freq, n, INTEREST)
    live = solvent(result)
    adjustment = result.array("adjustment")[:, 0, 0][live]
    assert np.abs(adjustment).max() < 1e-12

    for i in range(len(MEMBERS)):
        pensions = result.array("pension")[:, i, 0][live]
        assert pensions == pytest.approx(pensions[0], rel=1e-10)


@pytest.mark.parametrize("fund_return", [-0.10, 0.0, 0.04, 0.08, 0.20])
def test_the_pool_balances_after_every_revaluation(fund_return):
    """Assets equal liabilities, exactly, at every step — which is what makes
    the product a pure pass-through of experience."""
    freq, n = 12, 40 * 12
    _, result = run(freq, n, fund_return)
    assets = result.array("assets").sum(axis=1)[:, 0]
    accounts = result.array("account_value").sum(axis=1)[:, 0]
    live = solvent(result)
    assert np.allclose(accounts[live], assets[live], rtol=1e-12, atol=0.0)


@pytest.mark.parametrize("fund_return", [-0.10, 0.0, 0.08, 0.20])
def test_the_adjustment_is_the_return_relative_to_the_valuation_rate(fund_return):
    """With a flat fund return against a flat valuation rate the adjustment
    has a closed form: ``(1 + r) / (1 + i) - 1``, every period. Anything the
    pool earns above the rate it was valued at becomes pension, and nothing
    else does."""
    freq, n = 1, 40
    _, result = run(freq, n, fund_return)
    live = solvent(result)
    want = (1 + per_period(fund_return, freq)) / (1 + per_period(INTEREST, freq)) - 1
    adjustment = result.array("adjustment")[:, 0, 0][live]
    # t = 0 has nothing to adjust: the pool starts in balance.
    assert adjustment[0] == pytest.approx(0.0, abs=1e-12)
    assert adjustment[1:] == pytest.approx(want, rel=1e-10)

    # And pensions compound at exactly that rate.
    pensions = result.array("pension")[:, 0, 0][live]
    for t in range(1, len(pensions)):
        assert pensions[t] == pytest.approx(
            pensions[0] * (1 + want) ** t, rel=1e-9
        )


def test_a_pension_cut_is_proportional_across_every_member():
    """One number moves the whole pool, which is what a reduction across the
    model-point axis buys — and what no per-policy formula could produce."""
    freq, n = 1, 30
    _, result = run(freq, n, -0.10)
    live = solvent(result)
    ratios = [
        result.array("pension")[:, i, 0][live] / result.array("pension")[0, i, 0]
        for i in range(len(MEMBERS))
    ]
    for other in ratios[1:]:
        assert np.allclose(other, ratios[0], rtol=1e-12)


# --- against the reference implementation ----------------------------------


def test_one_step_matches_the_vpla_reference_valuation():
    """The template's adjustment against ``vpla_reference.valuation_step``,
    which is the hand transcription of ``CalcEngine``. The reference works in
    member rows — a survivor row and a deceased row per cohort, the deceased
    one carrying assets but no liability — where the template carries cohort
    weights; the two have to agree on the pool number regardless."""
    freq, n = 1, 40
    fund_return = 0.09
    points, factors = build_pool(freq, n)
    _, result = run(freq, n, fund_return)

    survival = result.array("lives")[:, :, 0] / np.array(
        [p.init_lives for p in points]
    )
    accounts, pensions, annuity_factors, alive = [], [], [], []
    for i, point in enumerate(points):
        for weight, is_alive in (
            (survival[1, i], True), (survival[0, i] - survival[1, i], False)
        ):
            accounts.append(point.account_value * point.init_lives * weight)
            pensions.append(point.pension * point.init_lives * weight)
            annuity_factors.append(factors[i, 1])
            alive.append(is_alive)

    reference = valuation_step(
        account_values=accounts, pensions=pensions,
        annuity_factors=annuity_factors, contributions=[0.0] * len(accounts),
        fund_return=fund_return, freq=freq, alive=alive,
    )
    assert result.array("adjustment")[1, 0, 0] == pytest.approx(
        reference["adjustment"], rel=1e-12
    )
    assert result.array("assets").sum(axis=1)[1, 0] == pytest.approx(
        sum(reference["retrospective"]), rel=1e-12
    )
    assert result.array("liability").sum(axis=1)[1, 0] == pytest.approx(
        sum(reference["prospective"]), rel=1e-12
    )


# --- revaluation frequency -------------------------------------------------


def test_pensions_hold_between_revaluations():
    """VPLA pays monthly but revalues periodically. Between valuations the
    pension is carried unchanged and the assets simply roll forward."""
    freq, revalue_every, years = 12, 12, 20
    n = years * freq
    _, result = run(freq, n, 0.08, revalue_every=revalue_every)
    adjustment = result.array("adjustment")[:, 0, 0]
    pensions = result.array("pension")[:, 0, 0]
    live = solvent(result)

    off_cycle = np.array([t % revalue_every != 0 for t in range(n)])
    assert np.all(adjustment[off_cycle] == 0.0)
    assert np.any(np.abs(adjustment[~off_cycle & live]) > 1e-6)
    for t in range(1, n):
        if t % revalue_every:
            assert pensions[t] == pensions[t - 1]


def test_revaluing_every_period_is_the_default():
    freq, n = 12, 120
    _, every = run(freq, n, 0.08, revalue_every=1)
    adjustment = every.array("adjustment")[:, 0, 0][solvent(every)]
    assert np.all(np.abs(adjustment[1:]) > 1e-9)


# --- the pooled variable itself --------------------------------------------


def test_the_adjustment_is_declared_pooled():
    assert VariablePayoutAnnuity.pooled_names() == ["adjustment"]
    assert "adjustment" in VariablePayoutAnnuity.var_names()


def test_a_pooled_model_is_never_chunked():
    """A reduction over a chunk would be a reduction over the wrong
    population, so the runner has to keep the block whole — without anyone
    remembering to set a flag."""
    seen = []

    class Watched(VariablePayoutAnnuity):
        def setup(self):
            seen.append(self.mp.n)
            super().setup()

    points, _ = build_pool(1, 30)
    run_vectorized(
        Watched, points, basis(1), proj_len=29, outputs=["survival"],
        chunk_size=1,
    )
    assert seen == [len(points)]


def test_pool_sum_reduces_model_points_and_leaves_scenarios():
    class Reducer(Model):
        @var
        def per_policy(self, t):
            return self.mp.weight * (t + 1.0)

        @pool
        def total(self, t):
            return self.pool_sum(self.per_policy(t))

    points = [ModelPoint(id=i, weight=float(i + 1)) for i in range(4)]
    deterministic = run_vectorized(
        Reducer, points, None, proj_len=3, outputs=["total", "per_policy"]
    )
    # Every model point sees the same pooled value.
    for t in range(4):
        assert list(deterministic.array("total")[t]) == [10.0 * (t + 1)] * 4

    scenarios = ScenarioSet.flat(0.05, n_scenarios=3, horizon=4)
    stochastic = run_stochastic(
        Reducer, points, None, scenarios, 3, outputs=["total"]
    )
    assert stochastic.array("total").shape == (4, 4, 3)
    assert np.all(stochastic.array("total")[1] == 20.0)


def test_a_pooled_slab_equals_per_scenario_runs_bitwise():
    """The reduction must run within each scenario, not across them."""
    freq, n = 1, 30
    points, _ = build_pool(freq, n)
    scenarios = ScenarioSet.lognormal(
        n_scenarios=5, horizon=n, drift=np.log(1 + INTEREST) / freq, vol=0.12,
        seed=4,
    )
    names = ["pension", "adjustment", "assets", "account_value"]
    slab = run_stochastic(
        VariablePayoutAnnuity, points, basis(freq), scenarios, n - 1, outputs=names
    )
    for s in range(scenarios.n_scenarios):
        alone = run_stochastic(
            VariablePayoutAnnuity, points, basis(freq), scenarios.single(s),
            n - 1, outputs=names,
        )
        for name in names:
            assert np.array_equal(
                slab.array(name)[:, :, s], alone.array(name)[:, :, 0]
            ), f"scenario {s} var {name}"


def test_scenarios_move_pensions_independently():
    freq, n = 1, 25
    points, _ = build_pool(freq, n)
    scenarios = ScenarioSet(
        np.vstack([
            np.full(n, per_period(-0.05, freq)),
            np.full(n, per_period(INTEREST, freq)),
            np.full(n, per_period(0.12, freq)),
        ])
    )
    result = run_stochastic(
        VariablePayoutAnnuity, points, basis(freq), scenarios, n - 1,
        outputs=["pension"],
    )
    final = result.array("pension")[10, 0, :]
    assert final[0] < final[1] < final[2]
    assert final[1] == pytest.approx(points[0].pension, rel=1e-9)


# --- attribution -----------------------------------------------------------


def test_a_cohort_funds_its_own_mortality_release():
    """Worth pinning because it is easy to misread. Each model point is a
    cohort with a fractional survival weight, and its deceased members'
    assets stay with it, so the release never crosses model points. What the
    adjustment carries between them is investment experience and mortality
    *deviation* — which is why a pool running exactly to assumption shows a
    zero adjustment while members are dying."""
    freq, n = 1, 30
    _, result = run(freq, n, INTEREST)
    live = solvent(result)
    lives = result.array("lives")[:, :, 0]
    assets = result.array("assets")[:, :, 0]
    liability = result.array("liability")[:, :, 0]

    assert lives[10].sum() < lives[0].sum()          # members are dying
    # Yet each cohort's assets stay equal to its own liability. The tolerance
    # loosens with the horizon only because the tail has decayed through
    # seven orders of magnitude by then, not because the identity weakens.
    assert np.allclose(assets[live], liability[live], rtol=1e-8, atol=0.0)
    assert np.abs(result.array("adjustment")[:, 0, 0][live]).max() < 1e-12
