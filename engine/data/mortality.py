"""The VPLA mortality basis, made first class and vectorized.

``engine/data/assumptions.py`` carries a deliberately minimal annual
``MortalityTable`` for the toy templates. This module is the real basis, a
direct promotion of ``application/mortality_table.py`` from
jonmaestro10/VPLA — validated there against Society of Actuaries
calculators — with three layers:

1. **Table lookup** — ``q_x`` by integer age and sex, the last tabulated age
   held flat, an optional blend across sexes.
2. **Improvement** — a 1-D constant scale
   (``q * (1 - imp[age]) ** (year - year_start)``) or a 2-D generational
   scale (``q * Π_y (1 - imp[y][age])`` over calendar years, the last
   tabulated year held flat).
3. **Fractional age** — the probability of death over one payment period
   beginning on any date, splitting the period across the two ages it
   straddles by day count (actual, or 30/360) and combining them under
   uniform distribution of deaths or linearly.

What changed, and why it does not change a number
-------------------------------------------------
VPLA evaluates layer 3 one ``relativedelta`` call at a time — 120 x freq
calls per policy per annuity factor, each allocating date objects. Here the
same arithmetic runs over ``(policies, periods)`` integer arrays via
engine/core/dates.py, and the improvement scale is pre-accumulated once
into a dense lookup instead of being re-multiplied year by year on every
call. Both are reorganisations of the identical formulas: the multiplication
order within each period is unchanged, so the results agree to the last bit.
tests/test_mortality_basis.py pins that against a literal transcription of
the original, and scripts/vpla_parity.py against the original itself.

Two deliberate departures from the original, both from docs/vpla-review.md:

- ``blend="improved"`` (the default) blends the *improved* rates across
  sexes. VPLA blends the base rates and then applies the improvement scale
  of whichever sex was asked for, which improves a blended rate
  inconsistently (review §6.4). ``blend="base"`` reproduces the original.
  With sex-specific rates — the configuration VPLA actually runs — the two
  are identical.
- An age below the table raises instead of failing on a dict lookup;
  extrapolating mortality downwards is never the right silent default.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from engine.core.dates import DateArray, months_per_period, period_starts

# Attained age at which VPLA treats death as certain.
OMEGA = 120


def _dense(by_age: Mapping, min_age: int, max_age: int, what: str):
    """Contiguous ``min_age..max_age`` vector. Keys may be ints or the
    strings a JSON table brings with it."""
    values = {int(age): value for age, value in by_age.items()}
    if sorted(values) != list(range(min_age, max_age + 1)):
        raise ValueError(f"{what} ages must be contiguous {min_age}-{max_age}")
    return np.array(
        [float(values[age]) for age in range(min_age, max_age + 1)],
        dtype=np.float64,
    )


def _depth(d) -> int:
    if not isinstance(d, Mapping) or not d:
        return 0
    return 1 + max(_depth(v) for v in d.values())


class MortalityBasis:
    """Mortality rates by age, sex and calendar year, with fractional ages.

    ``rates`` maps sex code to ``{age: q_x}``; every sex must span the same
    contiguous age range. ``improvement`` is either ``{sex: {age: rate}}``
    (a constant scale) or ``{sex: {year: {age: rate}}}`` (generational);
    the shape is detected from its nesting, as VPLA does.
    """

    def __init__(
        self,
        rates: Mapping[str, Mapping[int, float]],
        *,
        year_start: int,
        improvement: Mapping | None = None,
        use_improvement: bool = True,
        calc: str = "udd",
        actual_daycount: bool = True,
        blend_male_percent: float | None = None,
        blend: str = "improved",
        omega: int = OMEGA,
    ):
        if not rates:
            raise ValueError("no mortality rates supplied")
        if calc not in ("udd", "linear"):
            raise ValueError(f"calc must be 'udd' or 'linear', got {calc!r}")
        if blend not in ("improved", "base"):
            raise ValueError(f"blend must be 'improved' or 'base', got {blend!r}")
        if blend_male_percent is not None and not 0.0 <= blend_male_percent <= 1.0:
            raise ValueError(f"blend_male_percent {blend_male_percent} outside [0, 1]")

        self.sexes = sorted(rates)
        self._sex_index = {s: i for i, s in enumerate(self.sexes)}
        first = rates[self.sexes[0]]
        ages = sorted(int(a) for a in first)
        self.min_age, self.max_age = ages[0], ages[-1]
        self._q = np.stack(
            [
                _dense(rates[s], self.min_age, self.max_age, f"mortality[{s}]")
                for s in self.sexes
            ]
        )
        if np.any((self._q < 0.0) | (self._q > 1.0)):
            raise ValueError("mortality rates outside [0, 1]")

        self.year_start = int(year_start)
        self.calc = calc
        self.actual_daycount = actual_daycount
        self.blend_male_percent = blend_male_percent
        self.blend = blend
        self.omega = int(omega)
        self.use_improvement = bool(use_improvement) and bool(improvement)
        self._build_improvement(improvement)

    # --- improvement ------------------------------------------------------

    def _build_improvement(self, improvement):
        self.improvement_kind = None
        self._factor_cache: dict[int, np.ndarray] = {}
        self._stack_cache: dict[tuple[int, int], np.ndarray] = {}
        self._ones = np.ones_like(self._q)
        if not self.use_improvement:
            return
        depth = _depth(improvement)
        if depth == 2:
            self.improvement_kind = "constant"
            self._imp = np.stack(
                [
                    _dense(improvement[s], self.min_age, self.max_age,
                           f"improvement[{s}]")
                    for s in self.sexes
                ]
            )
            return
        if depth != 3:
            raise ValueError(
                f"improvement scale must be keyed by (sex, age) or "
                f"(sex, year, age); got nesting depth {depth}"
            )
        self.improvement_kind = "generational"
        years = sorted(int(y) for y in improvement[self.sexes[0]])
        if years[0] > self.year_start + 1:
            raise ValueError(
                f"generational scale starts at {years[0]} but improvement "
                f"runs from {self.year_start + 1}"
            )
        if years != list(range(years[0], years[-1] + 1)):
            raise ValueError("generational scale years must be contiguous")
        self.improvement_max_year = years[-1]
        self._gen_step = np.stack(
            [
                np.stack(
                    [
                        1.0 - _dense(improvement[s][_key(improvement[s], y)],
                                     self.min_age, self.max_age,
                                     f"improvement[{s}][{y}]")
                        for y in range(self.year_start + 1,
                                       self.improvement_max_year + 1)
                    ]
                )
                for s in self.sexes
            ],
            axis=1,
        )
        self._factor_cache[self.year_start] = self._ones
        self._gen_top = self.year_start

    def _factor_table(self, year: int) -> np.ndarray:
        """Improvement factor for every ``(sex, age)`` at one calendar year.

        Built once per calendar year and reused across every policy and
        period — the projection touches ~120 distinct years, where VPLA
        redoes this work on every single lookup.

        The arithmetic is kept identical to the reference, not merely
        equivalent. The constant scale is one ``pow`` per (sex, age), taken
        through NumPy *scalars*: NumPy's vectorized ``power`` loop rounds
        differently from libm's scalar ``pow`` in the last bit, and the
        reference uses the latter. The generational scale is accumulated one
        calendar year at a time in increasing order, as the reference loops,
        rather than being refactored into a cumulative product plus a power
        of the tail — same factors, but multiplication is not associative in
        floating point.
        """
        cached = self._factor_cache.get(year)
        if cached is not None:
            return cached
        if self.improvement_kind == "constant":
            # No clamp at zero: a valuation before `year_start` extrapolates
            # the scale backwards, which is what the original does.
            elapsed = float(year - self.year_start)
            table = np.array(
                [[float(v) ** elapsed for v in row] for row in 1.0 - self._imp],
                dtype=np.float64,
            )
            self._factor_cache[year] = table
            return table
        # The generational scale is *not* symmetric with the constant one:
        # the reference loops over calendar years, so a year at or before
        # `year_start` yields an empty product rather than a reversal.
        if year <= self.year_start:
            return self._ones
        last = self._gen_step.shape[0] - 1
        while self._gen_top < year:
            self._gen_top += 1
            step = self._gen_step[min(self._gen_top - self.year_start - 1, last)]
            self._factor_cache[self._gen_top] = (
                self._factor_cache[self._gen_top - 1] * step
            )
        return self._factor_cache[year]

    def _factor_stack(self, first_year: int, last_year: int) -> np.ndarray:
        """``(years, sexes, ages)`` improvement factors, built once per range.

        Stacking the per-year tables turns the lookup into a single gather
        rather than one masked pass per distinct calendar year — with a
        60-year monthly projection that is the difference between ~120
        sweeps of the whole slab and one.
        """
        key = (first_year, last_year)
        stack = self._stack_cache.get(key)
        if stack is None:
            stack = np.stack(
                [self._factor_table(y) for y in range(first_year, last_year + 1)]
            )
            self._stack_cache[key] = stack
        return stack

    def improvement_factor(self, sex_index, age, year):
        """Cumulative improvement applied to ``q_x`` at a calendar year."""
        sex_index, age, year = np.broadcast_arrays(
            np.asarray(sex_index, dtype=np.int64),
            np.asarray(age, dtype=np.int64),
            np.asarray(year, dtype=np.int64),
        )
        if not self.use_improvement:
            return np.ones(age.shape, dtype=np.float64)
        first = int(year.min())
        stack = self._factor_stack(first, int(year.max()))
        return stack[year - first, sex_index, age - self.min_age]

    # --- table lookup -----------------------------------------------------

    def q(self, age, sex_index, year):
        """Improved ``q_x``; ages above the table are held flat."""
        age = np.asarray(age, dtype=np.int64)
        if np.any(age < self.min_age):
            raise KeyError(
                f"age(s) below the mortality table minimum {self.min_age}"
            )
        clipped = np.minimum(age, self.max_age)
        sex_index = np.asarray(sex_index)
        idx = clipped - self.min_age
        if self.blend_male_percent is None:
            return self._q[sex_index, idx] * self.improvement_factor(
                sex_index, clipped, year
            )
        male, female = self._sex_index["M"], self._sex_index["F"]
        w = self.blend_male_percent
        if self.blend == "base":
            # VPLA: blend the base rates, improve on the requested sex.
            base = w * self._q[male, idx] + (1.0 - w) * self._q[female, idx]
            return base * self.improvement_factor(sex_index, clipped, year)
        return w * self._q[male, idx] * self.improvement_factor(
            male, clipped, year
        ) + (1.0 - w) * self._q[female, idx] * self.improvement_factor(
            female, clipped, year
        )

    def sex_indices(self, sex) -> np.ndarray:
        codes = np.atleast_1d(np.asarray(sex, dtype=object))
        try:
            return np.array(
                [self._sex_index[getattr(s, "value", s)] for s in codes],
                dtype=np.int64,
            )
        except KeyError as exc:
            raise KeyError(f"sex {exc.args[0]!r} not in this basis") from None

    # --- fractional age ---------------------------------------------------

    def period_mortality(self, dob, valuation, sex, freq: int, n_periods: int):
        """Death probability over each of ``n_periods`` payment periods.

        Returns shape ``(n_policies, n_periods)``. Period ``k`` starts
        ``k * 12 / freq`` months after the valuation date — added in one
        step from the valuation date, never accumulated, because month
        addition does not compose.
        """
        step = months_per_period(freq)
        dob = DateArray.coerce(dob)
        valuation = DateArray.coerce(valuation)
        sex_index = self.sex_indices(sex)[:, None]

        start = period_starts(valuation, step, n_periods)
        nxt = start.add_months(step)
        birth = DateArray(dob.year[:, None], dob.month[:, None], dob.day[:, None])

        first_age = start.whole_years_since(birth)
        second_age = nxt.whole_years_since(birth)
        current_bday = birth.add_years(first_age)
        next_bday = current_bday.add_years(1)
        after_next_bday = current_bday.add_years(2)

        days_in_year = next_bday.days_since(current_bday)
        days_in_next_year = after_next_bday.days_since(next_bday)
        start_in_year = start.days_since(current_bday)
        period_length = nxt.days_since(start) / days_in_year

        same_age = first_age == second_age
        start_percent = start_in_year / days_in_year
        percent_first = np.where(
            same_age, period_length, (days_in_year - start_in_year) / days_in_year
        )
        percent_second = np.where(
            same_age, 0.0, nxt.days_since(next_bday) / days_in_next_year
        )
        if not self.actual_daycount:
            start_percent = np.round(start_percent * freq) / freq
            percent_first = np.round(percent_first * freq) / freq
            percent_second = np.round(percent_second * freq) / freq

        # Both ages are read at the period start's calendar year, as VPLA does.
        year = start.year
        q_first = self.q(np.maximum(first_age, self.min_age), sex_index, year)
        q_second = self.q(np.maximum(second_age, self.min_age), sex_index, year)

        if self.calc == "linear":
            result = percent_first * q_first + percent_second * q_second
        else:
            survive_first = 1.0 - q_first * start_percent
            safe = np.where(survive_first == 0.0, 1.0, survive_first)
            result = np.where(
                survive_first == 0.0,
                1.0,
                percent_first / safe * q_first + percent_second * q_second,
            )
        return np.where(first_age >= self.omega, 1.0, result)

    def survival_curve(self, dob, valuation, sex, freq: int, n_periods: int):
        """``ₖp_x`` for ``k = 0 .. n_periods - 1``, shape ``(n_policies,
        n_periods)``. Entry 0 is 1 by construction."""
        q = self.period_mortality(dob, valuation, sex, freq, n_periods)
        survival = np.ones_like(q)
        if n_periods > 1:
            np.cumprod(1.0 - q[:, :-1], axis=1, out=survival[:, 1:])
        return survival

    # --- VPLA file shapes -------------------------------------------------

    @classmethod
    def from_vpla_tables(
        cls,
        mortality: Mapping,
        improvement: Mapping | None = None,
        **kwargs,
    ) -> "MortalityBasis":
        """Build from VPLA's own JSON shapes.

        The base table is ``{age: {"Mortality.M": q, "Mortality.F": q, ...}}``
        as produced by ``convert_csv_table_to_json``; a constant improvement
        scale rides in the same file under ``"Improvement.<sex>"``, while a
        generational scale is ``{sex: {year: {age: rate}}}`` from
        ``convert_csv_2d_improvement_to_json``.
        """
        sexes = sorted(
            key.split(".", 1)[1]
            for key in next(iter(mortality.values()))
            if key.startswith("Mortality.")
        )
        rates = {
            s: {int(a): row[f"Mortality.{s}"] for a, row in mortality.items()}
            for s in sexes
        }
        scale = improvement
        if scale is not None and _depth(scale) == 2:
            first_row = next(iter(scale.values()))
            if any(str(k).startswith("Improvement.") for k in first_row):
                scale = {
                    s: {int(a): row[f"Improvement.{s}"] for a, row in scale.items()}
                    for s in sexes
                }
        return cls(rates, improvement=scale, **kwargs)


def _key(mapping, year):
    """Year keys may be ints or strings depending on the loader."""
    return year if year in mapping else str(year)


