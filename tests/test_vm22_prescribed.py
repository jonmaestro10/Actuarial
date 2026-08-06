"""VM-22 §6.C: the prescribed assumptions, and the ones the text brackets.

The dated regulatory data half of the question C1, C2 and VM-22's
remediation each left open. RFC-050 answered the other half in the negative —
VM-20 Appendix 1.F prescribes shocks to a generator, so there is nothing to
carry — and this is the opposite answer.

Two things this suite exists to hold:

- **Provisional figures stay identifiable.** §6.C.2's escalation is written
  ``[1.025]`` and its inflation ``[2.5%]``, and in NAIC drafting the brackets
  mark a number still under discussion. Carrying them as ordinary floats
  would give them the standing of the $50/$100/$75 that are not bracketed,
  which is a claim the text does not make.
- **A factor from the wrong category is refused, not substituted.** §6.C.8
  gives a different ``Fx`` set per Reserving Category and only one is
  transcribed. Serving it for another would be a plausible number nothing
  downstream would question — the failure mode this chapter has already
  produced eight times.
"""

from __future__ import annotations

import numpy as np
import pytest

from engine.report.vm22_prescribed import (
    ACCOUNT_VALUE_EXPENSE_RATE,
    BASE_MAINTENANCE_EXPENSE,
    EXPENSE_BASE_YEAR,
    FX_CATEGORIES,
    FX_CATEGORIES_CARRIED,
    VM22_PRESCRIBED_2026,
    PrescribedAssumptions,
    PrescribedError,
    Provisional,
    fx_factor,
    maintenance_expense,
    partial_withdrawal_rate,
    prescribed_mortality_rate,
)


# --------------------------------------------------------------------------
# The brackets are the NAIC's
# --------------------------------------------------------------------------

def test_a_provisional_figure_is_a_float_that_says_it_is_provisional():
    """The arithmetic has to be unchanged — a value that behaved differently
    from the number it represents would be worse than a comment — and the
    standing has to travel with the value rather than sit beside it."""
    rate = Provisional(0.025, "§6.C.2.a, written [2.5%]")
    assert rate == 0.025
    assert rate * 4 == pytest.approx(0.1)
    assert 1.0 + rate == pytest.approx(1.025)
    assert isinstance(rate, float)
    assert "2.5%" in rate.note


def test_the_provisional_list_is_derived_and_cannot_drift():
    """A hand-kept list of which figures the text brackets is a list that
    drifts from the figures. This one is read off the values, so a figure
    that stops being provisional stops being listed by construction."""
    assert VM22_PRESCRIBED_2026.provisional_fields() == (
        "expense_escalation", "expense_inflation")
    assert VM22_PRESCRIBED_2026.has_provisional

    settled = PrescribedAssumptions(
        label="hypothetical 2027", expense_escalation=1.025,
        expense_inflation=0.025)
    assert settled.provisional_fields() == ()
    assert not settled.has_provisional
    # The numbers are identical; only their standing differs.
    assert settled.expense_escalation == \
        VM22_PRESCRIBED_2026.expense_escalation


def test_every_expense_under_this_text_is_flagged_provisional():
    """The escalation is unavoidable, so there is no expense under the 2026
    text that does not depend on a bracketed figure. A result that reported
    otherwise would be telling a reader a number is settled when it is
    not."""
    expense = maintenance_expense("payout_annuity", 2026)
    assert expense.provisional

    settled = PrescribedAssumptions(label="x", expense_escalation=1.025,
                                    expense_inflation=0.025)
    assert not maintenance_expense("payout_annuity", 2026,
                                   basis=settled).provisional


# --------------------------------------------------------------------------
# §6.C.2 — Table 6.1 and the two escalations
# --------------------------------------------------------------------------

def test_the_base_expenses_are_the_ones_table_6_1_states():
    """$50 payout, $100 accumulation with guaranteed living benefits, $75
    otherwise, and §6.C.2.c's $35 where the company does not administer."""
    assert BASE_MAINTENANCE_EXPENSE == {
        "payout_annuity": 50.0,
        "accumulation_with_glb": 100.0,
        "accumulation": 75.0,
        "not_administered": 35.0,
    }
    assert ACCOUNT_VALUE_EXPENSE_RATE == 0.0007       # "seven basis points"
    assert EXPENSE_BASE_YEAR == 2015


