"""Sub-annual projection for the age-indexed templates.

``PayoutAnnuity`` and ``VariablePayoutAnnuity`` run at any payment frequency
because they are driven by *dates*: a date of birth, a valuation date, and
``MortalityBasis.period_mortality`` splitting each period across the two ages
it straddles. The term-life and fixed-annuity templates are driven by an
*entry age* instead, which is what pricing work has, and had no way to run
anything but annually.

They do now. A year of age is split into ``freq`` sub-periods by
``MortalityBasis.periodic_rate`` — the dateless counterpart, which needs only
an age — and every other annual assumption has a matching per-period view on
``Assumptions``.

Two claims, and this file is organised around them:

1. **``freq = 1`` is the identity.** Every per-period view returns the annual
   assumption *bit for bit*, so the entire existing golden suite stands as
   the proof that making the templates frequency-aware moved nothing. The
   tests here pin the identities themselves.
2. **The split is exact where it has to be.** A year of sub-period survival
   multiplies back to the annual survival, so a monthly projection has the
   same policies in force at each anniversary as the annual one. What
   changes is *when* money moves inside the year, which is the whole point.
"""

import numpy as np
import pytest

from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity
from engine.library.term_life import TermLife
from vpla_reference import udd_period_mortality

MIN_AGE, MAX_AGE = 20, 110
QX = {age: min(0.0006 * 1.095 ** (age - MIN_AGE), 0.5)
      for age in range(MIN_AGE, MAX_AGE + 1)}
TABLE = MortalityTable(QX)

INTEREST = 0.03
LAPSE = 0.06
TERM_YEARS = 20

TERM_POINT = ModelPoint(
    id="T1", age_at_entry=45, term_years=TERM_YEARS, sum_assured=250_000.0,
    annual_premium=1_200.0, init_pols=1,
)
ANNUITY_POINT = ModelPoint(
    id="A1", age_at_entry=60, defer_years=5, premium=100_000.0,
    annual_payment=12_000.0, init_pols=1,
)


def assumptions(freq=1, fractional_ages="udd"):
    return Assumptions(
        mortality=TABLE, lapse=LAPSE, interest=INTEREST,
        expense_per_policy=60.0, crediting_rate=0.02, freq=freq,
        fractional_ages=fractional_ages,
    )


def project(template, point, freq, years, outputs):
    return run_vectorized(
        template, [point], assumptions(freq), proj_len=years * freq - 1,
        outputs=outputs,
    )


# --- 1. freq = 1 is the identity -------------------------------------------


def test_every_per_period_view_is_exact_at_annual_frequency():
    """Not "close to" the annual assumption — the same float. This is what
    licenses the annual golden suite to stand as the regression test for the
    whole change."""
    a = assumptions(freq=1)
    ages = np.arange(40, 90)
    assert list(a.periodic_q(ages, 0)) == list(a.annual_q(ages))
    assert list(a.periodic_q(ages, 7)) == list(a.annual_q(ages, offset=7))
    assert a.periodic_lapse() == LAPSE
    assert a.per_period(1_234.5) == 1_234.5
    for t in (0, 1, 5, 40):
        assert a.discount(t) == (1.0 + INTEREST) ** (-t)
        assert a.years_elapsed(t) == t
        assert a.sub_period(t) == 0
    assert a.periods(TERM_YEARS) == TERM_YEARS


def test_an_annual_run_is_unchanged_by_the_frequency_machinery():
    names = ["pols_if", "claims", "premiums", "expenses", "q_x", "v"]
    result = project(TermLife, TERM_POINT, 1, 30, names)
    # Rebuilt by hand from the table, the way the template read it before.
    survivors, expected_pols = 1.0, []
    for t in range(30):
        expected_pols.append(survivors)
        if t < TERM_YEARS:
            q = QX[min(TERM_POINT.age_at_entry + t, MAX_AGE)]
            survivors = survivors * (1 - q) * (1 - LAPSE)
            if t + 1 >= TERM_YEARS:
                survivors = 0.0
        else:
            survivors = 0.0
    assert list(result.array("pols_if")[:, 0]) == expected_pols


