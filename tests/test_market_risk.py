"""Solvency II market risk — RFC-026.

Two layers, as everywhere else here. The first is **transcription**: the
prescribed numbers are checked against the Official Journal text, at named
maturities and named credit quality steps, because a table typed out of a
regulation is data and the only test of data is that it says what the
source says. The second is **behaviour**: the identities the standard
implies, the invariants a capital calculation has to satisfy, and the
measured results RFC-026 reports.
"""

import datetime as dt

import numpy as np
import pytest

from engine.data.rates import YieldCurve
from engine.report.embedded_value import (
    BalanceSheet, duration_matched_assets, macaulay_duration, present_value,
)
from engine.report.market_risk import (
    CALIBRATIONS, DELEGATED_2015, DELEGATED_2026, MARKET_RISKS,
    EquityExposure, InterestShockTable, ShockResult,
    LEGISLATIVE_OPTIONS, NEGATIVE_RATE_RULES,
    calibration_for, concentration_capital,
    currency_capital, curve_from_spot_rates, equity_capital,
    insurer_concentration_factor, interest_rate_capital,
    market_correlation, market_risk, property_capital, spot_rates,
    spread_capital, spread_factor, stressed_curve, symmetric_adjustment,
    unrated_spread_factor,
)

FLAT = YieldCurve.flat(0.03, freq=1)
#: A twenty-five year level annuity in payment — the long-liability book
#: every measurement below is made on.
ANNUITY = np.full(25, 100.0)


# --------------------------------------------------------------------------
# Transcription: the tables against the Official Journal
# --------------------------------------------------------------------------

def test_article_166_table_is_the_published_one():
    """Article 166(1), spot-checked at the ends and at three interior rows."""
    up = DELEGATED_2015.interest.up
    for maturity, expected in ((1, 0.70), (2, 0.70), (3, 0.64), (10, 0.42),
                               (15, 0.33), (20, 0.26), (90, 0.20)):
        assert up.relative_at(float(maturity)) == pytest.approx(expected)
    # "For maturities shorter than 1 year, the increase shall be 70 %."
    assert up.relative_at(0.25) == pytest.approx(0.70)
    # "For maturities longer than 90 years, the increase shall be 20 %."
    assert up.relative_at(120.0) == pytest.approx(0.20)
    # "the value of the increase shall be linearly interpolated"
    assert up.relative_at(3.5) == pytest.approx(0.5 * (0.64 + 0.59))
    # There is no parallel part before 2026/269.
    assert up.parallel_at(np.arange(1.0, 91.0)).max() == 0.0


def test_article_167_table_is_the_published_one():
    """Article 167(1). Note 13 to 20 is not monotone — it falls to 27% at
    fifteen years and rises again to 29%, which a transcription that
    "tidied" the table would lose."""
    down = DELEGATED_2015.interest.down
    for maturity, expected in ((1, 0.75), (2, 0.65), (5, 0.46), (10, 0.31),
                               (15, 0.27), (16, 0.28), (19, 0.29), (20, 0.29),
                               (90, 0.20)):
        assert down.relative_at(float(maturity)) == pytest.approx(expected)
    assert down.relative_at(0.5) == pytest.approx(0.75)
    assert down.relative_at(150.0) == pytest.approx(0.20)
    series = down.relative_at(np.arange(13.0, 21.0))
    assert series.argmin() == 2   # fifteen years, then back up


def test_2026_interest_tables_are_the_published_ones():
    """Article 166 and 167 as replaced by 2026/269 points (43) and (44)."""
    up, down = DELEGATED_2026.interest.up, DELEGATED_2026.interest.down
    for maturity, s, b in ((1, 0.61, 0.0214), (10, 0.30, 0.0105),
                           (20, 0.25, 0.0088), (30, 0.20, 0.0069),
                           (50, 0.21, 0.0073)):
        assert up.relative_at(float(maturity)) == pytest.approx(s)
        assert up.parallel_at(float(maturity)) == pytest.approx(b)
    for maturity, s, b in ((1, 0.58, 0.0116), (7, 0.37, 0.0063),
                           (20, 0.50, 0.0050), (50, 0.65, 0.0018)):
        assert down.relative_at(float(maturity)) == pytest.approx(s)
        assert down.parallel_at(float(maturity)) == pytest.approx(b)
    # "for maturities m of at least 60 years, b shall be equal to 0 %; for
    # maturities of at least 90 years, s shall be equal to 20 %"
    for table in (up, down):
        assert table.parallel_at(60.0) == 0.0
        assert table.parallel_at(75.0) == 0.0
        assert table.relative_at(90.0) == pytest.approx(0.20)
        assert table.relative_at(200.0) == pytest.approx(0.20)


def test_the_2026_down_shock_lengthens_where_the_2015_one_shortens():
    """The shape, not just the level, is different.

    Under 2015/35 the relative decrease falls monotonically from 75% at one
    year to 27% at fifteen. Under 2026/269 it falls to 37% at seven years
    and then **rises** all the way to 65% at fifty — so the new calibration
    bites hardest exactly where a long annuity book lives.
    """
    years = np.arange(7.0, 51.0)
    old = DELEGATED_2015.interest.down.relative_at(years)
    new = DELEGATED_2026.interest.down.relative_at(years)
    assert old[-1] < old[0]
    assert new[-1] > new[0]
    assert new.max() == pytest.approx(0.65)
    assert np.all(new[7:] > old[7:])


def test_spread_table_is_the_published_one():
    """Article 176(3), at the corners of every band."""
    cases = {
        # (credit quality step, duration): stress
        (0, 1.0): 0.009, (0, 5.0): 0.045, (0, 10.0): 0.070,
        (1, 5.0): 0.055, (1, 10.0): 0.085, (1, 15.0): 0.110, (1, 20.0): 0.135,
        (2, 20.0): 0.155, (3, 10.0): 0.200, (4, 10.0): 0.350,
        (5, 10.0): 0.585, (6, 10.0): 0.585, (5, 20.0): 0.635,
    }
    for (cqs, dur), expected in cases.items():
        got = spread_factor(np.array(cqs), np.array(dur), DELEGATED_2015)
        assert float(got) == pytest.approx(expected)
    # 2026/269 left paragraph 3 alone.
    for (cqs, dur), expected in cases.items():
        got = spread_factor(np.array(cqs), np.array(dur), DELEGATED_2026)
        assert float(got) == pytest.approx(expected)


def test_unrated_spread_table_is_the_published_one():
    """Article 176(4): four bands, with 10 to 20 years undivided."""
    for dur, expected in ((1.0, 0.03), (5.0, 0.15), (10.0, 0.235),
                          (20.0, 0.355), (30.0, 0.405)):
        got = unrated_spread_factor(np.array(dur), DELEGATED_2015)
        assert float(got) == pytest.approx(expected)