def test_the_two_escalations_are_not_one_escalation():
    """**The trap.** §6.C.2.a has the base "multiplied by [1.025]^(valuation
    year – 2015) **in the first projection year**, and increased by an
    assumed annual inflation rate of [2.5%] **for subsequent projection
    years**".

    Two different exponents: one from 2015 to the valuation, applied once,
    and one over the projection, compounding. Collapsing them into a single
    power is the natural simplification and gives the wrong answer for every
    valuation after 2015 — which is all of them."""
    first = maintenance_expense("payout_annuity", 2026, 0)
    assert first.escalated == pytest.approx(50.0 * 1.025 ** 11)
    assert first.amount == pytest.approx(50.0 * 1.025 ** 11)

    fifth = maintenance_expense("payout_annuity", 2026, 5)
    assert fifth.amount == pytest.approx(50.0 * 1.025 ** 11 * 1.025 ** 5)
    # The collapsed version — one exponent of (11 + 5) — happens to agree
    # here only because both bracketed rates are 2.5%. They are separately
    # stated and need not stay equal, so the test pins the structure.
    apart = PrescribedAssumptions(label="split",
                                  expense_escalation=Provisional(1.04),
                                  expense_inflation=Provisional(0.02))
    split = maintenance_expense("payout_annuity", 2026, 5, basis=apart)
    assert split.amount == pytest.approx(50.0 * 1.04 ** 11 * 1.02 ** 5)
    assert split.amount != pytest.approx(50.0 * 1.04 ** 16)


def test_the_valuation_year_of_the_base_is_a_no_op():
    """A 2015 valuation escalates by nothing, which is the boundary the
    exponent is written around."""
    assert maintenance_expense("accumulation", 2015, 0).amount \
        == pytest.approx(75.0)


def test_seven_basis_points_of_the_account_value_is_added():
    """§6.C.2.b, and it is *plus* — the per-contract amount and the
    asset-based one are both incurred, not the greater of them."""
    with_av = maintenance_expense("accumulation_with_glb", 2015, 0,
                                  account_value=200_000.0)
    assert with_av.account_value_component == pytest.approx(140.0)
    assert with_av.amount == pytest.approx(100.0 + 140.0)


def test_a_contract_the_company_does_not_administer_takes_the_flat_amount():
    """§6.C.2 reads "(a) plus (b) … **or** (c)", so (c) stands alone: $35 on
    the same escalation and no account-value component. Reading it as an
    addition would charge a rider-only assumed contract for administration
    the company is not performing."""
    outsourced = maintenance_expense("accumulation_with_glb", 2015, 0,
                                     account_value=200_000.0,
                                     administered=False)
    assert outsourced.base == 35.0
    assert outsourced.account_value_component == 0.0
    assert outsourced.amount == pytest.approx(35.0)


def test_an_unknown_contract_type_and_a_negative_year_are_refused():
    """Table 6.1 is keyed by product type and has no default. Falling back
    to one of the three would charge a contract an expense the text sets for
    a different product."""
    with pytest.raises(PrescribedError, match="no base expense"):
        maintenance_expense("whole_life", 2026)
    with pytest.raises(PrescribedError, match="before the valuation"):
        maintenance_expense("accumulation", 2026, -1)


# --------------------------------------------------------------------------
# §6.C.8 — Table 6.7, and the categories that are not carried
# --------------------------------------------------------------------------

def test_the_factors_are_the_ones_table_6_7_states():
    """Spot values read from the primary text: 150%/120% female/male without
    guaranteed living benefits at the young end, 125%/105% with them, and
    the grading that starts at 53 for males and 51 for the with-benefit
    female column."""
    assert fx_factor(50, "F") == pytest.approx(1.50)
    assert fx_factor(50, "M") == pytest.approx(1.20)
    assert fx_factor(50, "F", guaranteed_living_benefit=True) \
        == pytest.approx(1.25)
    assert fx_factor(50, "M", guaranteed_living_benefit=True) \
        == pytest.approx(1.05)
    assert fx_factor(54, "M") == pytest.approx(1.16)
    assert fx_factor(104, "M") == pytest.approx(1.017)


