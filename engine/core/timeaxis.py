"""Projection time structure.

Phases 0-2 assumed annual steps: ``t`` was a year, ages were
``age_at_entry + t``, and discounting was ``(1 + i) ** -t``. The VPLA basis
promoted in RFC-002 works at any payment frequency and on real calendar
dates, so the projection loop has to as well — a monthly VPLA pays 1,440
times over its horizon, and each period straddles two ages by day count.

A ``TimeAxis`` is the time structure alone: how many periods, how long each
one is, when each starts for each policy, and what age each policy has
attained. It carries no mortality and no discounting; those come from
:class:`engine.data.basis.ValuationBasis`, which the axis is combined with
in a template's ``setup()``.

Each policy carries its own valuation date, so a block valued on one date
and a block of policies valued on their own anniversaries are the same
object with different inputs.

Period ``k`` starts ``k * 12 / freq`` months after the valuation date, added
in **one** step — never accumulated — because month addition does not
compose (see engine/core/dates.py).
"""

from __future__ import annotations

import numpy as np

from engine.core.dates import DateArray, months_per_period, period_starts


class TimeAxis:
    """``n_periods`` payment periods of ``12 / freq`` months per policy."""

    def __init__(self, freq: int, n_periods: int, valuation):
        if n_periods < 1:
            raise ValueError("n_periods must be >= 1")
        self.freq = freq
        self.months_step = months_per_period(freq)
        self.n_periods = int(n_periods)
        self.valuation = DateArray.coerce(valuation)
        self.n_policies = int(np.prod(self.valuation.shape)) or 1
        self.starts = period_starts(self.valuation, self.months_step, self.n_periods)

    @classmethod
    def annual(cls, n_periods: int, valuation) -> "TimeAxis":
        return cls(1, n_periods, valuation)

    @classmethod
    def monthly(cls, n_periods: int, valuation) -> "TimeAxis":
        return cls(12, n_periods, valuation)

    @classmethod
    def for_years(cls, years: int, freq: int, valuation) -> "TimeAxis":
        """An axis covering ``years`` years of payments at ``freq`` a year."""
        return cls(freq, years * freq, valuation)

    @property
    def proj_len(self) -> int:
        """``proj_len`` for a model over this axis: ``t`` runs 0 .. proj_len
        inclusive, so that is one less than the number of periods."""
        return self.n_periods - 1

    @property
    def year_fraction(self) -> float:
        """Length of one period in years."""
        return 1.0 / self.freq

    def period_start(self, t: int) -> DateArray:
        """Start date of period ``t``, per policy."""
        self._check(t)
        return DateArray(
            self.starts.year[..., t], self.starts.month[..., t],
            self.starts.day[..., t],
        )

    def calendar_year(self, t: int):
        """Calendar year each policy's period ``t`` starts in."""
        self._check(t)
        return self.starts.year[..., t]

    def attained_age(self, dob, t: int):
        """Whole years elapsed since ``dob`` at the start of period ``t`` —
        the same count the mortality basis uses to pick a table row."""
        return self.period_start(t).whole_years_since(DateArray.coerce(dob))

    def elapsed_years(self, t: int) -> float:
        """Years from the valuation date to the start of period ``t``."""
        self._check(t)
        return t / self.freq

    def _check(self, t: int) -> None:
        if not 0 <= t < self.n_periods:
            raise IndexError(
                f"period {t} outside axis [0, {self.n_periods})"
            )

    def __repr__(self) -> str:
        return (
            f"TimeAxis(freq={self.freq}, n_periods={self.n_periods}, "
            f"n_policies={self.n_policies})"
        )