def test_concentration_tables_are_the_published_ones():
    """Articles 185 and 186(1)."""
    assert DELEGATED_2015.concentration.threshold == (
        0.03, 0.03, 0.03, 0.015, 0.015, 0.015, 0.015)
    assert DELEGATED_2015.concentration.factor == (
        0.12, 0.12, 0.21, 0.27, 0.73, 0.73, 0.73)
    # 2026/269 did not touch them.
    assert (DELEGATED_2026.concentration.threshold
            == DELEGATED_2015.concentration.threshold)
    assert (DELEGATED_2026.concentration.factor
            == DELEGATED_2015.concentration.factor)


def test_property_and_currency_are_25_percent_under_both_regimes():
    """Articles 174 and 188(3)-(4), unchanged by 2026/269."""
    for calibration in CALIBRATIONS:
        assert calibration.property_factor == 0.25
        assert calibration.currency_factor == 0.25


def test_calibrations_are_dated_and_picked_by_reporting_date():
    """2026/269 entered into force on 10 March 2026 and applies from
    30 January 2027, so a 2026 year-end reports on the old regime."""
    assert calibration_for(dt.date(2024, 12, 31)) is DELEGATED_2015
    assert calibration_for(dt.date(2026, 12, 31)) is DELEGATED_2015
    assert calibration_for(dt.date(2027, 1, 29)) is DELEGATED_2015
    assert calibration_for(dt.date(2027, 1, 30)) is DELEGATED_2026
    assert calibration_for(dt.date(2030, 6, 30)) is DELEGATED_2026
    with pytest.raises(ValueError, match="before"):
        calibration_for(dt.date(2010, 1, 1))


def test_every_calibration_names_its_source():
    for calibration in CALIBRATIONS:
        assert calibration.source
        assert calibration.name in calibration.source


def test_a_table_with_unordered_knots_is_refused():
    with pytest.raises(ValueError, match="strictly increasing"):
        InterestShockTable(relative=((2, 0.5), (1, 0.6)))


# --------------------------------------------------------------------------
# Legislative settings
# --------------------------------------------------------------------------

def test_every_setting_is_documented_and_round_trips():
    """``options()`` is the inverse of ``variant()``: a regime expressed as
    the switches it has thrown, and rebuildable from either end."""
    for calibration in CALIBRATIONS:
        settings = calibration.options()
        assert set(settings) == set(LEGISLATIVE_OPTIONS)
        assert all(LEGISLATIVE_OPTIONS[key] for key in settings)
        for other in CALIBRATIONS:
            rebuilt = other.variant(**settings)
            assert rebuilt.options() == settings


def test_a_rebuilt_regime_computes_the_same_numbers():
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    rebuilt = DELEGATED_2015.variant(**DELEGATED_2026.options())
    assert (interest_rate_capital(assets, ANNUITY, FLAT, rebuilt)
            == interest_rate_capital(assets, ANNUITY, FLAT, DELEGATED_2026))
    back = DELEGATED_2026.variant(**DELEGATED_2015.options())
    assert (interest_rate_capital(assets, ANNUITY, FLAT, back)
            == interest_rate_capital(assets, ANNUITY, FLAT, DELEGATED_2015))


def test_an_unknown_setting_raises_rather_than_being_ignored():
    """The failure this exists to prevent is a run that quietly used the
    regime it was not asked for."""
    with pytest.raises(ValueError, match="not legislative settings"):
        DELEGATED_2015.variant(equity_type_1_factor=0.5)
    with pytest.raises(ValueError, match="not one of"):
        DELEGATED_2015.variant(negative_rates="ignore")
    with pytest.raises(ValueError, match="not one of"):
        DELEGATED_2015.variant(interest_tables="2019/981")


def test_a_variant_leaves_the_published_regimes_alone():
    """Frozen, and rebuilt rather than mutated, so the two dated sets stay
    exactly what the Official Journal says they are."""
    before = DELEGATED_2015.options()
    changed = DELEGATED_2015.variant(minimum_increase=None, symmetric_cap=0.13)
    assert DELEGATED_2015.options() == before
    assert DELEGATED_2015.interest.minimum_increase == 0.01
    assert changed.interest.minimum_increase is None
    assert changed.equity.symmetric_cap == 0.13
    # The name says what was thrown, so a result carries its own provenance.
    assert "minimum_increase" in changed.name
    assert "symmetric_cap" in changed.name
    assert DELEGATED_2015.variant().name == "2015/35"
    assert DELEGATED_2015.variant("house view").name == "house view"


def test_the_negative_rate_rules_are_three_different_answers():
    """On a flat −1% curve, Article 167(2) as enacted shocks by nothing at
    all, 2026/269's floor takes the one-year point to −1.25%, and the raw
    formula would have gone to −1.58%."""
    curve = YieldCurve.flat(-0.01, freq=1)
    got = {}
    for rule in NEGATIVE_RATE_RULES:
        variant = DELEGATED_2026.variant(negative_rates=rule)
        got[rule] = spot_rates(stressed_curve(curve, variant, "down"))
    assert got["nil"][0] == pytest.approx(-0.01)
    assert got["floored"][0] == pytest.approx(-0.0125)
    assert got["unrestricted"][0] == pytest.approx(-0.0158)
    assert got["floored"][19] == pytest.approx(-0.00893)
    assert got["unrestricted"][19] == pytest.approx(-0.01)


def test_the_spread_table_switch_reproduces_the_original_discontinuity():
    """The 2016/467 comparison is data in the module, not history in a
    test: both tables ship and either can be run."""
    original = DELEGATED_2015.variant(spread_table="2015-oj")
    assert float(spread_factor(np.array(1), np.array(12.0),
                               original)) == pytest.approx(0.084 + 0.005 * 2)
    assert float(spread_factor(np.array(1), np.array(12.0),
                               DELEGATED_2015)) == pytest.approx(0.085 + 0.01)
    assert _band_jumps(original)[10.0][1] == pytest.approx(-0.001)
    assert _band_jumps(original)[20.0][4] == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------
# The finding: an amendment is not the sum of its clauses
# --------------------------------------------------------------------------

def test_deleting_the_minimum_increase_alone_removes_all_the_capital():
    """2026/269 does five things at once. Thrown one at a time on the
    (5, 20) fund, only one of them moves anything — and the one everybody
    would name as *relief* removes the entire requirement when it is thrown
    on its own.

    Under 2015/35 the upward shock on a 3% curve is Article 166(2)'s one
    percentage point at 76 of the first 90 maturities, so deleting the
    minimum does not trim the shock, it **is** the shock: 5.15 becomes 0.
    Switch the tables instead and the answer is the whole of 2026/269's
    11.64 without touching anything else.
    """
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)

    def capital(calibration):
        return interest_rate_capital(assets, ANNUITY, FLAT, calibration)[0]

    baseline = capital(DELEGATED_2015)
    assert baseline == pytest.approx(5.1464, abs=5e-4)
    assert capital(DELEGATED_2015.variant(minimum_increase=None)) == 0.0
    assert capital(DELEGATED_2015.variant(interest_tables="2026/269")) \
        == pytest.approx(capital(DELEGATED_2026))
    # The other three clauses do nothing at all on this book.
    for setting, value in (("negative_rates", "floored"),
                           ("symmetric_cap", 0.13),
                           ("interest_spread_correlation", 0.25)):
        assert capital(DELEGATED_2015.variant(**{setting: value})) \
            == pytest.approx(baseline)


