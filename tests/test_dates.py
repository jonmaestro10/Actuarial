"""The vectorized calendar against ``dateutil.relativedelta`` itself.

Everything in the VPLA basis that depends on the calendar — which two ages
a payment period straddles, how many days each part is, whether a birthday
falls inside it — comes out of three ``relativedelta`` operations.
engine/core/dates.py reimplements those over arrays, so they are checked
here against the library they replace, not against a restatement of it.

Coverage is deliberately adversarial: every month end, both sides of every
leap-year rule (1900 and 2000 disagree), 29 February birth dates, and a few
thousand random dates spanning two centuries.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from dateutil.relativedelta import relativedelta

from engine.core.dates import (
    DateArray,
    days_in_month,
    months_per_period,
    period_starts,
)

EDGE_YEARS = [1900, 1996, 1999, 2000, 2001, 2004, 2020, 2023, 2024, 2100]


def _edge_dates():
    out = []
    for year in EDGE_YEARS:
        for month in range(1, 13):
            for day in (1, 2, 15, 27, 28, 29, 30, 31):
                try:
                    out.append(date(year, month, day))
                except ValueError:
                    pass
    return out


def _random_dates(n=2000, seed=20240804):
    rng = np.random.default_rng(seed)
    base = date(1900, 1, 1)
    return [base + timedelta(days=int(d)) for d in rng.integers(0, 73000, n)]


ALL_DATES = _edge_dates() + _random_dates()
ARRAY = DateArray.from_dates(ALL_DATES)


def _as_dates(arr: DateArray):
    return [
        date(int(y), int(m), int(d))
        for y, m, d in zip(arr.year, arr.month, arr.day)
    ]


@pytest.mark.parametrize("n", [-240, -13, -1, 0, 1, 2, 3, 6, 11, 12, 13, 60, 1440])
def test_add_months_matches_relativedelta(n):
    got = _as_dates(ARRAY.add_months(n))
    want = [d + relativedelta(months=n) for d in ALL_DATES]
    assert got == want


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 20, 65, 100, 120])
def test_add_years_matches_relativedelta(n):
    got = _as_dates(ARRAY.add_years(n))
    want = [d + relativedelta(years=n) for d in ALL_DATES]
    assert got == want


def test_whole_years_matches_relativedelta():
    """The rule is *not* "compare (month, day) tuples": dateutil counts
    whole months and then divides, which is why 28 February is an
    anniversary for a 29 February birth date in a common year."""
    rng = np.random.default_rng(7)
    left = [ALL_DATES[i] for i in rng.integers(0, len(ALL_DATES), 6000)]
    right = [ALL_DATES[i] for i in rng.integers(0, len(ALL_DATES), 6000)]
    later = DateArray.from_dates([max(a, b) for a, b in zip(left, right)])
    earlier = DateArray.from_dates([min(a, b) for a, b in zip(left, right)])
    got = later.whole_years_since(earlier)
    want = np.array(
        [
            relativedelta(max(a, b), min(a, b)).years
            for a, b in zip(left, right)
        ]
    )
    assert np.array_equal(got, want)


def test_leap_day_anniversary_rule():
    born = DateArray.from_dates([date(2000, 2, 29)] * 4)
    on = DateArray.from_dates(
        [date(2021, 2, 28), date(2021, 3, 1), date(2024, 2, 28), date(2024, 2, 29)]
    )
    assert list(on.whole_years_since(born)) == [21, 21, 23, 24]


def test_days_since_matches_timedelta():
    rng = np.random.default_rng(3)
    left = [ALL_DATES[i] for i in rng.integers(0, len(ALL_DATES), 4000)]
    right = [ALL_DATES[i] for i in rng.integers(0, len(ALL_DATES), 4000)]
    got = DateArray.from_dates(left).days_since(DateArray.from_dates(right))
    want = np.array([(a - b).days for a, b in zip(left, right)])
    assert np.array_equal(got, want)


def test_days_in_month_covers_leap_rules():
    assert int(days_in_month(1900, 2)) == 28  # divisible by 100, not 400
    assert int(days_in_month(2000, 2)) == 29  # divisible by 400
    assert int(days_in_month(2024, 2)) == 29
    assert int(days_in_month(2023, 2)) == 28
    assert list(days_in_month([2024] * 12, range(1, 13))) == [
        31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
    ]


@pytest.mark.parametrize("freq", [1, 2, 3, 4, 6, 12])
def test_period_starts_add_the_whole_offset_each_time(freq):
    """Month addition does not compose — 31 Jan + 1 month + 1 month is
    28 March, but + 2 months is 31 March. VPLA adds the whole offset from
    the valuation date every period, so this must too, or every month-end
    valuation drifts."""
    step = months_per_period(freq)
    valuation = DateArray.from_dates([date(2021, 1, 31), date(2020, 2, 29)])
    n = 8 * freq
    starts = period_starts(valuation, step, n)
    for i, base in enumerate([date(2021, 1, 31), date(2020, 2, 29)]):
        want = [base + relativedelta(months=k * step) for k in range(n)]
        got = [
            date(int(starts.year[i, k]), int(starts.month[i, k]),
                 int(starts.day[i, k]))
            for k in range(n)
        ]
        assert got == want


def test_accumulating_months_would_drift():
    # Guards the guard: if month addition *did* compose, the test above
    # would be vacuous.
    stepwise = date(2021, 1, 31)
    for _ in range(2):
        stepwise += relativedelta(months=1)
    assert stepwise != date(2021, 1, 31) + relativedelta(months=2)


def test_bad_frequency_rejected():
    for freq in (0, -1, 5, 7, 8, 24):
        with pytest.raises(ValueError, match="divide 12"):
            months_per_period(freq)