@pytest.mark.parametrize("freq", [2, 4, 12])
def test_bad_frequencies_are_refused(freq):
    assumptions(freq=freq)  # these are fine
    for bad in (0, -1, 5, 7, 24):
        with pytest.raises(ValueError, match="must divide 12"):
            assumptions(freq=bad)


# --- 2. the split is exact where it has to be ------------------------------


@pytest.mark.parametrize("freq", [2, 4, 12])
@pytest.mark.parametrize("method", ["udd", "constant_force"])
def test_a_year_of_sub_periods_multiplies_back_to_the_annual_rate(freq, method):
    """The property that matters: splitting the year cannot change how many
    policies reach the end of it. Both splits telescope exactly."""
    ages = np.arange(40, 100)
    survival = np.ones(len(ages))
    for k in range(freq):
        survival = survival * (
            1.0 - TABLE.periodic_rate(ages, k, freq, method=method)
        )
    annual = 1.0 - TABLE.q_at(ages)
    assert survival == pytest.approx(annual, rel=1e-14)


@pytest.mark.parametrize("freq", [2, 12])
def test_the_udd_split_is_vplas_own_formula(freq):
    """The dateless split is the same statement as the first term of the
    basis's date-driven one, with ``pct_before = k/m`` and
    ``pct_within = 1/m`` — so the two ways of reaching a sub-annual rate
    agree rather than merely resembling each other."""
    ages = np.array([70])
    q = float(TABLE.q_at(ages)[0])
    for k in range(freq):
        got = float(TABLE.periodic_rate(ages, k, freq)[0])
        want = udd_period_mortality(k / freq, 1.0 / freq, 0.0, q, 0.0)
        assert got == pytest.approx(want, rel=1e-15)


@pytest.mark.parametrize("method", ["udd", "constant_force"])
def test_a_sub_annual_rate_is_always_a_probability(method):
    """Unlike the straddling split (docs/vpla-review.md §6.15), splitting
    *within* a year of age cannot exceed 1 — not even at ``q = 1``, where the
    final sub-period rate is exactly 1."""
    certain = MortalityTable({age: 1.0 for age in range(MIN_AGE, MAX_AGE + 1)})
    ages = np.arange(MIN_AGE, MAX_AGE + 1)
    for freq in (2, 4, 12):
        rates = np.stack(
            [certain.periodic_rate(ages, k, freq, method=method)
             for k in range(freq)]
        )
        assert np.all(rates >= 0.0)
        assert np.all(rates <= 1.0)
        # Exactly 1 in real arithmetic; the last sub-period's denominator is
        # a subtraction that rounds, so allow the ulp.
        assert rates[-1] == pytest.approx(1.0, rel=1e-15)


def test_udd_and_a_constant_force_disagree_within_the_year_but_not_across_it():
    ages = np.array([85])
    freq = 12
    udd = np.array([float(TABLE.periodic_rate(ages, k, freq)[0])
                    for k in range(freq)])
    force = np.array([
        float(TABLE.periodic_rate(ages, k, freq, method="constant_force")[0])
        for k in range(freq)
    ])
    assert np.all(np.diff(udd) > 0.0)          # UDD hardens through the year
    assert np.allclose(force, force[0])        # a constant force does not
    assert udd[0] < force[0] and udd[-1] > force[-1]
    assert np.prod(1 - udd) == pytest.approx(np.prod(1 - force), rel=1e-14)


# --- what a frequency actually buys ----------------------------------------


@pytest.mark.parametrize("freq", [2, 4, 12])
def test_the_same_policies_are_in_force_at_every_anniversary(freq):
    """A monthly projection and an annual one must agree at the year ends —
    exactly the consequence of the split telescoping. If they did not, the
    frequency would be changing the decrement basis rather than the timing."""
    annual = project(TermLife, TERM_POINT, 1, 30, ["pols_if"])
    fine = project(TermLife, TERM_POINT, freq, 30, ["pols_if"])
    for year in range(30):
        assert fine.array("pols_if")[year * freq, 0] == pytest.approx(
            annual.array("pols_if")[year, 0], rel=1e-12
        ), f"year {year}"