def test_the_clauses_are_not_additive():
    """Sum the one-at-a-time effects and you get +1.35; apply them together
    and you get +6.49. The minimum and the tables are not independent —
    2026/269's upward shock already exceeds a percentage point everywhere on
    a 3% curve, so deleting the minimum is a **no-op** once the new tables
    are in, and the whole of the relief a reader would attribute to that
    deletion is really the tables.

    RFC-024 found the same shape in the analysis of surplus: peeling
    drivers off one at a time does not decompose an interaction, it hides
    it in the order.
    """
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)

    def capital(calibration):
        return interest_rate_capital(assets, ANNUITY, FLAT, calibration)[0]

    baseline = capital(DELEGATED_2015)
    joint = capital(DELEGATED_2026) - baseline
    one_at_a_time = sum(
        capital(DELEGATED_2015.variant(**{key: value})) - baseline
        for key, value in DELEGATED_2026.options().items()
        if DELEGATED_2015.options()[key] != value)
    assert joint == pytest.approx(6.4949, abs=5e-4)
    assert one_at_a_time == pytest.approx(1.3485, abs=5e-4)
    assert joint != pytest.approx(one_at_a_time)
    # Removing the minimum from the 2026 regime changes nothing whatever.
    assert capital(DELEGATED_2026.variant(minimum_increase=0.01)) \
        == pytest.approx(capital(DELEGATED_2026))


def test_a_larger_shock_is_not_always_a_larger_capital_requirement():
    """Put Article 166(2)'s minimum back into the 2026 regime on a 0.5%
    curve, where the new upward shock reaches only 10bp at the long end.
    Every maturity now moves at least a percentage point — a strictly
    larger shock — and the capital **halves**, 8.86 to 4.03.

    The long end is where the minimum bites, and this fund's liability is
    longer than its assets, so raising the long end alone moves the
    liability down further than the assets. The module is a shape, not a
    level, and "bigger shock, bigger number" is not a property it has.
    """
    curve = YieldCurve.flat(0.005, freq=1)
    assets = duration_matched_assets(ANNUITY, curve, short=5, long=20)
    with_minimum = DELEGATED_2026.variant(minimum_increase=0.01)
    plain = spot_rates(stressed_curve(curve, DELEGATED_2026, "up"))
    floored = spot_rates(stressed_curve(curve, with_minimum, "up"))
    base = spot_rates(curve)
    assert (plain[:90] - base[:90]).min() == pytest.approx(0.001, abs=1e-9)
    assert np.all(floored[:90] >= plain[:90] - 1e-15)
    assert interest_rate_capital(assets, ANNUITY, curve,
                                 DELEGATED_2026)[0] == pytest.approx(
        8.8632, abs=5e-4)
    assert interest_rate_capital(assets, ANNUITY, curve,
                                 with_minimum)[0] == pytest.approx(
        4.0304, abs=5e-4)


def test_the_spread_correlation_change_is_invisible_in_the_bundle():
    """2026/269 point (41) is worth 9.46 of market SCR on a down-binding
    book — a 5.4% reduction — and the same amendment's interest tables add
    72.3, so anyone comparing the two regimes as bundles would report the
    correlation change as a capital *increase*. Only the switch separates
    them."""
    curve = YieldCurve.flat(0.03, freq=1)
    factors = curve.discount_factors(41)
    value = present_value(ANNUITY, curve)
    weight = 4.0 / 39.0
    assets = np.zeros(40)
    assets[0] = value * (1.0 - weight) / factors[1]
    assets[39] = value * weight / factors[40]
    values = np.array([assets[0] * factors[1], assets[39] * factors[40]])
    holdings = (values, np.array([1.0, 40.0]), np.array([2, 2]))

    def position(calibration):
        return market_risk(assets=assets, liabilities=ANNUITY, curve=curve,
                           calibration=calibration, spread=holdings,
                           property_value=200.0)

    base = position(DELEGATED_2015)
    isolated = position(DELEGATED_2015.variant(interest_spread_correlation=0.25))
    bundle = position(DELEGATED_2026)
    assert base.interest_direction == "down"
    assert base.scr == pytest.approx(175.796, abs=5e-3)
    assert isolated.scr == pytest.approx(166.337, abs=5e-3)
    assert bundle.scr == pytest.approx(248.112, abs=5e-3)
    assert isolated.modules == base.modules
    assert base.scr - isolated.scr == pytest.approx(9.459, abs=5e-3)
    assert bundle.scr > base.scr


# --------------------------------------------------------------------------
# Spot rates
# --------------------------------------------------------------------------

def test_spot_and_forward_are_exact_inverses():
    """The discount factors survive the round trip to machine precision."""
    curve = YieldCurve(np.linspace(0.01, 0.05, 40), freq=1, horizon_years=60)
    back = curve_from_spot_rates(spot_rates(curve), curve.freq,
                                 curve.horizon_years)
    n = curve.n_periods - 1
    assert np.allclose(back.discount_factors(n), curve.discount_factors(n),
                       rtol=1e-13, atol=0.0)
    assert np.allclose(back.rates[:n], curve.rates[:n], rtol=1e-11, atol=0.0)


def test_spot_rates_of_a_flat_curve_are_the_flat_rate():
    spots = spot_rates(FLAT)
    assert np.allclose(spots, 0.03, rtol=0.0, atol=1e-14)


def test_the_shock_is_applied_at_every_maturity_of_the_curve():
    """A monthly curve is shocked at monthly maturities, not annual ones."""
    monthly = YieldCurve.flat(0.03, freq=12, horizon_years=40)
    up = stressed_curve(monthly, DELEGATED_2015, "up")
    spots = spot_rates(up)
    # Six months in, the "shorter than 1 year" rule gives the 70% factor,
    # which on 3% is 5.1% — and Article 166(2)'s minimum does not bind.
    assert spots[5] == pytest.approx(0.051, abs=1e-9)


# --------------------------------------------------------------------------
# The interest rate shock
# --------------------------------------------------------------------------

def test_2015_upward_shock_is_multiplicative_then_floored():
    """Article 166(1) and (2) on a flat 3% curve."""
    spots = spot_rates(stressed_curve(FLAT, DELEGATED_2015, "up"))
    assert spots[0] == pytest.approx(0.03 * 1.70)     # 1y, 70%
    assert spots[9] == pytest.approx(0.03 * 1.42)     # 10y, 42%
    # 20y: 3% x 1.26 = 3.78%, which is less than 3% + 1pp, so the floor wins.
    assert spots[19] == pytest.approx(0.04)


