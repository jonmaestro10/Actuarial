"""Select-and-ultimate mortality.

A life underwritten last year is a better risk than one of the same age
underwritten twenty years ago, and term assurance is priced on exactly that
difference. The basis takes a select table keyed by ``(sex, duration, age at
selection)`` and falls through to the ultimate table once the select period
has run out.

Two things are being asserted here, and they pull in opposite directions:

- **Nothing moved.** An ultimate-only lookup — every caller that predates
  this — must produce the identical bits, whether or not it names a
  duration. That is asserted with ``==`` on floats, not ``approx``.
- **Something moved, in the right direction and by the right amount.** A
  select lookup must land on the published rate exactly, fall through at
  exactly the right duration, and make a freshly underwritten policy
  cheaper.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from engine.core.runner import run
from engine.core.vector import run_vectorized
from engine.data.assumptions import UNISEX, Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts
from engine.data.mortality import MortalityBasis
from engine.library.term_life import TermLife

MIN_AGE, MAX_AGE = 20, 100
SELECT_PERIOD = 5


def ultimate_q(age: int) -> float:
    """A Gompertz-ish ultimate table: smooth, increasing, inside (0, 1)."""
    return 0.0004 * 1.09 ** (age - MIN_AGE)


def select_ratio(duration: int) -> float:
    """Select rates grade linearly up to the ultimate over the period."""
    return 0.4 + 0.6 * duration / SELECT_PERIOD


def ultimate_rates(sexes=(UNISEX,)):
    return {s: {a: ultimate_q(a) for a in range(MIN_AGE, MAX_AGE + 1)}
            for s in sexes}


def select_rates(sexes=(UNISEX,), lo=MIN_AGE, hi=MAX_AGE):
    """``{sex: {duration: {age at selection: q}}}``.

    ``q_[x]+d = ratio(d) * q_{x+d}`` — the select rate at duration ``d`` for
    a life selected at age ``x``, scaled off the ultimate rate at the age it
    has actually attained.
    """
    return {
        s: {
            d: {x: select_ratio(d) * ultimate_q(min(x + d, MAX_AGE))
                for x in range(lo, hi + 1)}
            for d in range(SELECT_PERIOD)
        }
        for s in sexes
    }


def basis(**kwargs) -> MortalityBasis:
    kwargs.setdefault("year_start", 2020)
    kwargs.setdefault("use_improvement", False)
    return MortalityBasis(ultimate_rates(), **kwargs)


def select_basis(**kwargs) -> MortalityBasis:
    return basis(select=select_rates(), **kwargs)


# --- nothing moved ------------------------------------------------------


def test_an_ultimate_only_basis_ignores_duration_bit_for_bit():
    """The whole design rests on this: threading a duration through every
    lookup must be free when there are no select rates behind it."""
    ult = basis()
    assert ult.select_period == 0
    ages = np.arange(MIN_AGE, MAX_AGE + 1)
    for duration in (0, 1, 4, 5, 40):
        assert np.array_equal(
            ult.q_at(ages, duration=duration), ult.q_at(ages), equal_nan=True
        )


def test_a_select_basis_returns_the_ultimate_table_when_no_duration_is_given():
    """Omitting the duration is not "duration 0" — it is the ultimate
    lookup, so a caller written before select rates existed keeps its
    answer even against a basis that now carries them."""
    ages = np.arange(MIN_AGE, MAX_AGE + 1)
    assert np.array_equal(select_basis().q_at(ages), basis().q_at(ages))


def test_past_the_select_period_the_rate_is_the_ultimate_rate_bit_for_bit():
    ages = np.arange(MIN_AGE, MAX_AGE + 1)
    sel, ult = select_basis(), basis()
    for duration in (SELECT_PERIOD, SELECT_PERIOD + 1, 60):
        assert np.array_equal(
            sel.q_at(ages, duration=duration), ult.q_at(ages)
        )


def test_a_select_basis_costs_nothing_where_the_duration_varies_by_policy():
    """Durations arrive per model point, so the fall-through has to be
    element-wise rather than a branch on the whole array."""
    sel, ult = select_basis(), basis()
    ages = np.array([40, 41, 42, 43, 44, 45])
    durations = np.array([0, 2, 4, 5, 6, 30])
    got = sel.q_at(ages, duration=durations)
    for i, (age, d) in enumerate(zip(ages, durations)):
        if d >= SELECT_PERIOD:
            assert got[i] == ult.q_at(age)
        else:
            assert got[i] == select_ratio(d) * ultimate_q(age)


# --- the select lookup itself -------------------------------------------


def test_the_rate_is_keyed_by_age_at_selection_not_attained_age():
    """Two lives aged 45 today: one selected at 45 this year, one selected
    at 41 four years ago. Same attained age, different rows."""
    sel = select_basis()
    fresh = sel.q_at(45, duration=0)
    older = sel.q_at(45, duration=4)
    assert fresh == select_ratio(0) * ultimate_q(45)
    assert older == select_ratio(4) * ultimate_q(45)
    assert fresh < older


def test_select_rates_are_lighter_than_ultimate_at_the_same_attained_age():
    sel, ult = select_basis(), basis()
    for duration in range(SELECT_PERIOD):
        # Attained ages whose selection age is inside the select table; a
        # lower one reads a clipped row and is covered separately.
        ages = np.arange(MIN_AGE + duration, MAX_AGE + 1)
        assert np.all(sel.q_at(ages, duration=duration) < ult.q_at(ages))


def test_the_fall_through_happens_at_exactly_the_select_period():
    sel, ult = select_basis(), basis()
    last_select = sel.q_at(50, duration=SELECT_PERIOD - 1)
    first_ultimate = sel.q_at(50, duration=SELECT_PERIOD)
    assert last_select != first_ultimate
    assert last_select == select_ratio(SELECT_PERIOD - 1) * ultimate_q(50)
    assert first_ultimate == ult.q_at(50)


def test_a_select_table_whose_last_column_is_the_ultimate_rate_is_seamless():
    """Published tables usually grade into the ultimate. When the last
    select column *is* the ultimate rate, crossing the boundary is a
    no-op — which is the check that the boundary is where it claims."""
    seamless = {
        UNISEX: {
            d: {x: (ultimate_q(min(x + d, MAX_AGE)) if d == SELECT_PERIOD - 1
                    else 0.5 * ultimate_q(min(x + d, MAX_AGE)))
                for x in range(MIN_AGE, MAX_AGE + 1)}
            for d in range(SELECT_PERIOD)
        }
    }
    sel = basis(select=seamless)
    ages = np.arange(MIN_AGE + SELECT_PERIOD - 1, MAX_AGE + 1)
    assert np.array_equal(
        sel.q_at(ages, duration=SELECT_PERIOD - 1),
        sel.q_at(ages, duration=SELECT_PERIOD),
    )


def test_a_selection_age_outside_the_select_table_holds_the_nearest_row():
    """Select tables are published over a narrower range of selection ages
    than the ultimate table covers. Outside it the nearest tabulated row
    applies, the same convention the ultimate table uses above its last
    age — never a rate invented by extrapolation."""
    narrow = basis(select=select_rates(lo=30, hi=60))
    assert narrow.select_min_age == 30
    assert narrow.select_max_age == 60
    # Selected at 25, one year in: read row 30.
    assert narrow.q_at(26, duration=1) == select_ratio(1) * ultimate_q(31)
    # Selected at 80, one year in: read row 60.
    assert narrow.q_at(81, duration=1) == select_ratio(1) * ultimate_q(61)


def test_clipping_a_selection_age_can_read_a_heavier_rate_than_ultimate():
    """The honest consequence of clipping rather than raising, pinned so
    that nobody discovers it in a valuation instead of here.

    A life attained 20 at duration 4 was selected at 16. The select table
    starts at 20, so the lookup reads ``q_[20]+4`` — a rate for a life that
    has attained 24, and therefore *heavier* than the ultimate rate at 20.
    Clipping is what keeps the lookup total for ages a template masks out;
    the cure for a real block is a select table that covers its selection
    ages."""
    sel, ult = select_basis(), basis()
    assert sel.q_at(20, duration=4) == select_ratio(4) * ultimate_q(24)
    assert sel.q_at(20, duration=4) > ult.q_at(20)
    # One age higher the selection age is in range and the ordering is the
    # expected one again.
    assert sel.q_at(24, duration=4) < ult.q_at(24)


def test_the_top_of_the_ultimate_table_is_still_held_flat():
    sel = select_basis()
    assert sel.q_at(MAX_AGE, duration=99) == sel.q(
        np.int64(MAX_AGE + 30), 0, 2020
    )


def test_a_negative_duration_raises():
    with pytest.raises(ValueError, match="cannot be negative"):
        select_basis().q_at(45, duration=-1)


# --- interaction with the rest of the basis ------------------------------


def test_improvement_applies_at_the_attained_age():
    """The select dimension chooses which base rate applies; the
    improvement scale is a function of attained age and calendar year and
    is untouched by it."""
    improvement = {UNISEX: {a: 0.01 for a in range(MIN_AGE, MAX_AGE + 1)}}
    sel = MortalityBasis(
        ultimate_rates(), year_start=2020, improvement=improvement,
        select=select_rates(),
    )
    factor = 0.99 ** 10
    assert sel.q_at(45, year=2030, duration=2) == pytest.approx(
        select_ratio(2) * ultimate_q(45) * factor, rel=1e-15
    )


def test_blending_across_sexes_blends_the_select_rates():
    rates = {"M": {a: ultimate_q(a) for a in range(MIN_AGE, MAX_AGE + 1)},
             "F": {a: 0.8 * ultimate_q(a) for a in range(MIN_AGE, MAX_AGE + 1)}}
    select = {
        "M": {d: {x: select_ratio(d) * ultimate_q(min(x + d, MAX_AGE))
                  for x in range(MIN_AGE, MAX_AGE + 1)}
              for d in range(SELECT_PERIOD)},
        "F": {d: {x: 0.8 * select_ratio(d) * ultimate_q(min(x + d, MAX_AGE))
                  for x in range(MIN_AGE, MAX_AGE + 1)}
              for d in range(SELECT_PERIOD)},
    }
    blended = MortalityBasis(
        rates, year_start=2020, use_improvement=False, select=select,
        blend_male_percent=0.5,
    )
    got = blended.q_at(45, sex="M", duration=2)
    expected = 0.5 * select_ratio(2) * ultimate_q(45) * 1.8
    assert got == pytest.approx(expected, rel=1e-15)
    # And it is a *select* blend, not the ultimate one.
    assert got < blended.q_at(45, sex="M")


def test_the_sub_period_split_divides_the_select_rate():
    """Frequency and selection are independent: the select basis picks the
    year of mortality, the fractional-age split divides that year."""
    sel = select_basis()
    annual = sel.q_at(45, duration=2)
    survival = 1.0
    for k in range(12):
        survival *= 1.0 - sel.periodic_rate(45, k, 12, duration=2)
    assert survival == pytest.approx(1.0 - annual, rel=1e-14)


def test_the_sub_period_split_is_the_identity_at_freq_one():
    sel = select_basis()
    ages = np.arange(MIN_AGE, MAX_AGE + 1)
    for duration in (0, 3, 9):
        assert np.array_equal(
            sel.periodic_rate(ages, 0, 1, duration=duration),
            sel.q_at(ages, duration=duration),
        )


# --- the date-driven path -----------------------------------------------


def test_period_mortality_without_an_entry_date_is_the_ultimate_table():
    """Bitwise, against the same basis with no select rates at all — the
    guarantee that the whole VPLA parity suite is untouched."""
    dob = [dt.date(1975, 3, 14), dt.date(1960, 11, 2)]
    valuation = [dt.date(2020, 6, 30)] * 2
    args = (dob, valuation, [UNISEX, UNISEX], 12, 240)
    assert np.array_equal(
        select_basis().period_mortality(*args), basis().period_mortality(*args)
    )


def test_an_annual_period_on_the_birthday_reproduces_the_select_rate():
    """Valuation, birthday and date of selection all on 1 July: period k
    then covers exactly the year of age ``age0 + k`` at duration ``k``, so
    the date-driven split has to land on the annual select rate exactly."""
    sel = select_basis()
    dob, day = dt.date(1980, 7, 1), dt.date(2020, 7, 1)
    got = sel.period_mortality([dob], [day], [UNISEX], 1, 10, entry=[day])
    expected = np.array([sel.q_at(40 + k, duration=k) for k in range(10)])
    assert np.array_equal(got[0], expected)


def test_duration_is_read_at_the_start_of_each_piece_of_a_split_period():
    """When the anniversary and the birthday are different dates, a period
    can straddle both. The piece before the birthday carries the duration
    at the period start; the piece after it carries the duration at the
    birthday — each rate matched to the span it covers."""
    entry = dt.date(2020, 1, 1)          # anniversary: 1 January
    dob = dt.date(1980, 7, 1)            # birthday: 1 July
    # The annual period starting 1 October 2024 runs to 1 October 2025:
    # duration 4 until 1 January, 5 after; age 44 until 1 July, 45 after.
    sel = select_basis()
    got = sel.period_mortality(
        [dob], [dt.date(2024, 10, 1)], [UNISEX], 1, 1, entry=[entry]
    )[0, 0]
    # First piece: age 44, duration at 1 Oct 2024 is 4 (select).
    # Second piece: age 45, duration at the 1 Jul 2025 birthday is 5
    # (ultimate) — the anniversary passed inside the period.
    q_first = sel.q_at(44, duration=4)
    q_second = sel.q_at(45, duration=5)
    start_pct = (dt.date(2024, 10, 1) - dt.date(2024, 7, 1)).days / 365
    first_pct = (dt.date(2025, 7, 1) - dt.date(2024, 10, 1)).days / 365
    second_pct = (dt.date(2025, 10, 1) - dt.date(2025, 7, 1)).days / 365
    expected = (first_pct / (1.0 - q_first * start_pct) * q_first
                + second_pct * q_second)
    assert got == pytest.approx(expected, rel=1e-14)
    # The duration genuinely moved inside the period: had it not, the
    # second piece would have been read on the select row.
    assert q_second != sel.q_at(45, duration=4)


def test_a_select_basis_survives_more_lives_early_on():
    sel = select_basis()
    dob, day = [dt.date(1980, 7, 1)], [dt.date(2020, 7, 1)]
    selected = sel.survival_curve(dob, day, [UNISEX], 1, 30, entry=day)
    ultimate = sel.survival_curve(dob, day, [UNISEX], 1, 30)
    assert np.all(selected[0, 1:] > ultimate[0, 1:])
    # The advantage is earned during the select period and then held: past
    # it the two curves run parallel, because the rates are identical.
    ratio = selected[0, SELECT_PERIOD:] / ultimate[0, SELECT_PERIOD:]
    assert np.allclose(ratio, ratio[0], rtol=1e-13)


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "select, message",
    [
        ({UNISEX: {a: 0.1 for a in range(MIN_AGE, MAX_AGE + 1)}},
         "nesting depth"),
        ({"M": {d: {x: 0.1 for x in range(MIN_AGE, MAX_AGE + 1)}
                for d in range(3)}}, "cover sexes"),
        ({UNISEX: {d: {x: 0.1 for x in range(MIN_AGE, MAX_AGE + 1)}
                   for d in (0, 1, 3)}}, r"must run 0\.\.n-1"),
        ({UNISEX: {d: {x: 0.1 for x in range(MIN_AGE, MAX_AGE + 1)}
                   for d in (1, 2)}}, r"must run 0\.\.n-1"),
        ({UNISEX: {d: {x: 1.4 for x in range(MIN_AGE, MAX_AGE + 1)}
                   for d in range(3)}}, r"outside \[0, 1\]"),
        ({UNISEX: {0: {20: 0.1, 22: 0.1}}}, "contiguous"),
    ],
)
def test_a_malformed_select_table_raises(select, message):
    with pytest.raises(ValueError, match=message):
        basis(select=select)


def test_the_declared_select_period_must_match_the_table():
    with pytest.raises(ValueError, match="but 5 durations were given"):
        basis(select=select_rates(), select_period=3)
    with pytest.raises(ValueError, match="no select rates given"):
        basis(select_period=3)
    assert basis(select=select_rates(), select_period=SELECT_PERIOD)


def test_the_fingerprint_tells_select_bases_apart():
    from engine.core.fingerprint import fingerprint

    ult = fingerprint(basis())
    sel = fingerprint(select_basis())
    assert ult != sel
    assert fingerprint(select_basis()) == sel

    heavier = select_rates()
    heavier[UNISEX][0][45] *= 1.01
    assert fingerprint(basis(select=heavier)) != sel

    narrow = fingerprint(basis(select=select_rates(lo=30, hi=60)))
    assert narrow != sel


# --- through a product ---------------------------------------------------


def term_assumptions(mortality):
    return Assumptions(
        mortality=mortality, lapse=0.05, interest=0.03, base_year=2020
    )


def term_points(**overrides):
    row = {"id": "T1", "age_at_entry": 45, "term_years": 20,
           "sum_assured": 250_000.0, "annual_premium": 1200.0, "init_pols": 1}
    row.update(overrides)
    return from_dicts([row])


def pv_claims(mortality, **overrides):
    points = term_points(**overrides)
    model = TermLife(points[0], term_assumptions(mortality), 25)
    return model.pv_claims()


def test_a_flat_table_is_unaffected_by_the_duration_argument():
    """``TermLife`` now passes a duration on every mortality lookup. On a
    table with no select rates that must be a no-op to the last bit —
    which is what keeps tests/test_closed_form.py valid."""
    flat = MortalityTable.flat(0.01)
    model = TermLife(term_points()[0], term_assumptions(flat), 25)
    q = np.array([model.q_x(t) for t in range(20)])
    assert np.array_equal(q, np.full(20, 0.01))


def test_new_business_is_cheaper_than_an_identical_in_force_policy():
    """Same life, same age, same cover — one underwritten today, one
    underwritten ten years ago. The select basis has to price them apart,
    and in the right direction."""
    sel = select_basis()
    fresh = pv_claims(sel, duration_in_force=0)
    seasoned = pv_claims(sel, duration_in_force=10)
    assert fresh < seasoned
    # The seasoned policy is past its select period from the first period,
    # so it must price exactly as it would on the ultimate table alone.
    assert seasoned == pv_claims(basis(), duration_in_force=10)


def test_the_select_discount_runs_out_after_exactly_the_select_period():
    """A policy already ``SELECT_PERIOD`` years in force is ultimate for
    the whole projection; one a year short is not."""
    sel = select_basis()
    assert pv_claims(sel, duration_in_force=SELECT_PERIOD) == pv_claims(
        basis(), duration_in_force=SELECT_PERIOD
    )
    assert pv_claims(sel, duration_in_force=SELECT_PERIOD - 1) < pv_claims(
        basis(), duration_in_force=SELECT_PERIOD - 1
    )


def test_duration_in_force_defaults_to_new_business():
    sel = select_basis()
    assert pv_claims(sel) == pv_claims(sel, duration_in_force=0)


def test_the_two_executors_agree_bitwise_on_a_select_basis():
    """The select gather is a fancy index over three broadcast arrays,
    which is exactly the sort of thing that behaves differently one policy
    at a time than in a batch. It does not."""
    sel = select_basis()
    points = from_dicts([
        {"id": f"T{i}", "age_at_entry": 30 + 5 * i, "term_years": 20,
         "sum_assured": 100_000.0 * (i + 1), "annual_premium": 500.0 * (i + 1),
         "init_pols": 1, "duration_in_force": i}
        for i in range(6)
    ])
    outputs = ["q_x", "pols_if", "claims", "duration"]
    assumptions = term_assumptions(sel)
    interpreted = run(TermLife, points, assumptions, 25, outputs=outputs)
    vectorized = run_vectorized(TermLife, points, assumptions, 25,
                                outputs=outputs)
    for name in outputs:
        assert np.array_equal(
            np.array([mp[name] for mp in interpreted.per_mp]).T,
            np.asarray(vectorized.array(name)),
        ), name


def test_a_select_projection_walks_the_select_rows_in_order():
    """The duration a policy is valued at has to advance with projection
    time, not sit at its starting value."""
    sel = select_basis()
    model = TermLife(term_points(duration_in_force=2)[0],
                     term_assumptions(sel), 25)
    for t in range(10):
        assert model.duration(t) == 2 + t
        assert model.q_x(t) == sel.q_at(45 + t, duration=2 + t)


def test_the_run_id_separates_a_select_basis_from_an_ultimate_one():
    from engine.core.registry import record_run

    points = term_points()
    _, ultimate = record_run(TermLife, points, term_assumptions(basis()), 25,
                             outputs=["claims"])
    _, selected = record_run(TermLife, points,
                             term_assumptions(select_basis()), 25,
                             outputs=["claims"])
    assert ultimate.run_id != selected.run_id
    assert ultimate.results_digest != selected.results_digest
    assert ultimate.assumptions_digest != selected.assumptions_digest
