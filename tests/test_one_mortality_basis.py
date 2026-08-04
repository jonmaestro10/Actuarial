"""One mortality basis for the whole library.

Until now the engine had two implementations of "read a rate out of a table":
the VPLA basis promoted in docs/rfc-002-basis.md and held to bitwise parity
against the original, and a separate integer-age table that the term-life,
fixed-annuity and unit-linked templates used. Two implementations of the same
thing is the shape of defect the accuracy strategy exists to prevent, and it
meant three of five templates computed mortality through a path nothing had
validated.

There is now one. ``MortalityTable`` is a unisex, non-improving *view* over
``MortalityBasis``, and every template looks mortality up through
``Assumptions.annual_q``. This file holds the two claims that buys:

1. **Nothing moved.** The consolidation is a refactor, so the annual
   templates must produce bitwise-identical results to the table they were
   written against.
2. **They gained something.** The same templates, unchanged, now accept a
   full ``MortalityBasis`` — sex-distinct rates, generational improvement,
   the CPM2014-shaped inputs — because that is what they were always looking
   the rate up through.
"""

from datetime import date

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.stochastic import run_stochastic
from engine.core.vector import run_vectorized
from engine.data.assumptions import UNISEX, Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.mortality import MortalityBasis
from engine.data.scenarios import ScenarioSet
from engine.library.fixed_annuity import FixedAnnuity
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMxB

MIN_AGE, MAX_AGE = 20, 115
YEAR_START = 2014

UNISEX_RATES = {
    age: min(0.0006 * 1.095 ** (age - MIN_AGE), 0.5)
    for age in range(MIN_AGE, MAX_AGE + 1)
}
BY_SEX = {
    "M": dict(UNISEX_RATES),
    "F": {age: rate * 0.82 for age, rate in UNISEX_RATES.items()},
}
GENERATIONAL = {
    sex: {
        year: {age: 0.009 for age in range(MIN_AGE, MAX_AGE + 1)}
        for year in range(YEAR_START + 1, YEAR_START + 21)
    }
    for sex in ("M", "F")
}

PROJ_LEN = 35

TERM_FIELDS = {
    "id": "T1", "age_at_entry": 45, "term_years": 25, "sum_assured": 250_000.0,
    "annual_premium": 900.0, "init_pols": 1, "sex": "M",
}


def term_points(sexes=("M", "F", "M")):
    return [
        ModelPoint(**{**TERM_FIELDS, "id": f"T{i}",
                      "age_at_entry": 40 + 5 * i, "sex": sex})
        for i, sex in enumerate(sexes)
    ]


def assumptions(mortality, **kwargs):
    return Assumptions(
        mortality=mortality, lapse=0.04, interest=0.03,
        expense_per_policy=50.0, crediting_rate=0.02, amc=0.01, **kwargs
    )


# --- 1. nothing moved ------------------------------------------------------


def test_the_table_is_a_view_over_the_basis_not_a_second_lookup():
    table = MortalityTable(UNISEX_RATES)
    assert isinstance(table.basis, MortalityBasis)
    assert table.basis.sexes == [UNISEX]
    ages = np.arange(MIN_AGE, MAX_AGE + 1)
    assert np.array_equal(table.q_at(ages), table.basis.q_at(ages))
    # Same rates, to the bit, as the mapping that went in.
    assert list(table.q_at(ages)) == [UNISEX_RATES[int(a)] for a in ages]


def test_lookups_outside_the_table_still_raise():
    table = MortalityTable(UNISEX_RATES)
    with pytest.raises(KeyError, match="outside mortality table range"):
        table.q_at(np.array([MIN_AGE - 1]))
    with pytest.raises(KeyError, match="outside mortality table range"):
        table.q_at(np.array([MAX_AGE + 1]))
    with pytest.raises(KeyError, match="not in mortality table"):
        table.q(MAX_AGE + 1)
    # Clipping is the documented way to reach a masked age.
    assert int(table.clip_age(np.array([MAX_AGE + 40]))[0]) == MAX_AGE
    assert int(table.clip_age(np.array([0]))[0]) == MIN_AGE