def test_the_one_percentage_point_minimum_only_ever_raises():
    """Article 166(2) is a minimum on the increase, so it cannot reduce the
    stressed rate below the multiplicative answer."""
    curve = YieldCurve(np.linspace(0.001, 0.08, 90), freq=1, horizon_years=95)
    base = spot_rates(curve)
    shocked = spot_rates(stressed_curve(curve, DELEGATED_2015, "up"))
    relative = DELEGATED_2015.interest.up.relative_at(
        np.arange(1.0, base.size + 1.0))
    assert np.all(shocked >= base * (1.0 + relative) - 1e-15)
    assert np.all(shocked - base >= 0.01 - 1e-15)


def test_below_1_43_percent_the_2015_upward_shock_is_a_parallel_100bp():
    """The floor binds at every maturity once the rate falls below
    ``1% / 70%``, at which point the whole calibrated table is inoperative
    and Article 166 is a flat +100bp parallel shift."""
    for rate in (0.001, 0.005, 0.0142):
        curve = YieldCurve.flat(rate, freq=1)
        move = (spot_rates(stressed_curve(curve, DELEGATED_2015, "up"))
                - spot_rates(curve))
        assert np.allclose(move[:90], 0.01, rtol=0.0, atol=1e-12)
    # At 3% it binds at most maturities but not at the short end.
    move = (spot_rates(stressed_curve(FLAT, DELEGATED_2015, "up"))
            - spot_rates(FLAT))
    binding = move[:90] <= 0.01 + 1e-12
    assert binding.sum() == 76
    assert not binding[0]


def test_2015_downward_shock_is_nil_on_a_negative_rate():
    """Article 167(2) as enacted."""
    curve = YieldCurve(np.full(30, -0.005), freq=1, horizon_years=40)
    base = spot_rates(curve)
    shocked = spot_rates(stressed_curve(curve, DELEGATED_2015, "down"))
    assert np.allclose(shocked[:30], base[:30], rtol=0.0, atol=1e-15)


def test_2026_shock_is_multiplicative_plus_a_parallel_shift():
    """``r(1+s)+b`` and ``r(1-s)-b`` — 2026/269 points (43) and (44)."""
    up = spot_rates(stressed_curve(FLAT, DELEGATED_2026, "up"))
    down = spot_rates(stressed_curve(FLAT, DELEGATED_2026, "down"))
    assert up[0] == pytest.approx(0.03 * 1.61 + 0.0214)
    assert up[9] == pytest.approx(0.03 * 1.30 + 0.0105)
    assert down[0] == pytest.approx(0.03 * (1 - 0.58) - 0.0116)
    assert down[19] == pytest.approx(0.03 * (1 - 0.50) - 0.0050)


def test_2026_has_no_minimum_increase():
    """Point (43) replaced paragraphs 1 and 2 together, and the new
    paragraph 2 is the formula — the one percentage point floor is gone."""
    assert DELEGATED_2026.interest.minimum_increase is None
    curve = YieldCurve.flat(0.0, freq=1)
    move = (spot_rates(stressed_curve(curve, DELEGATED_2026, "up"))
            - spot_rates(curve))
    # On a zero curve the whole shock is the parallel part, which is 0.88%
    # at twenty years — less than the percentage point 2015/35 required.
    assert move[19] == pytest.approx(0.0088)


def test_2026_downward_shock_respects_the_term_dependent_floor():
    """Article 167(1) as replaced: −1.25% to seven years, −0.893% from
    twenty, interpolated between.

    It takes an already negative curve to reach the floor. On a flat −1%
    the one-year shock lands at −1.58% and the floor takes it back to
    −1.25%; on a flat +0.1% it lands at −1.12% and the floor never binds at
    all. So the floor is not a detail bolted onto the table — it is the
    whole of the shock in the rate environment the amendment was written
    for, and it replaces a rule (Article 167(2)) under which that same
    curve would have been shocked by nothing whatever.
    """
    curve = YieldCurve.flat(-0.01, freq=1)
    unfloored = (spot_rates(curve)[0] * (1.0 - 0.58)) - 0.0116
    assert unfloored == pytest.approx(-0.0158)
    shocked = spot_rates(stressed_curve(curve, DELEGATED_2026, "down"))
    assert shocked[0] == pytest.approx(-0.0125)
    assert shocked[6] == pytest.approx(-0.0125)
    assert shocked[19] == pytest.approx(-0.00893)
    # Fifty years out the relative factor has grown to 65% and the parallel
    # part shrunk to 18bp, so the shock lands at −0.53% and the floor has
    # nothing to do — it binds at the short and middle of the curve, not at
    # the long end where the table is harshest.
    assert shocked[49] == pytest.approx(-0.01 * (1.0 - 0.65) - 0.0018)
    assert shocked[49] > -0.00893
    mild = spot_rates(stressed_curve(YieldCurve.flat(0.001, freq=1),
                                     DELEGATED_2026, "down"))
    assert mild[0] == pytest.approx(0.001 * (1.0 - 0.58) - 0.0116)
    assert mild[0] > -0.0125
    # Thirteen and a half years is exactly half way between the two knots.
    floor = DELEGATED_2026.interest.floor_at(np.array(13.5))
    assert float(floor) == pytest.approx(0.5 * (-0.0125 + -0.00893))
    assert np.all(shocked[:90] >= -0.0125 - 1e-15)


def test_2015_has_no_floor_and_2026_has_no_nil_rule():
    assert DELEGATED_2015.interest.down_floor is None
    assert DELEGATED_2015.interest.nil_decrease_when_negative
    assert DELEGATED_2026.interest.down_floor is not None
    assert not DELEGATED_2026.interest.nil_decrease_when_negative


def test_an_unknown_direction_is_refused():
    with pytest.raises(ValueError, match="up.*down"):
        stressed_curve(FLAT, DELEGATED_2015, "sideways")


# --------------------------------------------------------------------------
# The capital requirement is a fall in own funds
# --------------------------------------------------------------------------

def test_a_cashflow_matched_fund_has_no_interest_capital_at_all():
    """The control. Assets that *are* the liability move with it under any
    curve whatever, so the loss is exactly zero — asserted with ``==``,
    because anything else would mean the two sides were valued
    differently."""
    for calibration in CALIBRATIONS:
        capital, _, shocks = interest_rate_capital(
            ANNUITY, ANNUITY, FLAT, calibration)
        assert capital == 0.0
        for shock in shocks.values():
            assert shock.loss == 0.0
            assert shock.reconciles()


def test_every_shock_reconciles_to_the_fall_in_own_funds():
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    for calibration in CALIBRATIONS:
        _, _, shocks = interest_rate_capital(assets, ANNUITY, FLAT,
                                             calibration)
        for shock in shocks.values():
            assert shock.reconciles()
            assert shock.capital == max(shock.loss, 0.0)


def test_a_shock_that_improves_own_funds_releases_no_capital():
    """The floor at zero is the standard's: the SCR is the loss in the
    99.5% scenario, and a gain in it is not a negative loss."""
    shock = ShockResult(name="x", capital=0.0, assets_base=100.0,
                        assets_stressed=120.0)
    assert shock.loss == -20.0
    assert shock.reconciles()