def exits(freq):
    """Total deaths and total lapses over the projection."""
    r = project(TermLife, TERM_POINT, freq, 30,
                ["pols_if", "q_x", "lapse_rate", "claims", "v"])
    pols = r.array("pols_if")[:, 0]
    q, lapse = r.array("q_x")[:, 0], r.array("lapse_rate")[:, 0]
    step = (1.0 + INTEREST) ** (-1.0 / freq)   # end-of-period settlement
    return dict(
        deaths=float(np.sum(pols * q)),
        lapses=float(np.sum(pols * (1 - q) * lapse)),
        pv_claims=float(np.sum(r.array("claims")[:, 0] * r.array("v")[:, 0] * step)),
    )


def test_a_finer_step_shifts_exits_from_deaths_to_lapses():
    """The substantive consequence of running sub-annually, and not the one
    that first comes to mind.

    Total exits are unchanged — identical, because the split telescopes. What
    changes is which decrement claims them. An annual step applies the whole
    year's mortality to the whole year's opening in-force and only then
    removes the lapses; a monthly step interleaves them, so policyholders who
    lapse in January are not exposed to February's mortality. Finer is the
    more correct answer: it converges on the continuous multi-decrement
    result, where the two forces compete throughout.

    So the present value of claims *falls* with frequency. The earlier
    settlement of each claim pushes the other way, and loses.
    """
    annual, quarterly, monthly = exits(1), exits(4), exits(12)

    total = [e["deaths"] + e["lapses"] for e in (annual, quarterly, monthly)]
    assert total[1] == pytest.approx(total[0], rel=1e-12)
    assert total[2] == pytest.approx(total[0], rel=1e-12)

    deaths = [e["deaths"] for e in (annual, quarterly, monthly)]
    lapses = [e["lapses"] for e in (annual, quarterly, monthly)]
    assert deaths == sorted(deaths, reverse=True)
    assert lapses == sorted(lapses)
    assert (deaths[0] - deaths[2]) / deaths[0] > 0.02   # a real shift

    pv = [e["pv_claims"] for e in (annual, quarterly, monthly)]
    assert pv == sorted(pv, reverse=True)
    assert (pv[0] - pv[2]) / pv[0] < 0.05               # timing, not a basis change


@pytest.mark.parametrize("freq", [4, 12])
def test_premiums_are_worth_less_when_they_arrive_in_instalments(freq):
    """The mirror image, and the reason a monthly-pay contract is priced
    differently: premiums in advance are most valuable paid annually."""
    def pv_premiums(f):
        r = project(TermLife, TERM_POINT, f, 30, ["premiums", "v"])
        return float(np.sum(r.array("premiums")[:, 0] * r.array("v")[:, 0]))

    annual, fine = pv_premiums(1), pv_premiums(freq)
    assert fine < annual
    assert (annual - fine) / annual < 0.05


@pytest.mark.parametrize("freq", [4, 12])
def test_a_year_of_instalments_sums_to_the_annual_amount_before_discounting(freq):
    """Splitting the payment must not change how much is paid, only when."""
    annual = project(TermLife, TERM_POINT, 1, 30, ["premiums", "pols_if"])
    fine = project(TermLife, TERM_POINT, freq, 30, ["premiums"])
    # Within the first period of a year the in-force is the same, so the
    # first instalment is exactly 1/freq of the annual payment.
    assert fine.array("premiums")[0, 0] == pytest.approx(
        annual.array("premiums")[0, 0] / freq, rel=1e-14
    )
    # Across the year the in-force decrements, so the total is slightly less.
    year_one = fine.array("premiums")[:freq, 0].sum()
    assert year_one < annual.array("premiums")[0, 0]
    assert year_one > annual.array("premiums")[0, 0] * 0.95


