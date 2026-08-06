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

import engine.report.vm22_prescribed as module
from engine.report.vm22_prescribed import (
    ACCOUNT_VALUE_EXPENSE_RATE,
    BASE_MAINTENANCE_EXPENSE,
    EXPENSE_BASE_YEAR,
    FX_CATEGORIES,
    FX_CATEGORIES_CARRIED,
    FX_MAX_AGE,
    FX_MIN_AGE,
    FX_RATE_UP_SPLIT,
    FX_STANDARD_CONTRACT_YEARS,
    FX_STRUCTURED_MIN_AGE,
    FX_SUBSTANDARD_CONTRACT_YEARS,
    LAPSE_TABLES_CARRIED,
    TABLES_CARRIED,
    TABLES_NOT_CARRIED,
    VM22_PRESCRIBED_2026,
    PrescribedAssumptions,
    PrescribedError,
    Provisional,
    base_lapse_rate,
    fx_factor,
    maintenance_expense,
    mortality_basis,
    partial_withdrawal_rate,
    prescribed_mortality_rate,
    projection_offset,
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


def test_a_category_whose_table_is_not_carried_is_refused(monkeypatch):
    """**The refusal that matters.** §6.C.8 gives a different factor set per
    Reserving Category, and serving one category's table for another would
    be a plausible number from the wrong section, which nothing downstream
    would question — exactly how this chapter produced eight errors before
    anyone read it.

    RFC-071 carried the last of the four, so ``set(FX_CATEGORIES) -
    set(FX_CATEGORIES_CARRIED)`` is now **empty** and a loop over it asserts
    nothing. A loop that has quietly stopped running is a passing test that
    guards nothing, so the mechanism is exercised directly: the carried set
    is narrowed for the duration and the refusal has to still fire. §6.C.8
    can grow a category — it grew three between the 2023 exposure draft and
    this edition — and this is what will catch it."""
    assert set(FX_CATEGORIES_CARRIED) == set(FX_CATEGORIES)

    monkeypatch.setattr(module, "FX_CATEGORIES_CARRIED", ("accumulation",))
    for absent in set(FX_CATEGORIES) - {"accumulation"}:
        with pytest.raises(PrescribedError, match="not transcribed here"):
            fx_factor(70, "F", category=absent, contract_year=1,
                      rate_up_years=5)
    # And a category the section does not have at all is a different error,
    # which the narrowing must not turn into the first one.
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
    """The 2012 IAM Basic table, the 1983 IAM Table 'a' and Projection Scale
    G2 all live in VM-M. This module inventing them would be the same error
    as inventing the prescribed scenarios — so they arrive as arguments,
    exactly as `stochastic_exclusion_test` takes its. What *is* this
    chapter's is which of them a category calls for, and that is carried:
    see :func:`mortality_basis`."""
    for absent in ("IAM_2012", "IAM_1983", "SCALE_G2", "iam_basic_table"):
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
    docs/sources/, and it has to name the tables that are absent — otherwise
    a caller reasonably assumes §6.C is covered.

    Asserted against the **derived** lists rather than against a spelled-out
    count, because this string had been saying "Table 6.1 and Table 6.7 are
    carried; the other nine" for as long as RFC-067 had been carrying seven,
    and the test asserting `"nine" in text` was enforcing the error rather
    than catching it. The string travels into `__fingerprint__` and so into
    every run record citing this set, which is the reason a stale coverage
    claim here is worse than a stale one in a docstring."""
    text = VM22_PRESCRIBED_2026.text
    assert len(TABLES_CARRIED) == 10 and len(TABLES_NOT_CARRIED) == 1
    assert not set(TABLES_CARRIED) & set(TABLES_NOT_CARRIED)
    # Down to the verb: "the other 1 (Table 6.5) is recorded", because the
    # count reached one and a derived sentence that reads wrong is a
    # sentence a reader stops trusting.
    assert "the other 1 (Table 6.5) is recorded" in text
    for table in TABLES_CARRIED + TABLES_NOT_CARRIED:
        assert f"Table {table}" in text, table
    assert f"{len(TABLES_CARRIED)} of 11" in text
    assert "not transcribed" in text
    assert "provisional" in text
    assert VM22_PRESCRIBED_2026.label.startswith("VM-22 §6.C")


def test_what_the_set_says_it_carries_is_what_it_carries():
    """Two independent readings of the same fact, which is the only kind of
    check that catches a provenance string drifting from the code. The lapse
    and *F*x refusals name what they carry; those names have to agree with
    the table inventory the text is built from."""
    assert set(LAPSE_TABLES_CARRIED) == {"indexed", "with_glb"}   # 6.4, 6.6
    assert "6.4" in TABLES_CARRIED and "6.6" in TABLES_CARRIED
    assert "6.5" in TABLES_NOT_CARRIED
    with pytest.raises(PrescribedError):
        base_lapse_rate(0, 65, table="fixed")
    assert "6.7" in TABLES_CARRIED and "6.8" in TABLES_CARRIED
    for carried in ("6.9", "6.10", "6.11"):        # RFC-071
        assert carried in TABLES_CARRIED
    assert set(FX_CATEGORIES_CARRIED) == set(FX_CATEGORIES)


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
    assert set(FX_CATEGORIES_CARRIED) == {
        "accumulation", "payout_annuity",
        "structured_settlement_standard",
        "structured_settlement_substandard"}
    # And all four are different tables, not one served four ways. A female
    # at 70 reads 114%, 97.2%, 119% and 82% across them.
    seventy = [
        float(fx_factor(70, "F")),
        float(fx_factor(70, "F", category="payout_annuity")),
        float(fx_factor(70, "F", category="structured_settlement_standard",
                        contract_year=1)),
        float(fx_factor(70, "F", category="structured_settlement_substandard",
                        contract_year=1, rate_up_years=5)),
    ]
    assert seventy == pytest.approx([1.14, 0.972, 1.19, 0.82])
    assert len(set(seventy)) == 4


# --------------------------------------------------------------------------
# §6.C.8.iii — Tables 6.9, 6.10 and 6.11, and the axis that is not the same
# twice (RFC-071)
# --------------------------------------------------------------------------

STANDARD = "structured_settlement_standard"
SUBSTANDARD = "structured_settlement_substandard"


def test_the_structured_settlement_factors_are_the_ones_the_tables_state():
    """Spot values read from the primary text, one per table and per band.

    Table 6.9's first row is 300% in contract years 1-10 and 365%/375% at
    ≥11; Table 6.10's is 55% in every band; Table 6.11's is 55%, 55%, 70%/75%
    and 70%. The oldest rows are where the three separate: 6.10 reaches 105
    from **above** (101.7% at 104) and 6.11 from **below** (96.7%), and both
    are 100% at the cap."""
    assert fx_factor(2, "F", category=STANDARD, contract_year=1) \
        == pytest.approx(3.00)
    assert fx_factor(2, "M", category=STANDARD, contract_year=11) \
        == pytest.approx(3.75)
    assert fx_factor(30, "M", category=STANDARD, contract_year=11) \
        == pytest.approx(4.60)
    assert fx_factor(62, "F", category=STANDARD, contract_year=7) \
        == pytest.approx(1.70)

    assert fx_factor(2, "F", category=SUBSTANDARD, contract_year=31,
                     rate_up_years=5) == pytest.approx(0.55)
    assert fx_factor(62, "M", category=SUBSTANDARD, contract_year=31,
                     rate_up_years=20) == pytest.approx(2.00)
    assert fx_factor(104, "F", category=SUBSTANDARD, contract_year=1,
                     rate_up_years=1) == pytest.approx(1.017)

    assert fx_factor(2, "M", category=SUBSTANDARD, contract_year=21,
                     rate_up_years=21) == pytest.approx(0.75)
    assert fx_factor(62, "M", category=SUBSTANDARD, contract_year=31,
                     rate_up_years=40) == pytest.approx(1.80)
    assert fx_factor(104, "F", category=SUBSTANDARD, contract_year=1,
                     rate_up_years=21) == pytest.approx(0.967)


def test_the_contract_year_bands_are_not_the_same_bands_twice():
    """**The hazard this whole item exists for.** Table 6.9 bands contract
    years 1-5 / 6-10 / ≥11; Tables 6.10 and 6.11 band them 1-10 / 11-20 /
    21-30 / ≥31. Three bands against four — and the two boundaries they
    share, 1 and 11, are the trap rather than the reassurance: contract year
    11 opens the *third* band of Table 6.9 and the *second* of Tables 6.10
    and 6.11. A band index computed against the wrong list is therefore in
    range, lands a column or two off, and reads a real cell of a real table.

    The gap is computed on purpose rather than described. A female aged 62
    in contract year 11 takes **225%** from Table 6.9. Read with the
    substandard banding, contract year 11 is band two rather than band three
    and the same lookup returns **170%** — a 24% understatement of the
    prescribed mortality, from a number that looks entirely ordinary."""
    assert FX_STANDARD_CONTRACT_YEARS == (1, 6, 11)
    assert FX_SUBSTANDARD_CONTRACT_YEARS == (1, 11, 21, 31)
    assert set(FX_STANDARD_CONTRACT_YEARS) \
        & set(FX_SUBSTANDARD_CONTRACT_YEARS) == {1, 11}
    assert FX_STANDARD_CONTRACT_YEARS.index(11) == 2
    assert FX_SUBSTANDARD_CONTRACT_YEARS.index(11) == 1

    right = fx_factor(62, "F", category=STANDARD, contract_year=11)
    assert right == pytest.approx(2.25)

    # The wrong reading, computed: band index against the other list.
    wrong_band = int(np.searchsorted(
        np.asarray(FX_SUBSTANDARD_CONTRACT_YEARS), 11, side="right")) - 1
    row = [r for r in module._FX_SS_STANDARD if r[0] == 62][0]
    wrong = row[1 + 2 * wrong_band]
    assert wrong == pytest.approx(1.70)
    assert wrong / right == pytest.approx(0.756, abs=0.001)

    # And each table steps where its own header says it does, nowhere else.
    for year, expected in ((5, 1.55), (6, 1.70), (10, 1.70), (11, 2.25)):
        assert fx_factor(62, "F", category=STANDARD,
                         contract_year=year) == pytest.approx(expected)
    for year, expected in ((10, 0.90), (11, 1.35), (20, 1.35), (21, 1.75),
                           (30, 1.75), (31, 1.95)):
        assert fx_factor(62, "F", category=SUBSTANDARD, contract_year=year,
                         rate_up_years=5) == pytest.approx(expected)


def test_the_structured_tables_reach_ages_the_annuity_tables_never_do():
    """**Structured settlements cover children**, so Tables 6.9 to 6.11 floor
    at attained age **2** where Tables 6.7 and 6.8 floor at 50. Reusing
    ``FX_MIN_AGE`` here would clamp a five-year-old claimant to the age-50
    row and return 198% where the table says 318% — a plausible number, in
    the direction that understates the reserve."""
    assert FX_STRUCTURED_MIN_AGE == 2 and FX_MIN_AGE == 50

    assert fx_factor(5, "F", category=STANDARD, contract_year=1) \
        == pytest.approx(3.18)
    assert fx_factor(50, "F", category=STANDARD, contract_year=1) \
        == pytest.approx(1.98)
    # Below the floor takes the floor row, as "<=2" states.
    assert fx_factor(0, "F", category=STANDARD, contract_year=1) \
        == fx_factor(2, "F", category=STANDARD, contract_year=1)

    # The cap is shared, and is the tables' own ">=105" of 100%.
    for age in (FX_MAX_AGE, 110, 130):
        assert fx_factor(age, "M", category=STANDARD,
                         contract_year=1) == pytest.approx(1.0)
        assert fx_factor(age, "M", category=SUBSTANDARD, contract_year=1,
                         rate_up_years=25) == pytest.approx(1.0)


def test_the_substandard_factors_are_lower_and_that_is_the_ced_not_a_slip():
    """**The finding that would have been "fixed" the wrong way.** A
    substandard life is impaired, so the obvious expectation is a *higher*
    mortality factor than a standard one. Table 6.10 is 55% where Table 6.9
    is 300%, and stays strictly below it to attained age 86.

    §6.C.8.iii says why, and the reason is that the two multiply different
    rates: substandard mortality "reflect[s] the inclusion of the 'Constant
    Extra Death' (CED) methodology described in Actuarial Guideline IX-A.
    The CED shall be applied prior to the application of multiplicative Fx
    factor." The impairment is already in the rate before *F*\\ :sub:`x`
    touches it, so the factor is a correction to a loaded rate rather than
    the loading itself. Comparing the two sets as if they were the same
    quantity is the error, and it is the kind that gets a transcription
    "corrected" until it agrees with the intuition."""
    ages = np.arange(2, 106)
    standard = fx_factor(ages, "F", category=STANDARD, contract_year=1)
    substandard = fx_factor(ages, "F", category=SUBSTANDARD, contract_year=1,
                            rate_up_years=5)
    assert np.all(substandard[ages <= 86] < standard[ages <= 86])
    assert standard.max() == pytest.approx(3.75)     # age 32
    assert substandard[ages <= 86].max() == pytest.approx(1.08)   # age 86

    # The ordering reverses at the very top and the reversal is small: both
    # tables run down to 100% at 105, and Table 6.10 comes at it from above
    # (101.7% at 104) while Table 6.9 has been flat at 100% since 102.
    assert np.all(substandard[(ages >= 87) & (ages <= 97)]
                  == standard[(ages >= 87) & (ages <= 97)])
    assert np.all(substandard[(ages >= 98) & (ages <= 104)]
                  > standard[(ages >= 98) & (ages <= 104)])
    assert substandard[-1] == standard[-1] == pytest.approx(1.0)

    # Table 6.9 never dips below 100%; both substandard tables spend most of
    # their range below it. That asymmetry is the same fact seen twice.
    every_standard = np.stack([
        fx_factor(np.arange(2, 106), sex, category=STANDARD,
                  contract_year=year)
        for sex in ("F", "M") for year in FX_STANDARD_CONTRACT_YEARS])
    assert every_standard.min() == pytest.approx(1.0)
    assert substandard.min() == pytest.approx(0.55)


def test_table_6_11_is_not_monotone_across_its_contract_year_bands():
    """Every other column set rises with the contract-year band — longer on
    the books, higher factor. **Table 6.11's male columns do not**, at five
    cells and only there: at attained ages 2 to 6 the ≥31 band sits *below*
    the 21-30 band (75% against 70% at age 2, converging to 79%/78% at 6).
    At age 7 and above the male and female columns are identical throughout
    the table and the reversal disappears.

    Asserted because a test written to the expected shape would have failed
    against the real table, and the tempting fix would have been to sort the
    data until it agreed — which is how a transcription becomes a model."""
    reversals = []
    for age in range(2, 106):
        by_band = [fx_factor(age, "M", category=SUBSTANDARD,
                             contract_year=year, rate_up_years=21)
                   for year in FX_SUBSTANDARD_CONTRACT_YEARS]
        for lower, upper in zip(by_band, by_band[1:]):
            if upper < lower - 1e-12:
                reversals.append(age)
    assert reversals == [2, 3, 4, 5, 6]

    assert fx_factor(2, "M", category=SUBSTANDARD, contract_year=21,
                     rate_up_years=21) == pytest.approx(0.75)
    assert fx_factor(2, "M", category=SUBSTANDARD, contract_year=31,
                     rate_up_years=21) == pytest.approx(0.70)

    # The female column at the same cells rises, so this is not a banding
    # bug in the lookup: it is one sex, five ages, one table.
    assert fx_factor(2, "F", category=SUBSTANDARD, contract_year=21,
                     rate_up_years=21) == fx_factor(
        2, "F", category=SUBSTANDARD, contract_year=31, rate_up_years=21)
    for age in range(2, 106):
        by_band = [fx_factor(age, "F", category=SUBSTANDARD,
                             contract_year=year, rate_up_years=21)
                   for year in FX_SUBSTANDARD_CONTRACT_YEARS]
        assert all(u >= l - 1e-12 for l, u in zip(by_band, by_band[1:])), age


def test_the_rate_up_picks_the_table_rather_than_the_caller():
    """§6.C.8.iii: "The factors for Substandard lives differ by the extent of
    the age rate-up", Table 6.10 for 1 to 20 years and Table 6.11 for 21 or
    more. The caller supplies the rate-up, which is a fact about the
    contract; the table number is derived from it, which is a fact about the
    text. Naming the tables in the API instead would move the boundary to
    the call site, where it would be restated and eventually drift.

    The split is asserted at the boundary itself, because that is the only
    place an off-by-one shows: rate-ups of 20 and 21 years read different
    tables and different numbers at the same age and contract year."""
    assert FX_RATE_UP_SPLIT == 21
    twenty = fx_factor(50, "F", category=SUBSTANDARD, contract_year=1,
                       rate_up_years=20)
    twenty_one = fx_factor(50, "F", category=SUBSTANDARD, contract_year=1,
                           rate_up_years=21)
    assert twenty == pytest.approx(0.78)      # Table 6.10
    assert twenty_one == pytest.approx(0.88)  # Table 6.11
    assert fx_factor(50, "F", category=SUBSTANDARD, contract_year=1,
                     rate_up_years=1) == twenty
    assert fx_factor(50, "F", category=SUBSTANDARD, contract_year=1,
                     rate_up_years=60) == twenty_one


def test_the_second_dimension_is_required_where_it_exists_and_refused_where_it_does_not():
    """**Both directions, because both are wrong in the same way.** Omitting
    the contract year for a structured settlement would have to default to a
    band, and there is no band to default to; supplying one for Table 6.7 or
    6.8 would let a caller believe a banding had been applied when those
    tables have no such axis. Either would return a number.

    Same for the rate-up: required for a substandard life, because Tables
    6.10 and 6.11 disagree at nearly every cell, and refused for a standard
    one, because a life with an age rate-up is not standard."""
    with pytest.raises(PrescribedError, match="contract_year is required"):
        fx_factor(50, "F", category=STANDARD)
    with pytest.raises(PrescribedError, match="contract_year is required"):
        fx_factor(50, "F", category=SUBSTANDARD, rate_up_years=5)
    with pytest.raises(PrescribedError, match="no contract-year band"):
        fx_factor(50, "F", contract_year=3)
    with pytest.raises(PrescribedError, match="no contract-year band"):
        fx_factor(50, "F", category="payout_annuity", contract_year=3)

    with pytest.raises(PrescribedError, match="rate_up_years is required"):
        fx_factor(50, "F", category=SUBSTANDARD, contract_year=1)
    with pytest.raises(PrescribedError, match="no rate-up dimension"):
        fx_factor(50, "F", category=STANDARD, contract_year=1,
                  rate_up_years=5)
    with pytest.raises(PrescribedError, match="no rate-up dimension"):
        fx_factor(50, "F", rate_up_years=5)

    # A rate-up of zero is a standard life, not a substandard one at the
    # bottom of Table 6.10 — the table starts at 1.
    with pytest.raises(PrescribedError, match="is not substandard"):
        fx_factor(50, "F", category=SUBSTANDARD, contract_year=1,
                  rate_up_years=0)
    # Contract years count from 1; a 0 would read the first band as if it
    # were a year of cover.
    with pytest.raises(PrescribedError, match="contract year is 1 in the"):
        fx_factor(50, "F", category=STANDARD, contract_year=0)
    # One lookup cannot straddle two tables.
    with pytest.raises(PrescribedError, match="therefore scalar"):
        fx_factor(50, "F", category=SUBSTANDARD, contract_year=1,
                  rate_up_years=np.array([5, 25]))


def test_a_structured_settlement_has_no_guaranteed_living_benefit_split():
    """§6.C.8.iii gives six and eight columns, none of them a rider split. A
    structured settlement is a stream of payments under a claim settlement
    and has no guaranteed living benefit to buy — so the request would be
    answered from Table 6.7, which is the wrong-section failure again."""
    for category, extra in ((STANDARD, {}),
                            (SUBSTANDARD, {"rate_up_years": 5})):
        with pytest.raises(PrescribedError, match="not split by guaranteed"):
            fx_factor(50, "F", category=category, contract_year=1,
                      guaranteed_living_benefit=True, **extra)


def test_the_structured_lookup_broadcasts_and_keeps_its_contract():
    """Shape, dtype and value asserted **separately** — the standing rule
    RFC-069 and RFC-070 earned, where three bugs produced equal numbers with
    an unequal contract and every one would have passed a value-only
    comparison.

    The case that matters is a scalar age with a vector of contract years,
    which is the natural projection: one claimant, every future year. A
    lookup that broadcast in the wrong order would take the first column for
    all of them and be right for the first five entries."""
    years = np.arange(1, 15)
    got = fx_factor(62, "F", category=STANDARD, contract_year=years)
    assert got.shape == years.shape
    assert got.dtype == np.float64
    assert got[:5] == pytest.approx(1.55)
    assert got[5:10] == pytest.approx(1.70)
    assert got[10:] == pytest.approx(2.25)

    # Vector age against vector contract year, elementwise.
    ages = np.array([2, 50, 62])
    both = fx_factor(ages, "F", category=STANDARD,
                     contract_year=np.array([1, 6, 11]))
    assert both.shape == ages.shape and both.dtype == np.float64
    assert both == pytest.approx([3.00, 2.00, 2.25])

    # A scalar in gives a scalar-shaped result, as the other tables do.
    scalar = fx_factor(62, "F", category=STANDARD, contract_year=1)
    assert np.ndim(scalar) == 0
    assert np.ndim(fx_factor(62, "F")) == 0


def test_the_structured_settlement_base_table_is_not_the_2012_iam():
    """**The difference the arithmetic cannot see.** §6.C.8.i and .ii project
    the 2012 IAM Basic Mortality Table from 2012; §6.C.8.iii projects the
    **1983 IAM Table 'a'** (VM-M §1.M) from **2011**. Different table,
    different base year, and ``q (1 − G2)^n × F`` returns an ordinary-looking
    number either way.

    So the pairing is data — :data:`FX_MORTALITY_BASIS` — and the offset is
    derived from it rather than written at the call site. At a 2026
    valuation ``n`` is 14 for an accumulation contract and **15** for a
    structured settlement; taking the accumulation one applies a year too
    little improvement, which overstates mortality and understates an
    annuity reserve."""
    accumulation = mortality_basis("accumulation")
    structured = mortality_basis(STANDARD)
    assert accumulation.base_year == 2012
    assert accumulation.table == "2012 IAM Basic Mortality Table"
    assert structured.base_year == 2011
    assert structured.table == "1983 IAM Table 'a'"
    assert structured.vm_m_section == "VM-M §1.M"
    assert mortality_basis(SUBSTANDARD) == structured
    assert mortality_basis("payout_annuity") == accumulation

    assert projection_offset(2026) == 14
    assert projection_offset(2026, category=STANDARD) == 15

    # The gap, computed rather than described: one improvement year.
    q, g2, fx = 0.02, 0.01, 3.0
    right = prescribed_mortality_rate(
        q, g2, fx, projection_offset(2026, category=STANDARD))
    wrong = prescribed_mortality_rate(q, g2, fx, projection_offset(2026))
    assert wrong / right == pytest.approx(1.0 / (1.0 - g2))
    assert wrong > right

    # A year before the base is refused rather than run backwards.
    with pytest.raises(PrescribedError, match="before 2011"):
        projection_offset(2010, category=STANDARD)
    assert projection_offset(2011, category=STANDARD) == 0
    with pytest.raises(PrescribedError, match="before 2012"):
        projection_offset(2011)

    # And the categories §6.C.8 gives no Fx at all are refused by name.
    with pytest.raises(PrescribedError, match="1994 GAM"):
        mortality_basis("longevity_reinsurance")


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


# --------------------------------------------------------------------------
# §6.C.5 — the lapse tables, and the one that will not reconcile
# --------------------------------------------------------------------------

def test_the_surrender_charge_expiry_spike_is_the_shape_of_the_table():
    """**Why the table has two dimensions at all.** An indexed annuity
    written to a 60-to-69-year-old lapses at 3.5% the year before its
    surrender charge expires and 41.5% the year it does — a factor of twelve
    across one contract anniversary.

    A single-rate lapse assumption cannot express that, and a model that
    smoothed it would put the cash flow in the wrong *year* rather than
    merely get the level wrong."""
    assert base_lapse_rate(-1, 65) == pytest.approx(0.035)
    assert base_lapse_rate(0, 65) == pytest.approx(0.415)
    assert base_lapse_rate(1, 65) == pytest.approx(0.175)
    assert base_lapse_rate(0, 65) / base_lapse_rate(-1, 65) \
        == pytest.approx(11.86, abs=0.02)


def test_a_guaranteed_living_benefit_flattens_the_after_expiry_rows():
    """Table 6.6 is flat across every after-expiry row and its expiry spike
    is less than half Table 6.4's. The contract holder who bought a benefit
    that pays while they live is not leaving once the charge is gone, and
    the two tables encode different behaviour rather than one scaled."""
    after = [base_lapse_rate(d, 65, table="with_glb") for d in (1, 2, 3, 4, 5)]
    assert after == [pytest.approx(0.065)] * 5
    assert base_lapse_rate(0, 65, table="with_glb") == pytest.approx(0.140)
    assert base_lapse_rate(0, 65, table="with_glb") < base_lapse_rate(0, 65)


def test_the_bands_clamp_at_five_years_either_side():
    """"5 yrs or more after expiry" and "5 yrs or more to expiry" are the end
    rows, so nothing beyond them is extrapolated — a lapse rate ten years
    past expiry is the five-year one, as the table says."""
    assert base_lapse_rate(9, 65) == base_lapse_rate(5, 65) \
        == pytest.approx(0.070)
    assert base_lapse_rate(-9, 65) == base_lapse_rate(-5, 65) \
        == pytest.approx(0.025)
    assert base_lapse_rate(0, 85) == pytest.approx(0.235)   # 80 and above


def test_the_fixed_annuity_lapse_table_is_refused_and_here_is_why():
    """**Table 6.5 is not carried, and the reason is specific rather than
    effort.** Its second dimension is the *interest guarantee period*, not
    attained age, and its own Guidance Note supplies three worked examples.

    Two of the three reproduce exactly under the straightforward reading —
    row by years from surrender-charge expiry, column by where the contract
    sits in its IGP cycle. The third does not: Example 3's contract year 5
    comes out at **2.0%** where the text says **1.0%**.

    The first of those is now closed, by an argument that uses no reading at
    all: 25% occurs at exactly one cell of Table 6.5 and 65% at exactly one
    other, so Example 3's own years 4 and 6 pin the row offset either side of
    year 5 — forcing *1 yr after expiry*, whose values are 10.0%, 2.0% and
    75.0%. **1.0% appears in no at-or-after-expiry row of the table.** The
    reading computes 2.0% because the table leaves nothing else.

    The refusal stands regardless. Whether the Guidance Note has a typo or an
    unstated axis switch is the drafters' to say, and either way carrying the
    table would put a plausible number in every cell."""
    with pytest.raises(PrescribedError, match="interest guarantee period"):
        base_lapse_rate(0, 65, table="fixed")
    assert set(LAPSE_TABLES_CARRIED) == {"indexed", "with_glb"}
    assert "fixed" not in LAPSE_TABLES_CARRIED


def test_a_negative_age_is_refused_by_the_lapse_lookup():
    with pytest.raises(PrescribedError, match="not negative"):
        base_lapse_rate(0, -1)