def test_a_duration_approximation_fails_the_reconciliation():
    """The invariant has teeth. A module that priced the asset move with a
    duration sensitivity instead of revaluing would report a capital that
    does not match the balance sheet it claims to come from."""
    shock = ShockResult(name="x", capital=7.0, assets_base=100.0,
                        assets_stressed=95.0, liabilities_base=0.0,
                        liabilities_stressed=0.0)
    assert not shock.reconciles()


# --------------------------------------------------------------------------
# The finding: duration matching does not determine the interest SCR
# --------------------------------------------------------------------------

MATCHED_BARBELLS = ((10, 13), (9, 15), (8, 18), (5, 20), (3, 25), (1, 40))


def test_matching_value_and_duration_leaves_the_interest_scr_undetermined():
    """Every barbell here is worth exactly what the liability is worth and
    has exactly its duration, so the dollar duration gap RFC-025 identified
    as the thing that matters is **zero** in all of them. The prescribed
    interest capital nonetheless runs from 15.36 to nothing.

    The shock is a term-dependent *shape*, and a duration match is a single
    number: it cannot say anything about a shape.
    """
    target_value = present_value(ANNUITY, FLAT)
    target_duration = macaulay_duration(ANNUITY, FLAT)
    capitals = []
    for short, long in MATCHED_BARBELLS:
        assets = duration_matched_assets(ANNUITY, FLAT, short=short, long=long)
        value = present_value(assets, FLAT)
        duration = macaulay_duration(assets, FLAT)
        assert value == pytest.approx(target_value, rel=1e-12)
        assert duration == pytest.approx(target_duration, rel=1e-12)
        dollar_gap = value * duration - target_value * target_duration
        assert abs(dollar_gap) < 1e-8
        capitals.append(interest_rate_capital(assets, ANNUITY, FLAT,
                                              DELEGATED_2015)[0])
    assert capitals[0] == pytest.approx(15.3578, abs=5e-4)
    assert capitals[-1] == 0.0
    assert max(capitals) - min(capitals) > 15.0


def test_a_fund_that_gains_on_every_parallel_shift_still_carries_capital():
    """The sharpest form of it. The (5, 20) barbell has more convexity than
    the annuity, so a parallel move of any size in either direction
    *increases* the surplus — there is no parallel-shift loss to hedge.
    Article 166's shock still takes 5.15, because it is not parallel: it
    moves the five-year point 165bp and the twenty-year point 100bp.
    """
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    sheet = BalanceSheet(assets, ANNUITY, FLAT)
    for basis_points in (-300, -200, -100, -50, 50, 100, 200, 300):
        assert sheet.surplus_under_shift(basis_points) > 0.0
    capital, direction, _ = interest_rate_capital(assets, ANNUITY, FLAT,
                                                  DELEGATED_2015)
    assert direction == "up"
    assert capital == pytest.approx(5.1464, abs=5e-4)
    move = (spot_rates(stressed_curve(FLAT, DELEGATED_2015, "up"))
            - spot_rates(FLAT))
    assert move[4] == pytest.approx(0.0165, abs=1e-4)
    assert move[19] == pytest.approx(0.0100, abs=1e-4)


def test_the_2026_shock_is_the_larger_one_on_a_long_book():
    """Same balance sheet, both regimes."""
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    old = interest_rate_capital(assets, ANNUITY, FLAT, DELEGATED_2015)[0]
    new = interest_rate_capital(assets, ANNUITY, FLAT, DELEGATED_2026)[0]
    assert old == pytest.approx(5.1464, abs=5e-4)
    assert new == pytest.approx(11.6412, abs=5e-4)
    assert new > 2.0 * old


def test_interest_down_binds_for_a_book_with_assets_shorter_than_liabilities():
    """The classic annuity fund. Falling rates raise bond prices, and it
    still hurts — because the liability lengthens faster than the assets
    do, so its value rises more."""
    value = present_value(ANNUITY, FLAT)
    factors = FLAT.discount_factors(41)
    for asset_duration, expected in ((3.0, "down"), (5.0, "down"),
                                     (7.0, "down")):
        weight = (asset_duration - 1) / 39.0
        assets = np.zeros(40)
        assets[0] = value * (1.0 - weight) / factors[1]
        assets[39] = value * weight / factors[40]
        capital, direction, shocks = interest_rate_capital(
            assets, ANNUITY, FLAT, DELEGATED_2015)
        assert direction == expected
        assert capital > 0.0
        # Assets are worth *more* after the downward shock, and it is still
        # the binding scenario.
        down = shocks["down"]
        assert down.assets_stressed > down.assets_base
        assert down.liabilities_stressed > down.liabilities_base


def test_the_binding_direction_depends_on_the_shape_and_on_the_regime():
    """The (8, 18) barbell is a worked example of why the direction has to
    be carried rather than assumed: the same balance sheet binds *up* under
    2015/35 and *down* under 2026/269, so the two regimes do not even
    aggregate on the same correlation matrix."""
    assets = duration_matched_assets(ANNUITY, FLAT, short=8, long=18)
    assert interest_rate_capital(assets, ANNUITY, FLAT,
                                 DELEGATED_2015)[1] == "up"
    assert interest_rate_capital(assets, ANNUITY, FLAT,
                                 DELEGATED_2026)[1] == "down"


# --------------------------------------------------------------------------
# Equity
# --------------------------------------------------------------------------

def test_symmetric_adjustment_is_negative_when_the_index_is_on_its_average():
    """Article 172(2)'s ``− 8%``: the adjustment is zero when the index is
    8% *above* its own three-year average, not when it is on it."""
    assert symmetric_adjustment(100.0, 100.0, DELEGATED_2015) == pytest.approx(
        -0.04)
    assert symmetric_adjustment(108.0, 100.0, DELEGATED_2015) == pytest.approx(
        0.0)


def test_symmetric_adjustment_corridor_widens_under_2026():
    """Article 172(4), replaced by 2026/269 point (51)."""
    assert symmetric_adjustment(200.0, 100.0, DELEGATED_2015) == 0.10
    assert symmetric_adjustment(200.0, 100.0, DELEGATED_2026) == 0.13
    assert symmetric_adjustment(20.0, 100.0, DELEGATED_2015) == -0.10
    assert symmetric_adjustment(20.0, 100.0, DELEGATED_2026) == -0.13
    # Inside ±10% the two regimes agree exactly.
    for ratio in (0.92, 1.0, 1.08, 1.2):
        assert (symmetric_adjustment(ratio, 1.0, DELEGATED_2015)
                == symmetric_adjustment(ratio, 1.0, DELEGATED_2026))


def test_symmetric_adjustment_needs_a_positive_denominator():
    with pytest.raises(ValueError, match="denominator"):
        symmetric_adjustment(100.0, 0.0, DELEGATED_2015)


def test_equity_factors_are_the_published_ones():
    """Article 169(1) to (4)."""
    for exposure, expected in (
        (EquityExposure(type1=1000.0), 390.0),
        (EquityExposure(type2=1000.0), 490.0),
        (EquityExposure(infrastructure=1000.0), 300.0),
        (EquityExposure(infrastructure_corporate=1000.0), 360.0),
        (EquityExposure(strategic=1000.0), 220.0),
        (EquityExposure(long_term=1000.0), 220.0),
    ):
        capital, _ = equity_capital(exposure, DELEGATED_2015)
        assert capital == pytest.approx(expected)