@pytest.mark.parametrize("freq", [4, 12])
def test_the_deferred_annuity_runs_sub_annually_too(freq):
    """Same machinery, different product: the deferral vests at the same
    date and the payout is an annuity-due, so paying it in instalments is
    worth less — the ``ä - ä^(m)`` relation, from the projection rather than
    from a factor."""
    names = ["pols_if", "payments", "v", "in_defer"]
    annual = project(FixedAnnuity, ANNUITY_POINT, 1, 40, names)
    fine = project(FixedAnnuity, ANNUITY_POINT, freq, 40, names)

    # Vesting lands on the same anniversary.
    defer_periods = ANNUITY_POINT.defer_years * freq
    assert fine.array("in_defer")[defer_periods - 1, 0] == 1.0
    assert fine.array("in_defer")[defer_periods, 0] == 0.0

    # Survivors agree at each anniversary.
    for year in range(40):
        assert fine.array("pols_if")[year * freq, 0] == pytest.approx(
            annual.array("pols_if")[year, 0], rel=1e-12
        )

    pv_annual = float(np.sum(annual.array("payments")[:, 0] * annual.array("v")[:, 0]))
    pv_fine = float(np.sum(fine.array("payments")[:, 0] * fine.array("v")[:, 0]))
    assert pv_fine < pv_annual

    # ä - ä^(m) ~ (m - 1) / 2m per unit of annual payment, but the annuity is
    # deferred, so the whole difference arrives discounted and conditional on
    # surviving to vesting.
    gap = (pv_annual - pv_fine) / ANNUITY_POINT.annual_payment
    to_vesting = float(annual.array("pols_if")[ANNUITY_POINT.defer_years, 0])
    discounted = (1.0 + INTEREST) ** (-ANNUITY_POINT.defer_years)
    assert gap == pytest.approx(
        (freq - 1) / (2 * freq) * discounted * to_vesting, rel=0.02
    )


def test_the_deferral_fund_accrues_by_elapsed_time_not_by_period_count():
    """The crediting rate is annual, so five years of deferral is five years
    of growth whatever the projection frequency."""
    for freq in (1, 4, 12):
        result = project(FixedAnnuity, ANNUITY_POINT, freq, 40,
                         ["fund_eoy_per_pol"])
        at_vesting = result.array("fund_eoy_per_pol")[
            ANNUITY_POINT.defer_years * freq - 1, 0
        ]
        assert at_vesting == pytest.approx(
            ANNUITY_POINT.premium * 1.02 ** ANNUITY_POINT.defer_years, rel=1e-12
        )


def test_a_sub_annual_projection_still_conserves_decrements():
    freq = 12
    result = project(
        TermLife, TERM_POINT, freq, 30, ["pols_if", "q_x", "lapse_rate"]
    )
    pols = result.array("pols_if")[:, 0]
    q = result.array("q_x")[:, 0]
    lapse = result.array("lapse_rate")[:, 0]
    for t in range(TERM_YEARS * freq - 1):
        deaths = pols[t] * q[t]
        lapses = pols[t] * (1 - q[t]) * lapse[t]
        assert pols[t + 1] == pytest.approx(pols[t] - deaths - lapses, rel=1e-12)


def test_a_constant_force_basis_also_lands_on_the_anniversaries():
    """The choice of fractional-age assumption changes the path through the
    year, not where it ends up."""
    freq = 12
    udd = run_vectorized(
        TermLife, [TERM_POINT], assumptions(freq, "udd"),
        proj_len=30 * freq - 1, outputs=["pols_if"],
    )
    force = run_vectorized(
        TermLife, [TERM_POINT], assumptions(freq, "constant_force"),
        proj_len=30 * freq - 1, outputs=["pols_if"],
    )
    mid_year = udd.array("pols_if")[6, 0], force.array("pols_if")[6, 0]
    assert mid_year[0] != mid_year[1]
    for year in range(30):
        assert udd.array("pols_if")[year * freq, 0] == pytest.approx(
            force.array("pols_if")[year * freq, 0], rel=1e-12
        )


def test_an_unknown_fractional_age_assumption_is_refused():
    with pytest.raises(ValueError, match="udd.*constant_force"):
        TABLE.periodic_rate(np.array([70]), 0, 12, method="linear")