def test_the_table_floors_and_caps_where_it_says_it_does():
    """"<=50" is the first row and ">=105" is 100%. Both are the table's own
    statements, not extrapolation — a factor extrapolated past 105 would run
    below 100% and start *reducing* mortality at the oldest ages."""
    assert fx_factor(20, "F") == fx_factor(50, "F") == pytest.approx(1.50)
    for age in (105, 110, 130):
        assert fx_factor(age, "M") == pytest.approx(1.0)
        assert fx_factor(age, "F") == pytest.approx(1.0)


def test_the_factors_dip_below_one_and_that_is_the_table_not_a_slip():
    """**Table 6.7 is not monotone, and the trough is the interesting part.**

    The obvious guess is that the factors start high — annuitant selection
    biting hardest at the young ages — and grade down to 100% by 105. Three
    of the four columns start above 1 and all four end at 1, so the guess
    survives the endpoints and fails in the middle: every column troughs in
    the early-to-mid sixties, and the male ones go *below* 1.

    A male at 62 takes **95%** of the 2012 IAM Basic rate without a
    guaranteed living benefit and **78%** with one. Below 1 means the
    prescribed basis expects these lives to die *more slowly* than the base
    table, which is the conservative direction for a benefit that pays while
    they are alive — so it is a deliberate feature of the calibration, not a
    transcription error. It is asserted here because a test written to the
    guess would have failed against the real table, and the tempting fix
    would have been to sort the data until it agreed.
    """
    ages = np.arange(50, 106)
    male = fx_factor(ages, "M")
    male_glb = fx_factor(ages, "M", guaranteed_living_benefit=True)

    assert male.min() == pytest.approx(0.95)
    assert ages[male.argmin()] == 62
    assert male_glb.min() == pytest.approx(0.78)
    assert ages[male_glb.argmin()] == 62
    # It comes back up afterwards, which is what makes it a trough.
    assert male[-10] > male.min()

    for sex in ("M", "F"):
        for glb in (False, True):
            factors = fx_factor(ages, sex, guaranteed_living_benefit=glb)
            assert factors[-1] == pytest.approx(1.0)


def test_a_guaranteed_living_benefit_never_raises_the_factor():
    """The selection the table encodes: a contract holder who bought a
    benefit that pays while they live is expected to live longer, so the
    with-benefit factor is at or below the without-benefit one at every age
    and for both sexes.

    A monotonicity that *does* hold, asserted across the whole range — so a
    transcription slip that swapped two columns shows up as a reversal even
    where the age-shape would not catch it."""
    ages = np.arange(50, 106)
    for sex in ("M", "F"):
        without = fx_factor(ages, sex)
        with_glb = fx_factor(ages, sex, guaranteed_living_benefit=True)
        assert np.all(with_glb <= without + 1e-12), sex
        assert np.any(with_glb < without - 1e-12), sex


def test_a_category_whose_table_is_not_carried_is_refused():
    """**The refusal that matters.** §6.C.8 gives a different factor set per
    Reserving Category and only the Accumulation one is transcribed. Serving
    it for a payout annuity would be a plausible number from the wrong
    section, which nothing downstream would question — and that is exactly
    how this chapter produced eight errors before anyone read it."""
    assert set(FX_CATEGORIES_CARRIED) < set(FX_CATEGORIES)

    for absent in set(FX_CATEGORIES) - set(FX_CATEGORIES_CARRIED):
        with pytest.raises(PrescribedError, match="not transcribed here"):
            fx_factor(70, "F", category=absent)
    # And a category the section does not have at all is a different error.
    with pytest.raises(PrescribedError, match="no factor set"):
        fx_factor(70, "F", category="longevity_reinsurance")


def test_a_sex_the_table_is_not_quoted_by_is_refused():
    with pytest.raises(PrescribedError, match="quoted by sex"):
        fx_factor(70, "U")