def test_infrastructure_takes_a_weighted_share_of_the_adjustment():
    """Article 169(3)(c) and (4)(c): 77% and 92% of the adjustment."""
    _, charges = equity_capital(
        EquityExposure(type1=100.0, infrastructure=100.0,
                       infrastructure_corporate=100.0, strategic=100.0),
        DELEGATED_2015, symmetric=0.10)
    assert charges["type1"] == pytest.approx(49.0)
    assert charges["infrastructure"] == pytest.approx(30.0 + 7.7)
    assert charges["infrastructure_corporate"] == pytest.approx(36.0 + 9.2)
    # A strategic participation does not move with the equity index.
    assert charges["strategic"] == pytest.approx(22.0)


def test_equity_aggregation_sums_the_block_before_correlating_it():
    """Article 168(4) as corrected by corrigendum C2 correlates type 1 with
    the **sum** of the other three, not with each separately, and the two
    readings give different numbers."""
    exposure = EquityExposure(type1=1000.0, type2=500.0, infrastructure=500.0)
    total, charges = equity_capital(exposure, DELEGATED_2015)
    first = charges["type1"]
    rest = charges["type2"] + charges["infrastructure"]
    assert total == pytest.approx(
        np.sqrt(first ** 2 + 2 * 0.75 * first * rest + rest ** 2))
    separately = np.sqrt(
        first ** 2 + charges["type2"] ** 2 + charges["infrastructure"] ** 2
        + 2 * 0.75 * first * charges["type2"]
        + 2 * 0.75 * first * charges["infrastructure"])
    assert total > separately


def test_equity_aggregate_lies_between_the_largest_leg_and_their_sum():
    exposure = EquityExposure(type1=800.0, type2=400.0, infrastructure=200.0,
                              infrastructure_corporate=100.0, strategic=50.0)
    total, charges = equity_capital(exposure, DELEGATED_2015)
    assert max(charges.values()) <= total <= sum(charges.values())


# --------------------------------------------------------------------------
# Property and currency
# --------------------------------------------------------------------------

def test_property_is_a_flat_quarter():
    shock = property_capital(2_000.0, DELEGATED_2015)
    assert shock.capital == pytest.approx(500.0)
    assert shock.assets_stressed == pytest.approx(1_500.0)
    assert shock.reconciles()


def test_currency_sums_across_currencies_and_never_diversifies():
    """Article 188(1). The only market sub-module with no aggregation at
    all: three currencies of 100 cost three times one currency of 100,
    where any other sub-module would have taken a root."""
    one, _ = currency_capital({"USD": 100.0}, DELEGATED_2015)
    three, detail = currency_capital(
        {"USD": 100.0, "JPY": 100.0, "CHF": 100.0}, DELEGATED_2015)
    assert one == pytest.approx(25.0)
    assert three == pytest.approx(75.0)
    assert three == pytest.approx(3.0 * one)
    assert all(which == "down" for _, which in detail.values())


def test_a_short_currency_position_loses_when_the_currency_rises():
    _, detail = currency_capital({"USD": -400.0}, DELEGATED_2015)
    capital, which = detail["USD"]
    assert capital == pytest.approx(100.0)
    assert which == "up"


def test_currency_positions_that_offset_do_not_offset():
    """A long dollar and a short yen of the same size cost twice, not
    nothing: Article 188 takes the worse direction per currency and then
    adds."""
    total, _ = currency_capital({"USD": 500.0, "JPY": -500.0},
                                DELEGATED_2015)
    assert total == pytest.approx(250.0)


# --------------------------------------------------------------------------
# Spread
# --------------------------------------------------------------------------

def _band_jumps(calibration):
    jumps = {}
    for edge in (5.0, 10.0, 15.0, 20.0):
        below = spread_factor(np.arange(7), np.full(7, edge), calibration)
        above = spread_factor(np.arange(7), np.full(7, edge + 1e-12),
                              calibration)
        jumps[edge] = above - below
    return jumps


def test_the_2016_amendment_removed_one_discontinuity_and_created_another():
    """Set out to check the table was continuous, and it is not.

    A spread factor that jumps at a band boundary means two otherwise
    identical bonds a day apart in duration attract different capital. The
    text as first published in OJ L 12 does that once: credit quality step
    1 drops **10 basis points** at ten years. Commission Delegated
    Regulation (EU) 2016/467 replaced the whole table, and its version
    moved the three step 1 entries — 8.4→8.5, 10.9→11.0, 13.4→13.5 — which
    removes that jump exactly. In the same replacement step 4's twenty-year
    entry went 46.5→46.6, which **creates** one of the same size at twenty
    years.

    So the amendment did not tidy the table. It moved the discontinuity
    from step 1 to step 4, and it is still there in the text that applies
    today and in the text that applies from 2027.
    """
    original = _band_jumps(DELEGATED_2015.variant(spread_table="2015-oj"))
    assert original[10.0][1] == pytest.approx(-0.001)
    assert np.count_nonzero(np.abs(np.concatenate(list(original.values())))
                            > 1e-9) == 1

    for calibration in CALIBRATIONS:
        current = _band_jumps(calibration)
        assert current[10.0][1] == pytest.approx(0.0, abs=1e-12)
        assert current[20.0][4] == pytest.approx(0.001)
        assert np.count_nonzero(
            np.abs(np.concatenate(list(current.values()))) > 1e-9) == 1


def test_the_spread_factor_is_capped_at_all_of_the_bond():
    """The ``min(..., 1)`` of Article 176(3)'s last row. It binds for credit
    quality step 5 or 6 beyond about 93 years of modified duration."""
    assert float(spread_factor(np.array(5), np.array(200.0),
                               DELEGATED_2015)) == 1.0
    assert float(spread_factor(np.array(5), np.array(90.0),
                               DELEGATED_2015)) < 1.0
    assert float(unrated_spread_factor(np.array(500.0), DELEGATED_2015)) == 1.0


def test_duration_is_floored_at_one_year():
    """Article 176(2): ``dur_i`` shall never be lower than 1."""
    for duration in (0.0, 0.25, 0.5, 1.0):
        assert float(spread_factor(np.array(2), np.array(duration),
                                   DELEGATED_2015)) == pytest.approx(0.014)


def test_the_unrated_table_is_kinder_than_credit_quality_step_4():
    """Article 176(4) is not a conservative fallback. At every duration it
    charges less than step 4 does, so a bond loses capital by being
    downgraded out of an ECAI's coverage."""
    durations = np.arange(1.0, 51.0)
    unrated = unrated_spread_factor(durations, DELEGATED_2015)
    step4 = spread_factor(np.full(durations.size, 4), durations,
                          DELEGATED_2015)
    step3 = spread_factor(np.full(durations.size, 3), durations,
                          DELEGATED_2015)
    assert np.all(unrated < step4)
    assert np.all(unrated > step3)


