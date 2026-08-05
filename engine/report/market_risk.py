"""Solvency II market risk: the six sub-modules, and which direction binds.

PLAN.md §5.3 asks for the Solvency II standard formula. RFC-014 built the
technical provisions and the life underwriting stresses and scoped this out
in as many words:

    **Market risk**: interest up and down are expressible as a shift in the
    valuation rate and are supported, but equity, property, spread,
    concentration and currency need an asset model. That is the ALM overlay.

RFC-021 built the asset model and RFC-025 a trading strategy on top of it,
so what was left was the **prescribed data** — which is the whole of this
module's difficulty. Every number below is transcribed from the Official
Journal: Commission Delegated Regulation (EU) 2015/35 in the consolidated
version ``02015R0035 — EN — 30.07.2020 — 007.001``, and Commission
Delegated Regulation (EU) 2026/269 (OJ L, 18.2.2026). Each carries its
article.

Two calibrations, because two are in force
------------------------------------------
2026/269 entered into force on 10 March 2026 and **applies from 30 January
2027**. Both texts are therefore live, for different reporting dates, so
this module ships both as named, dated sets rather than baking one in:

- :data:`DELEGATED_2015` — reporting dates before 30 January 2027.
- :data:`DELEGATED_2026` — from 30 January 2027.

:func:`calibration_for` picks by reporting date, and the returned object
says which regime it is.

What actually changed
---------------------
The interest rate sub-module is rewritten. Under 2015/35 the shock is
purely multiplicative, with an absolute floor on the increase::

    up:    r*(m) = r(m) · (1 + s_up(m)),  at least r(m) + 1pp     [Art 166]
    down:  r*(m) = r(m) · (1 − s_down(m)), nil where r(m) < 0     [Art 167]

Under 2026/269 it is multiplicative **plus a parallel shift**, the one
percentage point minimum is gone, and "no shock where the rate is negative"
becomes a term-dependent floor::

    up:    r*(m) = r(m) · (1 + s_up(m)) + b_up(m)
    down:  r*(m) = r(m) · (1 − s_down(m)) − b_down(m),
           floored at −1.25% to 7 years, −0.893% from 20, interpolated

Elsewhere 2026/269 leaves the market risk parameters where they were —
property is still 25% (Art 174), currency still 25% (Art 188), the spread
table of Article 176(3) is unchanged, and so are the concentration
thresholds and factors of Articles 185 and 186. Three things in scope here
did move: the symmetric adjustment corridor widens from ±10% to ±13%
(Art 172(4)), Article 176(1) now excludes defaulted and forborne loans, and
the correlation between interest rate risk and spread risk gets its **own**
parameter.

The correlation matrix is direction-dependent
---------------------------------------------
Article 164(3)'s matrix does not contain a number where interest rate risk
meets equity, property or spread. It contains the symbol ``A``, which is 0
when the *up* shock is the binding one and 0.5 otherwise. 2026/269 splits
the spread cell out as ``B`` — 0 or 0.25.

That is not a detail. **The market SCR is not a function of the six module
capitals.** Two books with identical sub-module capitals aggregate to
different totals when the interest shock binds in different directions.
Every other correlation matrix in this library is a constant, and this one
has to be built per balance sheet.

Rates are shocked as spot rates, not as forwards
------------------------------------------------
:class:`engine.data.rates.YieldCurve` stores a rate per *period* — a
forward. The Delegated Regulation shocks "basic risk-free interest rates
for that currency at different maturities", which are the zero-coupon spot
rates EIOPA publishes. So :func:`stressed_curve` converts to spots, applies
the table at each maturity, and converts back; :func:`spot_rates` and
:func:`curve_from_spot_rates` are exact inverses. Shocking the stored
forwards instead is a different shock, and RFC-026 measures the difference.

The reconciliation invariants
-----------------------------
Every reporting overlay here is checked against a statement that cannot be
argued with. Market risk has two.

**The capital is a fall in own funds, not a formula on an exposure.**
Article 105(5) defines each sub-module as "the loss in the basic own funds
that would result from" a stated instantaneous change, so for each shock::

    capital = max(0, (A_base − L_base) − (A_stressed − L_stressed))

with both sides revalued. :class:`ShockResult` carries all four values and
:meth:`ShockResult.reconciles` checks the capital against them — so a
module that reaches for a duration approximation, or that charges a factor
against a total instead of against each holding, fails.

**Aggregation lies between the largest module and their sum.**
``max_i SCR_i ≤ SCR_market ≤ Σ_i SCR_i`` for any correlation matrix that is
positive semi-definite with entries in [0, 1]. RFC-014 found that a
plausible-looking matrix can break the lower bound and report no capital at
all. Here the matrix is *assembled per balance sheet* from a symbol, so it
has to hold in every substitution — both regimes, both directions — and
:meth:`MarketRiskPosition.reconciles` checks it on the position actually
reported.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

import numpy as np

from engine.data.rates import YieldCurve
from engine.report.embedded_value import present_value
from engine.report.solvency2 import CorrelationMatrix

# --------------------------------------------------------------------------
# Articles 166 and 167 — the interest rate tables
# --------------------------------------------------------------------------

#: Article 166(1), the maturity table for the upward shock, and Article
#: 167(1) for the downward one. Knots are ``(maturity in years, factor)``.
#: The regulation prescribes linear interpolation between tabulated points,
#: the first entry's value for maturities under a year, and the last
#: entry's value beyond 90 years — which is exactly a piecewise-linear
#: function held flat at both ends.
_2015_UP = (
    (1, 0.70), (2, 0.70), (3, 0.64), (4, 0.59), (5, 0.55), (6, 0.52),
    (7, 0.49), (8, 0.47), (9, 0.44), (10, 0.42), (11, 0.39), (12, 0.37),
    (13, 0.35), (14, 0.34), (15, 0.33), (16, 0.31), (17, 0.30), (18, 0.29),
    (19, 0.27), (20, 0.26), (90, 0.20),
)
_2015_DOWN = (
    (1, 0.75), (2, 0.65), (3, 0.56), (4, 0.50), (5, 0.46), (6, 0.42),
    (7, 0.39), (8, 0.36), (9, 0.33), (10, 0.31), (11, 0.30), (12, 0.29),
    (13, 0.28), (14, 0.28), (15, 0.27), (16, 0.28), (17, 0.28), (18, 0.28),
    (19, 0.29), (20, 0.29), (90, 0.20),
)

#: Article 166(2) as replaced by 2026/269 point (43): ``(m, s_up, b_up)``
#: for integer maturities 1 to 50. Article 166(2)(d)(iii) then sets
#: ``b_up`` to zero from 60 years and ``s_up`` to 20% from 90, and (iv)
#: interpolates everything in between.
_2026_UP_TABLE = (
    (1, 0.61, 0.0214), (2, 0.53, 0.0186), (3, 0.49, 0.0172),
    (4, 0.46, 0.0161), (5, 0.45, 0.0158), (6, 0.41, 0.0144),
    (7, 0.37, 0.0130), (8, 0.34, 0.0119), (9, 0.32, 0.0112),
    (10, 0.30, 0.0105), (11, 0.30, 0.0105), (12, 0.30, 0.0105),
    (13, 0.30, 0.0105), (14, 0.29, 0.0102), (15, 0.28, 0.0098),
    (16, 0.28, 0.0098), (17, 0.27, 0.0095), (18, 0.26, 0.0091),
    (19, 0.26, 0.0091), (20, 0.25, 0.0088), (21, 0.25, 0.0087),
    (22, 0.24, 0.0085), (23, 0.24, 0.0082), (24, 0.23, 0.0080),
    (25, 0.22, 0.0078), (26, 0.22, 0.0076), (27, 0.21, 0.0074),
    (28, 0.21, 0.0072), (29, 0.20, 0.0070), (30, 0.20, 0.0069),
    (31, 0.20, 0.0070), (32, 0.20, 0.0071), (33, 0.20, 0.0071),
    (34, 0.20, 0.0071), (35, 0.20, 0.0071), (36, 0.20, 0.0072),
    (37, 0.21, 0.0072), (38, 0.21, 0.0072), (39, 0.21, 0.0073),
    (40, 0.21, 0.0073), (41, 0.21, 0.0074), (42, 0.21, 0.0074),
    (43, 0.21, 0.0075), (44, 0.21, 0.0075), (45, 0.21, 0.0075),
    (46, 0.21, 0.0075), (47, 0.21, 0.0075), (48, 0.21, 0.0074),
    (49, 0.21, 0.0074), (50, 0.21, 0.0073),
)

#: Article 167(2) as replaced by 2026/269 point (44), same layout. The
#: shape is the striking part: the relative decrease *rises* with term from
#: 37% at seven years to 65% at fifty, where the 2015 table falls to 29%
#: and stays there.
_2026_DOWN_TABLE = (
    (1, 0.58, 0.0116), (2, 0.51, 0.0099), (3, 0.44, 0.0083),
    (4, 0.40, 0.0074), (5, 0.40, 0.0071), (6, 0.38, 0.0067),
    (7, 0.37, 0.0063), (8, 0.38, 0.0062), (9, 0.39, 0.0061),
    (10, 0.40, 0.0061), (11, 0.41, 0.0060), (12, 0.42, 0.0060),
    (13, 0.43, 0.0059), (14, 0.44, 0.0058), (15, 0.45, 0.0057),
    (16, 0.47, 0.0056), (17, 0.48, 0.0055), (18, 0.49, 0.0054),
    (19, 0.49, 0.0052), (20, 0.50, 0.0050), (21, 0.49, 0.0049),
    (22, 0.50, 0.0049), (23, 0.51, 0.0048), (24, 0.51, 0.0048),
    (25, 0.52, 0.0047), (26, 0.52, 0.0046), (27, 0.53, 0.0045),
    (28, 0.53, 0.0044), (29, 0.53, 0.0042), (30, 0.53, 0.0041),
    (31, 0.53, 0.0040), (32, 0.53, 0.0039), (33, 0.54, 0.0037),
    (34, 0.54, 0.0036), (35, 0.54, 0.0035), (36, 0.54, 0.0034),
    (37, 0.55, 0.0033), (38, 0.55, 0.0032), (39, 0.56, 0.0031),
    (40, 0.57, 0.0030), (41, 0.57, 0.0029), (42, 0.58, 0.0028),
    (43, 0.59, 0.0027), (44, 0.61, 0.0026), (45, 0.62, 0.0025),
    (46, 0.62, 0.0023), (47, 0.63, 0.0022), (48, 0.64, 0.0021),
    (49, 0.64, 0.0019), (50, 0.65, 0.0018),
)

#: Article 167(1) as replaced by 2026/269: the floor on the decreased rate.
#: −1.25% between 1 and 7 years, −0.893% from 20 years, interpolated
#: between. Maturities under a year are not addressed by the text; the
#: short-end value is carried down to zero, which is the only reading that
#: leaves the floor continuous, and it is stated here rather than hidden.
_2026_DOWN_FLOOR = ((0.0, -0.0125), (7.0, -0.0125), (20.0, -0.00893))


def _knots(pairs):
    """``(x, y)`` pairs as two float arrays, for :func:`numpy.interp`."""
    x = np.array([p[0] for p in pairs], dtype=np.float64)
    y = np.array([p[1] for p in pairs], dtype=np.float64)
    if x.size == 0:
        raise ValueError("a table needs at least one point")
    if np.any(np.diff(x) <= 0.0):
        raise ValueError("interpolation knots must be strictly increasing")
    return x, y


@dataclass(frozen=True)
class InterestShockTable:
    """One direction of the interest rate shock, as a function of maturity.

    ``relative`` is the multiplicative factor and ``parallel`` the absolute
    addition, in the same units as the rate. Under 2015/35 the parallel
    part does not exist and the tuple is empty; under 2026/269 both are
    tabulated. Both are piecewise linear in the maturity and held flat
    outside the tabulated range — which is what Article 166's "for
    maturities not specified in the table above, the value shall be
    linearly interpolated ... for maturities shorter than 1 year ... for
    maturities longer than 90 years" describes.
    """

    relative: tuple
    parallel: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "_relative", _knots(self.relative))
        object.__setattr__(
            self, "_parallel", _knots(self.parallel) if self.parallel else None
        )

    def relative_at(self, maturities) -> np.ndarray:
        x, y = self._relative
        return np.interp(np.asarray(maturities, dtype=np.float64), x, y)

    def parallel_at(self, maturities) -> np.ndarray:
        maturities = np.asarray(maturities, dtype=np.float64)
        if self._parallel is None:
            return np.zeros(maturities.shape, dtype=np.float64)
        x, y = self._parallel
        return np.interp(maturities, x, y)

    def __fingerprint__(self):
        return {"relative": self.relative, "parallel": self.parallel}


@dataclass(frozen=True)
class InterestRateCalibration:
    """Articles 165 to 167 for one regime."""

    up: InterestShockTable
    down: InterestShockTable
    #: Article 166(2): "the increase ... shall be at least one percentage
    #: point". ``None`` under 2026/269, which deleted it.
    minimum_increase: float | None = None
    #: Article 167(2) as enacted: "for negative basic risk-free interest
    #: rates the decrease shall be nil".
    nil_decrease_when_negative: bool = False
    #: Article 167(1) as replaced by 2026/269: a term-dependent floor on
    #: the decreased rate. ``None`` under 2015/35.
    down_floor: tuple | None = None

    def __post_init__(self):
        object.__setattr__(
            self, "_floor", _knots(self.down_floor) if self.down_floor else None
        )

    def floor_at(self, maturities):
        if self._floor is None:
            return None
        x, y = self._floor
        return np.interp(np.asarray(maturities, dtype=np.float64), x, y)


# --------------------------------------------------------------------------
# Articles 168 to 174 — equity and property
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EquityCalibration:
    """Article 169's instantaneous decreases and Article 172's adjustment.

    The symmetric adjustment enters type 1 and type 2 in full and the two
    infrastructure buckets at a weight — 77% for qualifying infrastructure
    equity and 92% for qualifying infrastructure corporate equity, Article
    169(3)(c) and (4)(c).
    """

    type1: float = 0.39                              # Art 169(1)(c)
    type2: float = 0.49                              # Art 169(2)(c)
    infrastructure: float = 0.30                     # Art 169(3)(c)
    infrastructure_corporate: float = 0.36           # Art 169(4)(c)
    infrastructure_weight: float = 0.77              # Art 169(3)(c)
    infrastructure_corporate_weight: float = 0.92    # Art 169(4)(c)
    strategic: float = 0.22                          # Art 169(1)(a), Art 171
    long_term: float = 0.22                          # Art 169(1)(b)
    #: Article 172(4). ±10% as enacted, ±13% under 2026/269 point (51).
    symmetric_cap: float = 0.10
    #: Article 168(4): type 1 against the sum of the other three.
    cross_correlation: float = 0.75


# --------------------------------------------------------------------------
# Article 176 — spread
# --------------------------------------------------------------------------

#: Article 176(3), the ``a_i`` table. Rows are the duration bands whose
#: lower edges are :data:`_SPREAD_BAND_FLOOR`; columns are credit quality
#: steps 0 to 6. The published table gives one column for "5 and 6",
#: carried here as two identical columns so that a step is always an index.
_SPREAD_BAND_FLOOR = (0.0, 5.0, 10.0, 15.0, 20.0)
_SPREAD_A = (
    (0.000, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000),
    (0.045, 0.055, 0.070, 0.125, 0.225, 0.375, 0.375),
    (0.070, 0.085, 0.105, 0.200, 0.350, 0.585, 0.585),
    (0.095, 0.110, 0.130, 0.250, 0.440, 0.610, 0.610),
    (0.120, 0.135, 0.155, 0.300, 0.466, 0.635, 0.635),
)
#: Article 176(3), the ``b_i`` table, same layout.
_SPREAD_B = (
    (0.009, 0.011, 0.014, 0.025, 0.045, 0.075, 0.075),
    (0.005, 0.006, 0.007, 0.015, 0.025, 0.042, 0.042),
    (0.005, 0.005, 0.005, 0.010, 0.018, 0.005, 0.005),
    (0.005, 0.005, 0.005, 0.010, 0.005, 0.005, 0.005),
    (0.005, 0.005, 0.005, 0.005, 0.005, 0.005, 0.005),
)
#: Article 176(4), for bonds and loans with no ECAI assessment and no
#: eligible collateral: ``(band floor, a, b)`` in ``a + b · (dur − floor)``.
#: Four bands rather than five, with 10 to 20 years undivided.
_UNRATED_BANDS = (
    (0.0, 0.000, 0.030),
    (5.0, 0.150, 0.017),
    (10.0, 0.235, 0.012),
    (20.0, 0.355, 0.005),
)


@dataclass(frozen=True)
class SpreadCalibration:
    """Article 176's factor tables.

    Identical under both regimes — 2026/269 amended Article 176(1)'s scope
    and left paragraphs 3 and 4 alone — but held as dated data anyway,
    because the next amendment will not be so kind and a table that lives
    in a calibration can be replaced without touching the arithmetic.
    """

    band_floor: tuple = _SPREAD_BAND_FLOOR
    a: tuple = _SPREAD_A
    b: tuple = _SPREAD_B
    unrated: tuple = _UNRATED_BANDS

    def __post_init__(self):
        object.__setattr__(self, "_floor", np.array(self.band_floor, float))
        object.__setattr__(self, "_a", np.array(self.a, float))
        object.__setattr__(self, "_b", np.array(self.b, float))
        object.__setattr__(
            self, "_unrated", tuple(np.array([u[i] for u in self.unrated],
                                             dtype=np.float64)
                                    for i in range(3))
        )


# --------------------------------------------------------------------------
# Articles 184 to 187 — concentration
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ConcentrationCalibration:
    """Articles 185 to 187."""

    #: Article 185: the relative excess exposure threshold ``CT_i`` by
    #: weighted average credit quality step 0 to 6.
    threshold: tuple = (0.03, 0.03, 0.03, 0.015, 0.015, 0.015, 0.015)
    #: Article 186(1): the risk factor ``g_i`` by the same step.
    factor: tuple = (0.12, 0.12, 0.21, 0.27, 0.73, 0.73, 0.73)
    #: Article 187(1): covered bonds at step 0 or 1.
    covered_bond_threshold: float = 0.15
    #: Article 187(2): a single immovable property.
    property_threshold: float = 0.10
    property_factor: float = 0.12
    #: Article 186(3), last subparagraph, and 186(4) and (5): the factor
    #: for an unrated insurer before it has published a solvency and
    #: financial condition report, an equivalent third-country insurer, or
    #: a bank meeting its own solvency requirements.
    unrated_regulated_factor: float = 0.645
    #: Article 186(6): everything else.
    residual_factor: float = 0.73
    #: Article 186(2): for an unrated insurer that meets its Minimum
    #: Capital Requirement, the factor comes off its own solvency ratio.
    solvency_ratio: tuple = (0.95, 1.00, 1.22, 1.75, 1.96)
    solvency_ratio_factor: tuple = (0.73, 0.645, 0.27, 0.21, 0.12)


# --------------------------------------------------------------------------
# The two regimes
# --------------------------------------------------------------------------

#: Article 164(1). The order is the order of Article 164(3)'s matrix.
MARKET_RISKS = ("interest", "equity", "property", "spread", "concentration",
                "currency")


@dataclass(frozen=True)
class MarketRiskCalibration:
    """One dated set of Article 164 to 188 parameters.

    ``applies_from`` is the date the regime bites, which is what makes
    choosing between them a reporting-date question rather than a
    preference.
    """

    name: str
    applies_from: _dt.date
    interest: InterestRateCalibration
    equity: EquityCalibration
    spread: SpreadCalibration
    concentration: ConcentrationCalibration
    property_factor: float = 0.25       # Art 174
    currency_factor: float = 0.25       # Art 188(3) and (4)
    #: Article 164(3)'s parameter ``A``: the correlation of interest rate
    #: risk with equity, property and spread when the *down* shock binds.
    #: Zero when the up shock binds.
    interest_correlation: float = 0.50
    #: The same cell against spread risk. 2026/269 gives it its own
    #: parameter ``B``; before that it is ``A`` and the two are equal.
    interest_spread_correlation: float = 0.50
    source: str = ""

    def __fingerprint__(self):
        return {"name": self.name,
                "applies_from": self.applies_from.isoformat()}


DELEGATED_2015 = MarketRiskCalibration(
    name="2015/35",
    applies_from=_dt.date(2016, 1, 1),
    interest=InterestRateCalibration(
        up=InterestShockTable(relative=_2015_UP),
        down=InterestShockTable(relative=_2015_DOWN),
        minimum_increase=0.01,
        nil_decrease_when_negative=True,
    ),
    equity=EquityCalibration(symmetric_cap=0.10),
    spread=SpreadCalibration(),
    concentration=ConcentrationCalibration(),
    source=("Commission Delegated Regulation (EU) 2015/35, consolidated "
            "text 02015R0035 — EN — 30.07.2020 — 007.001"),
)

DELEGATED_2026 = MarketRiskCalibration(
    name="2026/269",
    applies_from=_dt.date(2027, 1, 30),
    interest=InterestRateCalibration(
        up=InterestShockTable(
            relative=tuple((m, s) for m, s, _ in _2026_UP_TABLE) + ((90, 0.20),),
            parallel=(tuple((m, b) for m, _, b in _2026_UP_TABLE)
                      + ((60, 0.0), (90, 0.0))),
        ),
        down=InterestShockTable(
            relative=(tuple((m, s) for m, s, _ in _2026_DOWN_TABLE)
                      + ((90, 0.20),)),
            parallel=(tuple((m, b) for m, _, b in _2026_DOWN_TABLE)
                      + ((60, 0.0), (90, 0.0))),
        ),
        minimum_increase=None,
        nil_decrease_when_negative=False,
        down_floor=_2026_DOWN_FLOOR,
    ),
    equity=EquityCalibration(symmetric_cap=0.13),
    spread=SpreadCalibration(),
    concentration=ConcentrationCalibration(),
    interest_correlation=0.50,
    interest_spread_correlation=0.25,
    source=("Commission Delegated Regulation (EU) 2026/269, OJ L, "
            "18.2.2026; applies from 30 January 2027"),
)

CALIBRATIONS = (DELEGATED_2015, DELEGATED_2026)


def calibration_for(reporting_date=None) -> MarketRiskCalibration:
    """The regime in force at ``reporting_date``.

    Defaults to today, which is a convenience and not a policy: a reporting
    run should pass the date it reports at.
    """
    if reporting_date is None:
        reporting_date = _dt.date.today()
    if isinstance(reporting_date, _dt.datetime):
        reporting_date = reporting_date.date()
    chosen = None
    for calibration in sorted(CALIBRATIONS, key=lambda c: c.applies_from):
        if reporting_date >= calibration.applies_from:
            chosen = calibration
    if chosen is None:
        raise ValueError(
            f"{reporting_date} is before "
            f"{min(c.applies_from for c in CALIBRATIONS)}, when the standard "
            "formula first applied"
        )
    return chosen


# --------------------------------------------------------------------------
# Spot rates: what the regulation actually shocks
# --------------------------------------------------------------------------

def spot_rates(curve: YieldCurve, n_periods: int | None = None) -> np.ndarray:
    """Annual effective zero-coupon rates at maturities ``1/freq, 2/freq, ...``.

    :class:`YieldCurve` stores one rate per period — a forward. The
    Delegated Regulation shocks the *spot* curve, so this is the
    conversion, and :func:`curve_from_spot_rates` is its exact inverse.
    """
    n = curve.n_periods - 1 if n_periods is None else int(n_periods)
    if n < 1:
        raise ValueError(f"need at least one maturity, got {n}")
    df = curve.discount_factors(n + 1)[1:]
    years = np.arange(1, n + 1, dtype=np.float64) / curve.freq
    return df ** (-1.0 / years) - 1.0


def curve_from_spot_rates(spots, freq: int, horizon_years: int) -> YieldCurve:
    """Rebuild a :class:`YieldCurve` from annual effective spot rates.

    The inverse of :func:`spot_rates`. The rate array is assigned rather
    than passed to the constructor, which repeats each entry ``freq`` times
    — the same idiom :meth:`YieldCurve.convert_freq` uses, and for the same
    reason: these are already per-period rates.

    The last period has no spot rate to imply it, so it repeats the one
    before. At the default 120-year horizon that is the forward over the
    final period, which nothing values anything at.
    """
    spots = np.asarray(spots, dtype=np.float64).ravel()
    n = spots.size
    if n < 1:
        raise ValueError("need at least one spot rate")
    years = np.arange(1, n + 1, dtype=np.float64) / freq
    df = (1.0 + spots) ** (-years)
    previous = np.concatenate(([1.0], df[:-1]))
    forwards = (previous / df) ** freq - 1.0
    curve = YieldCurve([0.0], freq=freq, horizon_years=horizon_years)
    if n >= curve.n_periods:
        curve.rates = forwards[:curve.n_periods]
    else:
        curve.rates = np.concatenate(
            [forwards, np.full(curve.n_periods - n, forwards[-1])]
        )
    return curve


def stressed_curve(curve: YieldCurve, calibration: MarketRiskCalibration,
                   direction: str) -> YieldCurve:
    """The curve after the Article 166 or 167 shock.

    ``direction`` is ``"up"`` or ``"down"``. The shock lands on the spot
    rate at each maturity and the curve is rebuilt, because that is what
    the text prescribes.
    """
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    rules = calibration.interest
    table = rules.up if direction == "up" else rules.down
    spots = spot_rates(curve)
    maturities = np.arange(1, spots.size + 1, dtype=np.float64) / curve.freq
    relative = table.relative_at(maturities)
    parallel = table.parallel_at(maturities)

    if direction == "up":
        shocked = spots * (1.0 + relative) + parallel
        if rules.minimum_increase is not None:
            # Article 166(2). A minimum on the *increase*, so it can only
            # raise the shocked rate and never lower it.
            shocked = np.maximum(shocked, spots + rules.minimum_increase)
    else:
        shocked = spots * (1.0 - relative) - parallel
        if rules.nil_decrease_when_negative:
            # Article 167(2) as enacted: no decrease at all where the rate
            # is negative. An indicator rather than a branch, so a whole
            # curve evaluates at once.
            negative = spots < 0.0
            shocked = shocked * (1.0 - negative) + spots * negative
        floor = rules.floor_at(maturities)
        if floor is not None:
            shocked = np.maximum(shocked, floor)
    return curve_from_spot_rates(shocked, curve.freq, curve.horizon_years)


# --------------------------------------------------------------------------
# The loss in basic own funds
# --------------------------------------------------------------------------

@dataclass
class ShockResult:
    """One shock: both sides revalued, and the capital that falls out.

    Article 105(5) defines every market sub-module as "the loss in the
    basic own funds that would result from" a stated instantaneous change.
    So the capital is not a formula applied to an exposure — it is a
    difference of two balance sheets, and this carries both so the
    difference can be checked rather than trusted.

    ``capital`` is what the sub-module reports by its own route.
    :meth:`reconciles` checks it against the four values.
    """

    name: str
    capital: float
    assets_base: float
    assets_stressed: float
    liabilities_base: float = 0.0
    liabilities_stressed: float = 0.0

    @property
    def own_funds_base(self) -> float:
        return self.assets_base - self.liabilities_base

    @property
    def own_funds_stressed(self) -> float:
        return self.assets_stressed - self.liabilities_stressed

    @property
    def loss(self) -> float:
        """The fall in basic own funds, which may be negative."""
        return self.own_funds_base - self.own_funds_stressed

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """Is the reported capital the fall in own funds, floored at zero?

        A shock that *improves* own funds does not release capital — the
        floor is the standard's, not a convenience.
        """
        scale = max(1.0, abs(self.assets_base), abs(self.liabilities_base))
        return abs(self.capital - max(self.loss, 0.0)) <= tolerance * scale

    def __repr__(self) -> str:
        return (f"ShockResult({self.name!r}, capital={self.capital:,.2f}, "
                f"OF {self.own_funds_base:,.2f} -> "
                f"{self.own_funds_stressed:,.2f})")


def interest_rate_capital(assets, liabilities, curve: YieldCurve,
                          calibration: MarketRiskCalibration, *,
                          asset_timing: str = "end",
                          liability_timing: str = "end") -> tuple:
    """Article 165: the larger of the up and down capital requirements.

    ``assets`` and ``liabilities`` are cashflow series on ``curve``'s
    frequency. Both sides are revalued at the stressed curve — the asset
    side moves too, which is the whole reason a duration-matched fund is
    interesting here.

    Returns ``(capital, direction, {"up": ShockResult, "down": ShockResult})``.
    The direction is reported because Article 164(3)'s correlation matrix
    depends on it, so the aggregate is not a function of the module
    capitals alone.
    """
    base_assets = present_value(assets, curve, asset_timing)
    base_liabilities = present_value(liabilities, curve, liability_timing)
    results = {}
    for direction in ("up", "down"):
        shocked = stressed_curve(curve, calibration, direction)
        stressed_assets = present_value(assets, shocked, asset_timing)
        stressed_liabilities = present_value(liabilities, shocked,
                                             liability_timing)
        loss = ((base_assets - base_liabilities)
                - (stressed_assets - stressed_liabilities))
        results[direction] = ShockResult(
            name=f"interest_{direction}",
            capital=max(loss, 0.0),
            assets_base=base_assets, assets_stressed=stressed_assets,
            liabilities_base=base_liabilities,
            liabilities_stressed=stressed_liabilities,
        )
    # Article 165(1): the larger of the two. A tie goes to "up", which is
    # the regulation's own ordering and the substitution that makes
    # parameter A zero.
    if results["down"].capital > results["up"].capital:
        return results["down"].capital, "down", results
    return results["up"].capital, "up", results


# --------------------------------------------------------------------------
# Articles 169 and 172 — equity
# --------------------------------------------------------------------------

def symmetric_adjustment(current_index: float, average_index: float,
                         calibration: MarketRiskCalibration) -> float:
    """Article 172(2): ``½·((CI − AI)/AI − 8%)``, capped at ±10% or ±13%.

    ``AI`` is the equally weighted average of the daily index levels over
    the last 36 months — Article 172(3) — which this takes as an input,
    because it is data and not a calculation. Note the −8%: the adjustment
    is zero when the index sits 8% *above* its own three-year average, not
    when it sits on it.
    """
    if average_index <= 0.0:
        raise ValueError(
            f"the 36-month average index level {average_index} must be "
            "positive; it is a denominator"
        )
    raw = 0.5 * ((current_index - average_index) / average_index - 0.08)
    cap = calibration.equity.symmetric_cap
    return float(min(max(raw, -cap), cap))


@dataclass
class EquityExposure:
    """Market values by Article 168 bucket.

    ``strategic`` and ``long_term`` are the 22% carve-outs of Article
    169(1)(a) and (b); everything else takes its bucket's factor plus its
    share of the symmetric adjustment.
    """

    type1: float = 0.0
    type2: float = 0.0
    infrastructure: float = 0.0
    infrastructure_corporate: float = 0.0
    strategic: float = 0.0
    long_term: float = 0.0

    def total(self) -> float:
        return (self.type1 + self.type2 + self.infrastructure
                + self.infrastructure_corporate + self.strategic
                + self.long_term)


def equity_capital(exposure: EquityExposure,
                   calibration: MarketRiskCalibration, *,
                   symmetric: float = 0.0) -> tuple:
    """Article 168(4)'s aggregation of Article 169's decreases.

    ``SCR_equity = sqrt(SCR₁² + 2·0.75·SCR₁·R + R²)`` where ``R`` is the
    **sum** of the type 2, infrastructure and infrastructure corporate
    charges — Article 168(4) as corrected by corrigendum C2. The three are
    summed first and then correlated with type 1 as a block, rather than
    each correlated with type 1 separately, and the two readings give
    different numbers.

    The 22% buckets are strategic participations and long-term equity
    investments, which the standard attaches to whichever bucket they sit
    in. This charges them at 22% and puts them with type 1, where a
    strategic participation in a related undertaking normally sits; a
    caller holding strategic type 2 equity can split the exposure itself.

    Returns ``(capital, per-bucket charges)``.
    """
    eq = calibration.equity
    factors = {
        "type1": eq.type1 + symmetric,
        "type2": eq.type2 + symmetric,
        "infrastructure": eq.infrastructure
        + eq.infrastructure_weight * symmetric,
        "infrastructure_corporate": eq.infrastructure_corporate
        + eq.infrastructure_corporate_weight * symmetric,
        "strategic": eq.strategic,
        "long_term": eq.long_term,
    }
    charges = {name: getattr(exposure, name) * factor
               for name, factor in factors.items()}
    first = charges["type1"] + charges["strategic"] + charges["long_term"]
    rest = (charges["type2"] + charges["infrastructure"]
            + charges["infrastructure_corporate"])
    total = math.sqrt(
        first * first + 2.0 * eq.cross_correlation * first * rest + rest * rest
    )
    return total, charges


# --------------------------------------------------------------------------
# Articles 174 and 188 — property and currency
# --------------------------------------------------------------------------

def property_capital(value: float,
                     calibration: MarketRiskCalibration) -> ShockResult:
    """Article 174: an instantaneous 25% decrease in immovable property."""
    value = float(value)
    charge = value * calibration.property_factor
    return ShockResult(name="property", capital=charge, assets_base=value,
                       assets_stressed=value - charge)


def currency_capital(net_positions, calibration: MarketRiskCalibration) -> tuple:
    """Article 188: the **sum** over foreign currencies of the worse direction.

    ``net_positions`` maps currency to the net asset position translated
    into the local currency — assets less liabilities in that currency. A
    long position loses on a 25% fall and a short one on a 25% rise, so the
    worse of the two is ``25% · |position|``; the two are kept apart rather
    than collapsed, because Article 188(2) asks for the larger of two named
    requirements and reporting which one binds says something real.

    Article 188(1) sums across currencies with no diversification at all,
    which is the sub-module's most distinctive feature: it is the only one
    in the market module that does not aggregate.

    Returns ``(capital, {currency: (capital, direction)})``.
    """
    factor = calibration.currency_factor
    per_currency = {}
    total = 0.0
    for currency, position in net_positions.items():
        position = float(position)
        rise = max(-factor * position, 0.0)   # currency up: a short loses
        fall = max(factor * position, 0.0)    # currency down: a long loses
        capital = max(rise, fall)
        per_currency[currency] = (capital, "up" if rise > fall else "down")
        total += capital
    return total, per_currency


# --------------------------------------------------------------------------
# Article 176 — spread
# --------------------------------------------------------------------------

def spread_factor(credit_quality_step, duration,
                  calibration: MarketRiskCalibration) -> np.ndarray:
    """Article 176(3): ``stress_i`` for rated bonds and loans.

    ``duration`` is the modified duration in years, which Article 176(2)
    floors at 1. The band is selected by indicator rather than by branch,
    so one call prices a whole portfolio.

    The last band is ``min(a + b·(dur − 20), 1)`` — a bond cannot lose more
    than all of itself.
    """
    table = calibration.spread
    cqs = np.asarray(credit_quality_step)
    dur = np.maximum(np.asarray(duration, dtype=np.float64), 1.0)
    if np.any((cqs < 0) | (cqs > 6)):
        raise ValueError("credit quality steps run from 0 to 6")
    cqs = cqs.astype(np.intp)
    floors = table._floor
    band = np.clip(np.searchsorted(floors, dur, side="left") - 1, 0,
                   floors.size - 1)
    a = table._a[band, cqs]
    b = table._b[band, cqs]
    return np.minimum(a + b * (dur - floors[band]), 1.0)


def unrated_spread_factor(duration,
                          calibration: MarketRiskCalibration) -> np.ndarray:
    """Article 176(4): ``stress_i`` where no ECAI assessment is available.

    Four bands rather than five, with the 10 to 20 year band undivided —
    not a simplification of the rated table but a separate calibration, and
    one that is *kinder* than credit quality step 4 at every duration.
    """
    floors, a, b = calibration.spread._unrated
    dur = np.maximum(np.asarray(duration, dtype=np.float64), 1.0)
    band = np.clip(np.searchsorted(floors, dur, side="left") - 1, 0,
                   floors.size - 1)
    return np.minimum(a[band] + b[band] * (dur - floors[band]), 1.0)


def spread_capital(values, durations, credit_quality_step=None, *,
                   calibration: MarketRiskCalibration,
                   collateralised=None) -> ShockResult:
    """Article 176(1): the loss from an instantaneous relative decrease.

    ``credit_quality_step`` of ``None``, or an entry of ``-1``, means no
    ECAI assessment and takes Article 176(4)'s table instead.
    ``collateralised`` marks unrated holdings whose collateral meets
    Article 214, for which Article 176(5)(a) halves the factor when the
    risk-adjusted collateral covers the whole exposure. Only that first
    case is carried; (b) and (c) need the risk-adjusted collateral value,
    which is Article 112, 197 and 198 machinery this module does not have.

    The charge is computed per holding and summed — never as an average
    factor against a total, which is a different number whenever the
    durations differ. :meth:`ShockResult.reconciles` is what holds that
    line: the stressed asset value is built by scaling each holding, so the
    two sides only agree if the charge was additive.
    """
    values = np.asarray(values, dtype=np.float64)
    durations = np.asarray(durations, dtype=np.float64)
    if credit_quality_step is None:
        steps = np.full(values.shape, -1, dtype=np.intp)
    else:
        steps = np.asarray(credit_quality_step).astype(np.intp)
    rated = steps >= 0
    stress = np.where(
        rated,
        spread_factor(np.where(rated, steps, 0), durations, calibration),
        unrated_spread_factor(durations, calibration),
    )
    if collateralised is not None:
        covered = np.asarray(collateralised, dtype=bool) & ~rated
        stress = stress * (1.0 - 0.5 * covered)
    return ShockResult(
        name="spread",
        capital=float((values * stress).sum()),
        assets_base=float(values.sum()),
        assets_stressed=float((values * (1.0 - stress)).sum()),
    )


# --------------------------------------------------------------------------
# Articles 183 to 187 — concentration
# --------------------------------------------------------------------------

def insurer_concentration_factor(solvency_ratio,
                                 calibration: MarketRiskCalibration, *,
                                 meets_mcr: bool = True,
                                 disclosed: bool = True) -> np.ndarray:
    """Article 186(2) and (3): the factor for an unrated insurer.

    The only place in the market risk module where a capital charge depends
    on the *counterparty's own* solvency position rather than on a rating
    or an asset class. Five tabulated ratios with linear interpolation
    between, 73% below 95% and 12% above 196%.

    An undertaking that does not meet its Minimum Capital Requirement takes
    73% under Article 186(3), and one that has not yet published a solvency
    and financial condition report takes 64.5% under that article's last
    subparagraph — the same figure Article 186(4) and (5) give an
    equivalent third-country insurer or a bank.

    Note the shape: the factor falls from 73% to 27% between a 100% and a
    122% solvency ratio and only from 27% to 12% over the whole range from
    122% to 196%. Almost all of the relief is bought in the first
    twenty-two points above the requirement.
    """
    conc = calibration.concentration
    ratio = np.asarray(solvency_ratio, dtype=np.float64)
    x = np.asarray(conc.solvency_ratio, dtype=np.float64)
    y = np.asarray(conc.solvency_ratio_factor, dtype=np.float64)
    # np.interp needs an increasing x, which this table has; y falls.
    factor = np.interp(ratio, x, y)
    if not disclosed:
        return np.full(ratio.shape, conc.unrated_regulated_factor)
    if not meets_mcr:
        return np.full(ratio.shape, conc.residual_factor)
    return factor


def concentration_capital(exposures, credit_quality_step, assets: float,
                          calibration: MarketRiskCalibration, *,
                          thresholds=None, factors=None) -> tuple:
    """Articles 183 to 186: excess exposure over a threshold, then a root.

    ``XS_i = max(0, E_i − CT_i · Assets)`` — Article 184(1) — and
    ``Conc_i = XS_i · g_i``, aggregated as ``sqrt(Σ Conc_i²)`` under
    Article 183(1). The aggregation is a plain Euclidean norm: single name
    exposures are treated as independent of each other, which is why
    splitting one counterparty in two reduces the capital, and why Article
    182(1) treats a corporate group as a single name.

    ``thresholds`` and ``factors`` override Articles 185 and 186 per name,
    which is how Article 187's covered bonds, immovable property and
    zero-weighted sovereigns get in.

    Returns ``(capital, per-name capital)``.
    """
    exposures = np.asarray(exposures, dtype=np.float64)
    steps = np.asarray(credit_quality_step).astype(np.intp)
    if np.any((steps < 0) | (steps > 6)):
        raise ValueError("credit quality steps run from 0 to 6")
    if assets <= 0.0:
        raise ValueError(
            f"the calculation base is {assets}; Article 184(2) makes it the "
            "value of the assets in scope and it cannot be empty"
        )
    ct = (np.asarray(calibration.concentration.threshold)[steps]
          if thresholds is None else np.asarray(thresholds, dtype=np.float64))
    g = (np.asarray(calibration.concentration.factor)[steps]
         if factors is None else np.asarray(factors, dtype=np.float64))
    excess = np.maximum(exposures - ct * assets, 0.0)
    per_name = excess * g
    return float(np.sqrt((per_name * per_name).sum())), per_name


# --------------------------------------------------------------------------
# Article 164 — aggregation
# --------------------------------------------------------------------------

def market_correlation(calibration: MarketRiskCalibration, *,
                       interest_direction: str) -> CorrelationMatrix:
    """Article 164(3), with parameter ``A`` — and, from 2026/269, ``B`` — put in.

    ``A`` is 0 where the capital requirement for interest rate risk is the
    *upward* one and 0.5 otherwise; ``B`` is the same switch at 0.25 and
    covers only the interest-versus-spread cell.

    :class:`CorrelationMatrix` checks positive semi-definiteness on
    construction. Worth checking here more than anywhere: the regulation
    prints a matrix containing a *symbol*, and nothing in the text promises
    that every substitution of it is a valid correlation matrix.
    """
    if interest_direction not in ("up", "down"):
        raise ValueError(
            f"interest direction must be 'up' or 'down', got "
            f"{interest_direction!r}"
        )
    binds_down = interest_direction == "down"
    a = calibration.interest_correlation if binds_down else 0.0
    b = calibration.interest_spread_correlation if binds_down else 0.0
    matrix = [
        [1.00, a,    a,    b,    0.00, 0.25],
        [a,    1.00, 0.75, 0.75, 0.00, 0.25],
        [a,    0.75, 1.00, 0.50, 0.00, 0.25],
        [b,    0.75, 0.50, 1.00, 0.00, 0.25],
        [0.00, 0.00, 0.00, 0.00, 1.00, 0.00],
        [0.25, 0.25, 0.25, 0.25, 0.00, 1.00],
    ]
    return CorrelationMatrix(MARKET_RISKS, matrix)


@dataclass
class MarketRiskPosition:
    """The six sub-modules, the direction that binds, and the aggregate."""

    modules: dict
    interest_direction: str
    calibration: MarketRiskCalibration
    shocks: dict = field(default_factory=dict)

    @property
    def correlation(self) -> CorrelationMatrix:
        return market_correlation(self.calibration,
                                  interest_direction=self.interest_direction)

    @property
    def scr(self) -> float:
        """Article 164(2): ``sqrt(Σ Corr(i,j) · SCR_i · SCR_j)``."""
        return self.correlation.aggregate(self.modules)

    @property
    def undiversified(self) -> float:
        return float(sum(max(v, 0.0) for v in self.modules.values()))

    @property
    def largest_module(self) -> float:
        return float(max((max(v, 0.0) for v in self.modules.values()),
                         default=0.0))

    @property
    def diversification(self) -> float:
        total = self.undiversified
        return 0.0 if total <= 0.0 else 1.0 - self.scr / total

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """Both invariants: every recorded shock, and the aggregation bounds.

        The bounds are the second half of RFC-014's finding, applied to a
        matrix that is assembled rather than fixed. ``max_i SCR_i ≤
        SCR_market ≤ Σ_i SCR_i`` holds for every positive semi-definite
        matrix with entries in [0, 1], and fails loudly for one that is
        not.
        """
        if not all(shock.reconciles(tolerance)
                   for shock in self.shocks.values()):
            return False
        scale = max(1.0, self.undiversified)
        scr = self.scr
        return (scr >= self.largest_module - tolerance * scale
                and scr <= self.undiversified + tolerance * scale)

    def __repr__(self) -> str:
        return (f"MarketRiskPosition(SCR={self.scr:,.2f}, "
                f"interest {self.interest_direction} binds, "
                f"{self.calibration.name})")


def market_risk(*, assets, liabilities, curve: YieldCurve,
                calibration: MarketRiskCalibration | None = None,
                equity: EquityExposure | None = None,
                symmetric: float = 0.0,
                property_value: float = 0.0,
                currency_positions=None,
                spread=None,
                concentration=None,
                asset_timing: str = "end",
                liability_timing: str = "end") -> MarketRiskPosition:
    """The whole market risk module for one balance sheet.

    ``assets`` and ``liabilities`` are the interest-sensitive cashflows;
    ``spread`` is ``(values, durations, credit_quality_step)`` and
    ``concentration`` is ``(exposures, credit_quality_step, calculation
    base)``. A sub-module left out contributes zero, which is what a fund
    holding no property should report.
    """
    calibration = calibration or calibration_for()
    capital, direction, shocks = interest_rate_capital(
        assets, liabilities, curve, calibration,
        asset_timing=asset_timing, liability_timing=liability_timing,
    )
    shocks = {f"interest_{name}": shock for name, shock in shocks.items()}
    modules = {"interest": capital}
    modules["equity"] = (
        equity_capital(equity, calibration, symmetric=symmetric)[0]
        if equity is not None else 0.0
    )
    property_shock = property_capital(property_value, calibration)
    modules["property"] = property_shock.capital
    if property_value:
        shocks["property"] = property_shock
    modules["currency"] = (
        currency_capital(currency_positions, calibration)[0]
        if currency_positions else 0.0
    )
    if spread is not None:
        spread_shock = spread_capital(*spread, calibration=calibration)
        modules["spread"] = spread_shock.capital
        shocks["spread"] = spread_shock
    else:
        modules["spread"] = 0.0
    modules["concentration"] = (
        concentration_capital(*concentration, calibration)[0]
        if concentration is not None else 0.0
    )
    return MarketRiskPosition(modules=modules, interest_direction=direction,
                              calibration=calibration, shocks=shocks)