# --------------------------------------------------------------------------
# §6.C.8.i — the formula, over data that belongs to VM-M
# --------------------------------------------------------------------------

def test_the_mortality_formula_projects_then_adjusts():
    """§6.C.8.i: ``q_x^(2012+n) = q_x^2012 (1 − G2_x)^n × F_x``.

    Where the factor sits is the whole content: *outside* the improvement,
    multiplying the projected rate. Applying it to the base rate before
    improving gives a different number at every n except zero — and the same
    number at n=0, which is why a test at outset alone would not tell them
    apart."""
    q, g2, fx = 0.01, 0.01, 1.5
    assert prescribed_mortality_rate(q, g2, fx, 0) == pytest.approx(0.015)
    assert prescribed_mortality_rate(q, g2, fx, 10) == pytest.approx(
        0.01 * 0.99 ** 10 * 1.5)

    # The wrong order agrees at outset and diverges thereafter.
    wrong = (q * fx) * (1.0 - g2) ** 10
    assert wrong == pytest.approx(prescribed_mortality_rate(q, g2, fx, 10))
    # ...which is why the guard has to be on a *scale that varies by age*.
    ages = np.array([0.01, 0.02])
    both = prescribed_mortality_rate(np.array([0.01, 0.01]), ages,
                                     np.array([1.5, 1.2]), 10)
    assert both[0] > both[1]


def test_the_prescribed_data_that_belongs_to_vm_m_stays_an_argument():
    """The 2012 IAM Basic table and Projection Scale G2 live in VM-M. This
    module inventing them would be the same error as inventing the
    prescribed scenarios — so they arrive as arguments, exactly as
    `stochastic_exclusion_test` takes its."""
    import engine.report.vm22_prescribed as module

    for absent in ("IAM_2012", "SCALE_G2", "iam_basic_table"):
        assert not hasattr(module, absent), (
            f"{absent} exists now — if VM-M's tables have been carried, "
            f"wire them here and delete this test"
        )


def test_an_impossible_improvement_scale_or_direction_is_refused():
    """A G2 at or above 1 makes ``(1 − G2)^n`` change sign with n, which
    would produce negative mortality at odd durations and positive at even.
    A negative n projects backwards from 2012, which the formula does not
    do."""
    with pytest.raises(PrescribedError, match="in \\[0, 1\\)"):
        prescribed_mortality_rate(0.01, 1.0, 1.0, 5)
    with pytest.raises(PrescribedError, match="in \\[0, 1\\)"):
        prescribed_mortality_rate(0.01, -0.01, 1.0, 5)
    with pytest.raises(PrescribedError, match="projects forward"):
        prescribed_mortality_rate(0.01, 0.01, 1.0, -1)


def test_the_dated_set_says_what_it_carries_and_what_it_does_not():
    """The set's own text is the provenance a reader gets without opening
    docs/sources/, and it has to name the nine tables that are absent —
    otherwise a caller reasonably assumes §6.C is covered."""
    text = VM22_PRESCRIBED_2026.text
    assert "Table 6.1" in text and "Table 6.7" in text
    assert "nine" in text and "not" in text
    assert "provisional" in text
    assert VM22_PRESCRIBED_2026.label.startswith("VM-22 §6.C")


# --------------------------------------------------------------------------
# Tables 6.2, 6.3 and 6.8 — three more of the eleven
# --------------------------------------------------------------------------

def test_the_payout_annuity_factors_are_table_6_8():
    """§6.C.8.ii. Spot values read from the primary text: 125%/100% at the
    young ages, and the same trough in the early sixties Table 6.7 has —
    103% female and 95% male at 62."""
    assert fx_factor(50, "F", category="payout_annuity") \
        == pytest.approx(1.25)
    assert fx_factor(50, "M", category="payout_annuity") \
        == pytest.approx(1.00)
    assert fx_factor(62, "F", category="payout_annuity") \
        == pytest.approx(1.03)
    assert fx_factor(62, "M", category="payout_annuity") \
        == pytest.approx(0.95)
    assert fx_factor(110, "M", category="payout_annuity") == pytest.approx(1.0)