def test_spread_capital_is_additive_over_holdings():
    """Charged per holding and summed, never as an average factor against a
    total — the two differ whenever the durations do."""
    values = np.array([1000.0, 1000.0])
    durations = np.array([2.0, 20.0])
    steps = np.array([2, 2])
    shock = spread_capital(values, durations, steps, calibration=DELEGATED_2015)
    assert shock.reconciles()
    per_holding = sum(
        float(spread_factor(np.array(s), np.array(d), DELEGATED_2015)) * v
        for v, d, s in zip(values, durations, steps))
    assert shock.capital == pytest.approx(per_holding)
    average = float(spread_factor(np.array(2), np.array(11.0),
                                  DELEGATED_2015)) * values.sum()
    assert shock.capital != pytest.approx(average)


def test_collateral_halves_the_unrated_factor():
    """Article 176(5)(a), and only for unrated holdings — a rated bond is
    charged off Article 176(3) whatever collateral it has."""
    values, durations = np.array([1000.0]), np.array([10.0])
    plain = spread_capital(values, durations, calibration=DELEGATED_2015)
    secured = spread_capital(values, durations, calibration=DELEGATED_2015,
                             collateralised=[True])
    assert secured.capital == pytest.approx(0.5 * plain.capital)
    rated = spread_capital(values, durations, np.array([2]),
                           calibration=DELEGATED_2015, collateralised=[True])
    assert rated.capital == pytest.approx(105.0)


def test_spread_risk_is_unhedged_on_a_duration_matched_fund():
    """Nothing on the liability side moves when spreads widen, so the whole
    of the asset move is a loss — unless a matching or volatility
    adjustment applies, and neither is in this module.

    On the (5, 20) barbell the interest module takes 5.15 and the spread
    module on the same assets, held as credit quality step 2 paper, takes
    185.80. Matching the duration hedged the risk that was 3% of the
    problem.
    """
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    periods = np.flatnonzero(assets > 0.0) + 1
    factors = FLAT.discount_factors(int(periods.max()) + 1)
    values = assets[periods - 1] * factors[periods]
    interest = interest_rate_capital(assets, ANNUITY, FLAT, DELEGATED_2015)[0]
    spread = spread_capital(values, periods.astype(float),
                            np.full(periods.size, 2),
                            calibration=DELEGATED_2015)
    assert interest == pytest.approx(5.1464, abs=5e-4)
    assert spread.capital == pytest.approx(185.80, abs=0.05)
    assert spread.capital > 35.0 * interest
    assert spread.reconciles()


# --------------------------------------------------------------------------
# Concentration
# --------------------------------------------------------------------------

def test_only_the_excess_over_the_threshold_is_charged():
    """Article 184(1). A name inside its threshold contributes nothing at
    all, so the sub-module is a cliff rather than a slope."""
    capital, per_name = concentration_capital(
        [29.0, 31.0], [2, 2], 1_000.0, DELEGATED_2015)
    assert per_name[0] == 0.0
    assert per_name[1] == pytest.approx(1.0 * 0.21)
    assert capital == pytest.approx(0.21)


def test_thresholds_and_factors_follow_the_credit_quality_step():
    """Articles 185 and 186(1): the threshold halves at step 3 and the
    factor sextuples at step 4."""
    exposures = np.full(7, 200.0)
    _, per_name = concentration_capital(exposures, np.arange(7), 1_000.0,
                                        DELEGATED_2015)
    expected = [(200.0 - 1000.0 * ct) * g for ct, g in zip(
        DELEGATED_2015.concentration.threshold,
        DELEGATED_2015.concentration.factor)]
    assert per_name == pytest.approx(expected)


def test_splitting_one_name_in_two_removes_most_of_the_capital():
    """Article 183(1) aggregates single names as a Euclidean norm, which
    treats them as independent. Five hundred against one counterparty costs
    98.70; the same five hundred spread over ten counterparties of fifty
    costs 13.28 — 86.5% less for the identical total exposure, and the
    identical total assets.

    That is why Article 182(1) has to say a corporate group is one name:
    the sub-module's arithmetic rewards subdivision, so the definition of a
    name is doing all the work.
    """
    measured = [concentration_capital(np.full(n, 500.0 / n), np.full(n, 2),
                                      1_000.0, DELEGATED_2015)[0]
                for n in (1, 2, 5, 10)]
    assert measured[0] == pytest.approx(98.70)
    assert measured[-1] == pytest.approx(13.282, abs=5e-3)
    assert measured == sorted(measured, reverse=True)
    assert measured[-1] < 0.14 * measured[0]


def test_article_187_overrides_go_in_per_name():
    """Covered bonds at step 0 or 1 take a 15% threshold, and a single
    immovable property a 10% threshold at a 12% factor."""
    conc = DELEGATED_2015.concentration
    capital, _ = concentration_capital(
        [200.0], [1], 1_000.0, DELEGATED_2015,
        thresholds=[conc.covered_bond_threshold])
    assert capital == pytest.approx((200.0 - 150.0) * 0.12)
    capital, _ = concentration_capital(
        [200.0], [4], 1_000.0, DELEGATED_2015,
        thresholds=[conc.property_threshold], factors=[conc.property_factor])
    assert capital == pytest.approx((200.0 - 100.0) * 0.12)


def test_an_unrated_insurer_is_charged_off_its_own_solvency_ratio():
    """Article 186(2) and (3). Almost all of the relief is bought in the
    twenty-two points immediately above a 100% ratio: the factor falls 37.5
    points from 100% to 122% and only 15 more over the whole run from 122%
    to 196%."""
    ratios = np.array([0.80, 0.95, 1.00, 1.11, 1.22, 1.75, 1.96, 3.00])
    factors = insurer_concentration_factor(ratios, DELEGATED_2015)
    assert factors[0] == pytest.approx(0.73)
    assert factors[1] == pytest.approx(0.73)
    assert factors[2] == pytest.approx(0.645)
    assert factors[4] == pytest.approx(0.27)
    assert factors[6] == pytest.approx(0.12)
    assert factors[7] == pytest.approx(0.12)
    assert factors[3] == pytest.approx(0.645 + (0.27 - 0.645) * 11 / 22)
    assert factors[2] - factors[4] == pytest.approx(0.375)
    assert factors[4] - factors[6] == pytest.approx(0.15)
    # Article 186(3): failing the MCR, or not having disclosed yet.
    assert insurer_concentration_factor(
        np.array(1.96), DELEGATED_2015, meets_mcr=False) == pytest.approx(0.73)
    assert insurer_concentration_factor(
        np.array(0.50), DELEGATED_2015, disclosed=False) == pytest.approx(0.645)


def test_an_empty_calculation_base_is_refused():
    with pytest.raises(ValueError, match="calculation base"):
        concentration_capital([100.0], [2], 0.0, DELEGATED_2015)