def test_a_unisex_basis_and_the_table_agree_bitwise_in_every_template():
    """The refactor's safety net: a full basis carrying one non-improving
    set of rates has to be indistinguishable from the plain table, in the
    templates themselves and not merely in the lookup."""
    table = assumptions(MortalityTable(UNISEX_RATES))
    basis = assumptions(
        MortalityBasis({UNISEX: UNISEX_RATES}, year_start=YEAR_START,
                       use_improvement=False)
    )
    points = [
        ModelPoint(**{k: v for k, v in p.__dict__.items() if k != "sex"})
        for p in term_points()
    ]
    names = ["pols_if", "claims", "premiums", "expenses", "q_x"]
    a = run_vectorized(TermLife, points, table, proj_len=PROJ_LEN, outputs=names)
    b = run_vectorized(TermLife, points, basis, proj_len=PROJ_LEN, outputs=names)
    for name in names:
        assert np.array_equal(a.array(name), b.array(name)), name


def test_both_executors_still_agree_bitwise_after_the_consolidation():
    table = assumptions(MortalityTable(UNISEX_RATES))
    points = [
        ModelPoint(**{k: v for k, v in p.__dict__.items() if k != "sex"})
        for p in term_points()
    ]
    names = ["pols_if", "claims", "premiums", "expenses"]
    interpreted = run(TermLife, points, table, proj_len=PROJ_LEN, outputs=names)
    vectorized = run_vectorized(
        TermLife, points, table, proj_len=PROJ_LEN, outputs=names
    )
    for i in range(len(points)):
        for name in names:
            assert interpreted.per_mp[i][name] == list(
                vectorized.array(name)[:, i]
            ), name


# --- 2. what the templates gained ------------------------------------------


def test_the_annual_templates_now_take_sex_distinct_rates():
    """No template changed to get this. They already read mortality through
    the one lookup; the lookup can now tell a man from a woman."""
    basis = assumptions(
        MortalityBasis(BY_SEX, year_start=YEAR_START, use_improvement=False)
    )
    points = [
        ModelPoint(**{**TERM_FIELDS, "id": "M", "sex": "M"}),
        ModelPoint(**{**TERM_FIELDS, "id": "F", "sex": "F"}),
    ]
    result = run_vectorized(
        TermLife, points, basis, proj_len=PROJ_LEN, outputs=["q_x", "claims"]
    )
    male, female = result.array("q_x")[:, 0], result.array("q_x")[:, 1]
    in_term = male > 0.0
    assert np.all(female[in_term] < male[in_term])
    assert female[in_term] == pytest.approx(male[in_term] * 0.82, rel=1e-14)
    # Lighter mortality, fewer claims, more survivors.
    assert result.array("claims")[:, 1].sum() < result.array("claims")[:, 0].sum()


def test_the_annual_templates_now_take_an_improvement_scale():
    """Projection time is mapped onto calendar time by ``base_year``, so a
    generational scale improves the rate as the projection runs."""
    improving = assumptions(
        MortalityBasis(BY_SEX, year_start=YEAR_START, improvement=GENERATIONAL),
        base_year=2021,
    )
    flat = assumptions(
        MortalityBasis(BY_SEX, year_start=YEAR_START, use_improvement=False),
        base_year=2021,
    )
    points = term_points(("M",))
    names = ["q_x", "pols_if"]
    a = run_vectorized(TermLife, points, improving, proj_len=PROJ_LEN, outputs=names)
    b = run_vectorized(TermLife, points, flat, proj_len=PROJ_LEN, outputs=names)

    improved, plain = a.array("q_x")[:, 0], b.array("q_x")[:, 0]
    in_term = plain > 0.0
    assert np.all(improved[in_term] < plain[in_term])
    # The gap widens with projection time, which is what generational means.
    ratio = improved[in_term] / plain[in_term]
    assert np.all(np.diff(ratio) < 0.0)
    # And more policies reach the end of the cover term. (Past the term
    # `pols_if` is masked to zero, so the comparison has to sit inside it.)
    last_in_term = TERM_FIELDS["term_years"] - 1
    assert a.array("pols_if")[last_in_term, 0] > b.array("pols_if")[last_in_term, 0]