def test_the_payout_table_is_not_split_by_guaranteed_living_benefit():
    """§6.C.8 gives Table 6.8 one pair of columns. Asking for a split it
    does not have would otherwise be answered from the accumulation table —
    which is the same wrong-section failure the category refusal exists for,
    one level down."""
    with pytest.raises(PrescribedError, match="not split by guaranteed"):
        fx_factor(62, "F", category="payout_annuity",
                  guaranteed_living_benefit=True)


def test_the_two_carried_factor_sets_are_different_tables():
    """A regression against the obvious implementation slip: serving one
    table for both categories. They agree nowhere useful — 125% against
    150% for a female at 50 — so a single wrong lookup shows up here."""
    assert fx_factor(50, "F") != pytest.approx(
        fx_factor(50, "F", category="payout_annuity"))
    assert set(FX_CATEGORIES_CARRIED) == {"accumulation", "payout_annuity"}
    still_absent = set(FX_CATEGORIES) - set(FX_CATEGORIES_CARRIED)
    assert still_absent == {"structured_settlement_standard",
                            "structured_settlement_substandard"}
    for absent in still_absent:
        with pytest.raises(PrescribedError, match="not transcribed here"):
            fx_factor(70, "F", category=absent)


def test_the_withdrawal_bands_are_steps_and_are_not_interpolated():
    """§6.C.4's bands are the text's own — "59 and under", "60 – 64", … —
    so 59 and 60 take different rates and nothing between them is invented.

    Interpolating would produce a rate the text does not contain at every
    age between the band edges, which is the tempting smoothing and the one
    a prescribed table exists to prevent."""
    assert partial_withdrawal_rate(59, qualified=True) == pytest.approx(0.0165)
    assert partial_withdrawal_rate(60, qualified=True) == pytest.approx(0.0210)
    assert partial_withdrawal_rate(64, qualified=True) == pytest.approx(0.0210)
    assert partial_withdrawal_rate(65, qualified=True) == pytest.approx(0.0235)
    assert partial_withdrawal_rate(80, qualified=True) == pytest.approx(0.0630)
    assert partial_withdrawal_rate(99, qualified=True) == pytest.approx(0.0630)
    # The step is a step: no value between the two band rates is produced.
    ages = np.arange(50, 66)
    rates = partial_withdrawal_rate(ages, qualified=True)
    assert set(np.round(rates, 6)) == {0.0165, 0.0210, 0.0235}


def test_the_non_qualified_table_barely_moves_and_that_is_the_tax_code():
    """Tables 6.2 and 6.3 are two tables rather than one with an adjustment,
    and the reason shows in the numbers: the qualified rates grade from
    1.65% to 6.30% with age, and the non-qualified ones sit at 1.60% at
    every age without a guaranteed living benefit.

    Required minimum distributions drive withdrawals on qualified money and
    there is no equivalent pressure on non-qualified. Asserted because a
    module that applied one table with a factor would look reasonable and
    would be wrong at every age above 65."""
    ages = np.arange(50, 91)
    qualified = partial_withdrawal_rate(ages, qualified=True)
    other = partial_withdrawal_rate(ages, qualified=False)
    assert np.all(other == pytest.approx(0.0160))
    assert qualified.max() / qualified.min() == pytest.approx(3.82, abs=0.02)

    with_glb = partial_withdrawal_rate(ages, qualified=False,
                                       guaranteed_living_benefit=True)
    assert set(np.round(with_glb, 6)) == {0.0115, 0.0165}


def test_a_guaranteed_living_benefit_lowers_the_qualified_withdrawal_rate():
    """A contract holder who bought a benefit they have not yet exercised
    withdraws less, because withdrawing erodes it. True at every band in the
    qualified table, which is the check that the two columns are the right
    way round."""
    ages = np.arange(50, 91)
    without = partial_withdrawal_rate(ages, qualified=True)
    with_glb = partial_withdrawal_rate(ages, qualified=True,
                                       guaranteed_living_benefit=True)
    assert np.all(with_glb < without)


def test_a_negative_age_is_refused():
    with pytest.raises(PrescribedError, match="not negative"):
        partial_withdrawal_rate(-1, qualified=True)