def test_an_out_of_range_credit_quality_step_is_refused():
    with pytest.raises(ValueError, match="0 to 6"):
        concentration_capital([100.0], [7], 1_000.0, DELEGATED_2015)
    with pytest.raises(ValueError, match="0 to 6"):
        spread_factor(np.array(9), np.array(5.0), DELEGATED_2015)


# --------------------------------------------------------------------------
# Article 164 — the direction-dependent aggregation
# --------------------------------------------------------------------------

def test_shocking_the_forwards_instead_of_the_spots_gets_the_sign_wrong():
    """Not a shortcut with a small error.

    ``YieldCurve`` stores forwards; Article 166 shocks spot rates. Applying
    the table to the stored forwards on the (5, 20) fund turns a loss of
    5.15 into a **gain** of 0.67.
    """
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    surplus = present_value(assets, FLAT) - present_value(ANNUITY, FLAT)
    table = DELEGATED_2015.interest.up
    maturities = np.arange(1, FLAT.n_periods + 1) / FLAT.freq
    naive_rates = np.maximum(
        FLAT.rates * (1.0 + table.relative_at(maturities)),
        FLAT.rates + DELEGATED_2015.interest.minimum_increase)
    naive = YieldCurve([0.0], freq=FLAT.freq, horizon_years=FLAT.horizon_years)
    naive.rates = naive_rates
    naive_loss = surplus - (present_value(assets, naive)
                            - present_value(ANNUITY, naive))
    proper = interest_rate_capital(assets, ANNUITY, FLAT, DELEGATED_2015)[0]
    assert proper == pytest.approx(5.1464, abs=5e-4)
    assert naive_loss == pytest.approx(-0.6691, abs=5e-4)
    assert np.sign(naive_loss) != np.sign(proper)


def test_the_matrix_is_positive_semi_definite_in_every_substitution():
    """The regulation prints a matrix containing a *symbol*. Nothing in the
    text promises that every value of it leaves a valid correlation matrix,
    and RFC-014 showed what a matrix that is not buys you: an aggregate
    below the largest module it aggregates. All four substitutions here
    pass, with the smallest eigenvalue never below 0.15."""
    for calibration in CALIBRATIONS:
        for direction in ("up", "down"):
            matrix = market_correlation(calibration,
                                        interest_direction=direction)
            assert matrix.risks == MARKET_RISKS
            assert float(np.linalg.eigvalsh(matrix.matrix).min()) > 0.15


def test_parameter_a_is_zero_when_the_upward_shock_binds():
    """Article 164(3), last subparagraph."""
    up = market_correlation(DELEGATED_2015, interest_direction="up").matrix
    down = market_correlation(DELEGATED_2015, interest_direction="down").matrix
    assert up[0, 1] == 0.0 and up[0, 2] == 0.0 and up[0, 3] == 0.0
    assert down[0, 1] == 0.5 and down[0, 2] == 0.5 and down[0, 3] == 0.5


def test_2026_gives_the_spread_cell_its_own_parameter():
    """Point (41) replaces Article 164(3): the interest-versus-spread cell
    becomes ``B``, which is 0.25 where ``A`` is 0.5."""
    down = market_correlation(DELEGATED_2026, interest_direction="down").matrix
    assert down[0, 1] == 0.5      # equity
    assert down[0, 2] == 0.5      # property
    assert down[0, 3] == 0.25     # spread
    assert down[3, 0] == 0.25     # and symmetric
    up = market_correlation(DELEGATED_2026, interest_direction="up").matrix
    assert up[0, 3] == 0.0


def test_the_market_scr_is_not_a_function_of_the_module_capitals():
    """The quirk that deserves its own finding.

    Six identical sub-module capitals aggregate to 242.18 or to 285.74
    depending on nothing but which interest scenario was the binding one —
    an 18% difference that no amount of module-level reporting reveals. A
    reviewer handed the six numbers cannot reproduce the total.
    """
    modules = {"interest": 100.0, "equity": 100.0, "property": 50.0,
               "spread": 80.0, "concentration": 20.0, "currency": 30.0}
    up = market_correlation(DELEGATED_2015,
                            interest_direction="up").aggregate(modules)
    down = market_correlation(DELEGATED_2015,
                              interest_direction="down").aggregate(modules)
    assert up == pytest.approx(242.178, abs=5e-3)
    assert down == pytest.approx(285.745, abs=5e-3)
    assert down / up - 1.0 == pytest.approx(0.1799, abs=5e-4)
    # 2026/269's lower spread correlation takes a little of it back.
    down_2026 = market_correlation(DELEGATED_2026,
                                   interest_direction="down").aggregate(modules)
    assert down_2026 < down
    assert down_2026 / up - 1.0 == pytest.approx(0.1506, abs=5e-4)


def test_an_unknown_direction_is_refused_by_the_matrix():
    with pytest.raises(ValueError, match="up.*down"):
        market_correlation(DELEGATED_2015, interest_direction="flat")


# --------------------------------------------------------------------------
# The whole module
# --------------------------------------------------------------------------

def _full_position(calibration):
    assets = duration_matched_assets(ANNUITY, FLAT, short=5, long=20)
    periods = np.flatnonzero(assets > 0.0) + 1
    factors = FLAT.discount_factors(int(periods.max()) + 1)
    values = assets[periods - 1] * factors[periods]
    return market_risk(
        assets=assets, liabilities=ANNUITY, curve=FLAT,
        calibration=calibration,
        equity=EquityExposure(type1=200.0, type2=50.0),
        symmetric=0.02,
        property_value=150.0,
        currency_positions={"USD": 120.0},
        spread=(values, periods.astype(float), np.full(periods.size, 2)),
        concentration=(values, np.full(periods.size, 2), float(values.sum())),
    )


def test_the_whole_module_reconciles_under_both_regimes():
    for calibration in CALIBRATIONS:
        position = _full_position(calibration)
        assert set(position.modules) == set(MARKET_RISKS)
        assert position.reconciles()
        assert position.largest_module <= position.scr <= position.undiversified
        assert 0.0 <= position.diversification < 1.0


def test_the_position_reports_which_regime_and_which_direction():
    position = _full_position(DELEGATED_2015)
    assert position.calibration.name == "2015/35"
    assert position.interest_direction in ("up", "down")
    assert "2015/35" in repr(position)
    assert position.interest_direction in repr(position)


def test_a_sub_module_left_out_contributes_nothing():
    position = market_risk(assets=ANNUITY, liabilities=ANNUITY, curve=FLAT,
                           calibration=DELEGATED_2015)
    assert position.modules == {name: 0.0 for name in MARKET_RISKS}
    assert position.scr == 0.0
    assert position.diversification == 0.0
    assert position.reconciles()


def test_the_market_scr_moves_between_regimes_on_one_balance_sheet():
    """The number the two calibrations exist to keep apart."""
    old = _full_position(DELEGATED_2015)
    new = _full_position(DELEGATED_2026)
    assert new.modules["interest"] > old.modules["interest"]
    assert new.modules["spread"] == old.modules["spread"]
    assert new.modules["property"] == old.modules["property"]
    assert new.modules["currency"] == old.modules["currency"]
    assert new.scr > old.scr