def test_base_year_defaults_to_leaving_improvement_neutral():
    """Supplying an improving basis without saying when the projection
    starts must not silently improve or worsen anything: the default base
    year is the basis's own, where the scale is neutral."""
    basis = MortalityBasis(BY_SEX, year_start=YEAR_START, improvement=GENERATIONAL)
    neutral = assumptions(basis)
    assert neutral.base_year == YEAR_START
    assert neutral.annual_q(np.array([70]), sex=["M"], offset=0)[0] == (
        pytest.approx(BY_SEX["M"][70], rel=1e-15)
    )


@pytest.mark.parametrize(
    "template,points,names",
    [
        (
            FixedAnnuity,
            [ModelPoint(id="A1", age_at_entry=55, defer_years=10,
                        premium=100_000.0, annual_payment=9_000.0,
                        init_pols=1, sex="F")],
            ["q_x", "pols_if", "payments", "death_benefits"],
        ),
        (
            TermLife,
            [ModelPoint(**{**TERM_FIELDS, "sex": "F"})],
            ["q_x", "pols_if", "claims"],
        ),
    ],
)
def test_every_annual_template_reads_the_same_lookup(template, points, names):
    """Whatever the product, mortality comes from one place — so a basis
    swapped in at the assumption level reaches all of them."""
    by_sex = assumptions(
        MortalityBasis(BY_SEX, year_start=YEAR_START, use_improvement=False)
    )
    unisex = assumptions(MortalityTable(UNISEX_RATES))
    female = run_vectorized(template, points, by_sex, proj_len=PROJ_LEN, outputs=names)
    male_rates = run_vectorized(
        template, points, unisex, proj_len=PROJ_LEN, outputs=names
    )
    lighter = female.array("q_x")[:, 0]
    heavier = male_rates.array("q_x")[:, 0]
    live = heavier > 0.0
    assert np.all(lighter[live] < heavier[live])


def test_the_unit_linked_family_reads_it_too_under_scenarios():
    """The stochastic executor puts model-point fields in columns; a
    per-policy sex vector has to line up with that rather than against the
    scenario axis."""
    basis = Assumptions(
        mortality=MortalityBasis(BY_SEX, year_start=YEAR_START,
                                 use_improvement=False),
        lapse=0.02, interest=0.03, amc=0.012,
    )
    fields = dict(
        age_at_entry=55, term_years=20, premium=100_000.0, init_pols=1,
        gmdb_guarantee=100_000.0, gmab_guarantee=0.0, gmwb_base=0.0,
        gmwb_rate=0.0, gmwb_ratchet=0.0,
    )
    points = [
        ModelPoint(id="M", sex="M", **fields),
        ModelPoint(id="F", sex="F", **fields),
    ]
    scenarios = ScenarioSet.lognormal(
        n_scenarios=4, horizon=21, drift=np.log(1.03), vol=0.18, seed=11
    )
    result = run_stochastic(
        UnitLinkedGMxB, points, basis, scenarios, 20,
        outputs=["q_x", "pols_if", "gmdb_claims"],
    )
    assert result.array("q_x").shape == (21, 2, 4)
    male, female = result.array("q_x")[:, 0, 0], result.array("q_x")[:, 1, 0]
    in_term = male > 0.0
    assert np.all(female[in_term] < male[in_term])
    # The rate does not depend on the scenario, so it must be identical
    # across them — the sex vector lined up with the right axis.
    for s in range(1, 4):
        assert np.array_equal(result.array("q_x")[:, :, 0],
                              result.array("q_x")[:, :, s])


def test_a_basis_with_several_sexes_refuses_an_ambiguous_lookup():
    basis = MortalityBasis(BY_SEX, year_start=YEAR_START, use_improvement=False)
    with pytest.raises(ValueError, match="q_at needs one of them"):
        basis.q_at(np.array([70]))
    with pytest.raises(KeyError, match="not in this basis"):
        basis.q_at(np.array([70]), sex=["X"])
