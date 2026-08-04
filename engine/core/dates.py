"""Vectorized calendar arithmetic matching ``dateutil.relativedelta``.

The VPLA basis (engine/data/mortality.py) splits every payment period across
the two ages it straddles by day count, so its numbers depend on the exact
calendar: month-end clamping, leap years, and how many whole years have
elapsed since a date. VPLA gets that from ``dateutil.relativedelta``, one
call per period per policy. This module reproduces the same three
operations over whole arrays, so a projection costs a handful of integer
kernels instead of ``120 * freq`` Python objects per policy.

Fast is only worth having if it is the same number. The three operations
below are pinned against ``dateutil`` itself over randomized date grids
including every month end and every leap-year boundary — see
tests/test_dates.py.

Semantics being reproduced, exactly:

- ``relativedelta(months=n)`` — move the year/month, then clamp the day to
  the last day of the target month (31 Jan + 1 month = 28/29 Feb).
- ``relativedelta(years=n)`` — the same with ``12n`` months, so 29 Feb + 1
  year is 28 Feb but 29 Feb + 4 years is 29 Feb again.
- ``relativedelta(later, earlier).years`` — whole elapsed years. Note this
  is *not* the naive "(month, day) tuple comparison" rule: dateutil counts
  whole months and then divides, so a 29 Feb birth date has its anniversary
  on 28 Feb in common years.

Month addition does not compose: ``31 Jan + 1 month + 1 month`` is 28 Mar
but ``31 Jan + 2 months`` is 31 Mar. Callers must therefore add the whole
offset in one step, exactly as VPLA does.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Sequence

import numpy as np

_DAYS_IN_MONTH = np.array(
    [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], dtype=np.int64
)


def is_leap(year):
    """Proleptic Gregorian leap years, vectorized."""
    year = np.asarray(year, dtype=np.int64)
    return ((year % 4 == 0) & (year % 100 != 0)) | (year % 400 == 0)


def days_in_month(year, month):
    """Length of the given month, vectorized."""
    year = np.asarray(year, dtype=np.int64)
    month = np.asarray(month, dtype=np.int64)
    base = _DAYS_IN_MONTH[month]
    return base + ((month == 2) & is_leap(year))


def to_ordinal(year, month, day):
    """Day number on a fixed epoch, vectorized.

    Only differences are ever used, so the epoch is arbitrary; this is the
    standard Julian Day Number, which is branch-free over the Gregorian
    calendar.
    """
    year = np.asarray(year, dtype=np.int64)
    month = np.asarray(month, dtype=np.int64)
    day = np.asarray(day, dtype=np.int64)
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


class DateArray:
    """An array of calendar dates as year/month/day integer arrays.

    Deliberately not ``datetime64``: the operations that matter here are
    month and year arithmetic with day clamping, which calendar-aware
    integer fields express directly and ``datetime64`` (which is uniform in
    days) does not.
    """

    __slots__ = ("year", "month", "day")

    def __init__(self, year, month, day):
        self.year = np.asarray(year, dtype=np.int64)
        self.month = np.asarray(month, dtype=np.int64)
        self.day = np.asarray(day, dtype=np.int64)
        if not (self.year.shape == self.month.shape == self.day.shape):
            raise ValueError("year, month and day must share a shape")

    @classmethod
    def from_dates(cls, dates: Iterable[date]) -> "DateArray":
        rows = [(d.year, d.month, d.day) for d in dates]
        if not rows:
            raise ValueError("no dates supplied")
        arr = np.array(rows, dtype=np.int64)
        return cls(arr[:, 0], arr[:, 1], arr[:, 2])

    @classmethod
    def from_date(cls, d: date) -> "DateArray":
        return cls(np.int64(d.year), np.int64(d.month), np.int64(d.day))

    @classmethod
    def coerce(cls, value) -> "DateArray":
        """Accept a ``DateArray``, a single ``date``, or any sequence of them
        — including the object arrays a model point batch produces."""
        if isinstance(value, cls):
            return value
        if isinstance(value, date):
            return cls.from_dates([value])
        return cls.from_dates(list(value))

    @property
    def shape(self):
        return self.year.shape

    def reshape(self, *shape) -> "DateArray":
        return DateArray(
            self.year.reshape(*shape),
            self.month.reshape(*shape),
            self.day.reshape(*shape),
        )

    def add_months(self, n) -> "DateArray":
        """``relativedelta(months=n)``: shift, then clamp the day."""
        n = np.asarray(n, dtype=np.int64)
        total = self.year * 12 + (self.month - 1) + n
        year = total // 12
        month = total % 12 + 1
        day = np.minimum(np.broadcast_to(self.day, total.shape),
                         days_in_month(year, month))
        return DateArray(year, month, day)

    def add_years(self, n) -> "DateArray":
        """``relativedelta(years=n)``, which is ``12n`` months."""
        return self.add_months(np.asarray(n, dtype=np.int64) * 12)

    @property
    def ordinal(self):
        return to_ordinal(self.year, self.month, self.day)

    def days_since(self, other: "DateArray"):
        """``(self - other).days``."""
        return self.ordinal - other.ordinal

    def whole_years_since(self, other: "DateArray"):
        """``relativedelta(self, other).years`` for ``self >= other``.

        dateutil counts whole *months* — raw month difference, decremented
        when landing on that month would overshoot — and then divides by 12.
        Doing it in that order is what makes 28 Feb an anniversary for a
        29 Feb birth date.
        """
        raw = (self.year - other.year) * 12 + (self.month - other.month)
        # The day dateutil would land on after `raw` months.
        landed = np.minimum(other.day, days_in_month(self.year, self.month))
        months = raw - (landed > self.day)
        return months // 12

    def to_dates(self) -> list[date]:
        flat = [
            date(int(y), int(m), int(d))
            for y, m, d in zip(
                np.ravel(self.year), np.ravel(self.month), np.ravel(self.day)
            )
        ]
        return flat


def period_starts(valuation: DateArray, months_step: int, n_periods: int) -> DateArray:
    """Start dates of ``n_periods`` payment periods, shape ``(..., n_periods)``.

    Each offset is added to the valuation date in **one** step, never
    accumulated, because month addition does not compose.
    """
    offsets = np.arange(n_periods, dtype=np.int64) * months_step
    base = DateArray(
        valuation.year[..., None], valuation.month[..., None], valuation.day[..., None]
    )
    return base.add_months(offsets)


def months_per_period(freq: int) -> int:
    if freq <= 0 or 12 % freq:
        raise ValueError(f"payment frequency {freq} must divide 12")
    return 12 // freq
