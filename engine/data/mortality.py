"""The VPLA mortality basis, made first class and vectorized.

This is the engine's only mortality lookup. ``MortalityTable`` in
``engine/data/assumptions.py`` is a unisex, non-improving *view* over it for
the annual templates, not a second implementation. A direct promotion of
``application/mortality_table.py`` from
jonmaestro10/VPLA — validated there against Society of Actuaries
calculators — with three layers:

1. **Table lookup** — ``q_x`` by integer age and sex, the last tabulated age
   held flat, an optional blend across sexes; optionally **select and
   ultimate**, where the rate for the first ``n`` years since underwriting
   depends on how long ago the life was selected as well as on its age.
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

One addition VPLA has no counterpart for: **select rates**. VPLA values
payout annuities in payment, where every life has long since passed any
select period, so an ultimate table is the whole story. Term assurance is
not priced that way — a life underwritten last year is a materially better
risk than one of the same age underwritten twenty years ago — so the basis
takes an optional select table keyed by ``(sex, duration, age at
selection)``. It is off unless supplied, and a lookup that does not name a
duration reads the ultimate table by exactly the expression it always did,
which is why every existing golden value is untouched.
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


def split_annual(q, sub_period, freq: int, method: str = "udd"):
    """Split an annual ``q`` into one of ``freq`` equal sub-periods of a
    year of age.

    Extracted from :meth:`MortalityBasis.periodic_rate` so that anything
    holding a *stressed* annual rate — a Solvency II mortality stress, say —
    splits it exactly the way the basis does, rather than carrying a second
    copy of the arithmetic that could drift from this one.

    - ``"udd"``: uniform distribution of deaths, so the conditional rate
      rises through the year: ``(q/m) / (1 - (k/m) q)``.
    - ``"constant_force"``: a constant hazard, ``1 - (1 - q)^(1/m)``.

    Both telescope back exactly to the annual survival over a full year.
    """
    if method not in ("udd", "constant_force"):
        raise ValueError(
            f"method must be 'udd' or 'constant_force', got {method!r}"
        )
    if freq < 1:
        raise ValueError(f"freq {freq} must be >= 1")
    if method == "constant_force":
        return 1.0 - (1.0 - q) ** (1.0 / freq)
    return (q / freq) / (1.0 - (np.asarray(sub_period) / freq) * q)


class MortalityBasis:
    """Mortality rates by age, sex and calendar year, with fractional ages.

    ``rates`` maps sex code to ``{age: q_x}``; every sex must span the same
    contiguous age range. ``improvement`` is either ``{sex: {age: rate}}``
    (a constant scale) or ``{sex: {year: {age: rate}}}`` (generational);
    the shape is detected from its nesting, as VPLA does.

    ``select`` is ``{sex: {duration: {age: q}}}`` — the published layout of a
    select-and-ultimate table, one row per **age at selection** and one
    column per year since selection. Durations run ``0 .. n-1`` and set the
    select period; from duration ``n`` onwards ``rates`` applies.
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
        select: Mapping | None = None,
        select_period: int | None = None,
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
        self._build_select(select, select_period)

    def __fingerprint__(self):
        """What defines this basis: its rates and its conventions.

        Deliberately *not* ``vars(self)``. The improvement lookup caches are
        filled on demand, so hashing them would make an assumption set's
        identity depend on which calendar years happened to be asked for.
        """
        identity = {
            "sexes": self.sexes,
            "min_age": self.min_age,
            "max_age": self.max_age,
            "rates": self._q,
            "year_start": self.year_start,
            "calc": self.calc,
            "actual_daycount": self.actual_daycount,
            "blend_male_percent": self.blend_male_percent,
            "blend": self.blend,
            "omega": self.omega,
            "improvement_kind": self.improvement_kind,
            "select_period": self.select_period,
        }
        if self.improvement_kind == "constant":
            identity["improvement"] = self._imp
        elif self.improvement_kind == "generational":
            identity["improvement"] = self._gen_step
            identity["improvement_max_year"] = self.improvement_max_year
        if self.select_period:
            identity["select"] = self._select
            identity["select_min_age"] = self.select_min_age
            identity["select_max_age"] = self.select_max_age
        return identity

    # --- select and ultimate ---------------------------------------------

    def _build_select(self, select, select_period):
        """Stack ``{sex: {duration: {age: q}}}`` into ``(sexes, durations,
        ages)``, keyed by **age at selection**."""
        self.select_period = 0
        self.select_min_age = self.select_max_age = None
        self._select = None
        if not select:
            if select_period:
                raise ValueError(
                    f"select_period={select_period} but no select rates given"
                )
            return
        if _depth(select) != 3:
            raise ValueError(
                "select rates must be keyed by (sex, duration, age); got "
                f"nesting depth {_depth(select)}"
            )
        if sorted(select) != self.sexes:
            raise ValueError(
                f"select rates cover sexes {sorted(select)}, the ultimate "
                f"table covers {self.sexes}"
            )
        durations = sorted(int(d) for d in select[self.sexes[0]])
        if durations != list(range(len(durations))):
            raise ValueError(
                f"select durations must run 0..n-1, got {durations}"
            )
        n = len(durations)
        if select_period is not None and select_period != n:
            raise ValueError(
                f"select_period={select_period} but {n} durations were given"
            )

        first = select[self.sexes[0]][_key(select[self.sexes[0]], 0)]
        ages = sorted(int(a) for a in first)
        lo, hi = ages[0], ages[-1]
        self._select = np.stack(
            [
                np.stack(
                    [
                        _dense(select[s][_key(select[s], d)], lo, hi,
                               f"select[{s}][{d}]")
                        for d in range(n)
                    ]
                )
                for s in self.sexes
            ]
        )
        if np.any((self._select < 0.0) | (self._select > 1.0)):
            raise ValueError("select mortality rates outside [0, 1]")
        self.select_period = n
        self.select_min_age = lo
        self.select_max_age = hi

    def _table_q(self, sex_index, clipped_age, duration):
        """Un-improved tabular ``q``, select where the duration says so.

        With no ``duration`` — the default everywhere — this is exactly the
        expression the class has always evaluated, character for character,
        so an ultimate-only lookup keeps its last bit. That is the whole
        reason the select dimension is threaded as an optional argument
        rather than folded into the age index.
        """
        ultimate = self._q[sex_index, clipped_age - self.min_age]
        if duration is None or not self.select_period:
            return ultimate
        duration = np.asarray(duration, dtype=np.int64)
        if np.any(duration < 0):
            raise ValueError(
                "policy duration cannot be negative: a life cannot be valued "
                "before it was selected"
            )
        in_select = duration < self.select_period
        # Durations past the select period index row 0 and are then thrown
        # away by the `where`; the clip keeps the gather in bounds without
        # inventing a rate, since the ultimate branch is what survives.
        d = np.where(in_select, duration, 0)
        # A selection age outside the tabulated range reads the nearest
        # row. That is deliberate but it is not free: at the bottom of the
        # table the nearest row belongs to an *older* attained age, so the
        # rate can come out heavier than the ultimate one. It is total —
        # never raising on an age a template masks out anyway — and the
        # cure is a select table covering the block's selection ages.
        entry_age = np.clip(
            clipped_age - d, self.select_min_age, self.select_max_age
        )
        selected = self._select[sex_index, d, entry_age - self.select_min_age]
        return np.where(in_select, selected, ultimate)

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

    def q(self, age, sex_index, year, duration=None):
        """Improved ``q_x``; ages above the table are held flat.

        ``duration`` is whole years since the life was selected. It is read
        only when the basis carries a select table, and omitting it reads
        the ultimate table — the behaviour of every caller that predates
        select rates.
        """
        age = np.asarray(age, dtype=np.int64)
        if np.any(age < self.min_age):
            raise KeyError(
                f"age(s) below the mortality table minimum {self.min_age}"
            )
        clipped = np.minimum(age, self.max_age)
        sex_index = np.asarray(sex_index)
        if self.blend_male_percent is None:
            return self._table_q(
                sex_index, clipped, duration
            ) * self.improvement_factor(sex_index, clipped, year)
        male, female = self._sex_index["M"], self._sex_index["F"]
        w = self.blend_male_percent
        q_male = self._table_q(male, clipped, duration)
        q_female = self._table_q(female, clipped, duration)
        if self.blend == "base":
            # VPLA: blend the base rates, improve on the requested sex.
            base = w * q_male + (1.0 - w) * q_female
            return base * self.improvement_factor(sex_index, clipped, year)
        return w * q_male * self.improvement_factor(
            male, clipped, year
        ) + (1.0 - w) * q_female * self.improvement_factor(
            female, clipped, year
        )

    def clip_age(self, ages):
        """Clamp ages into the table's range.

        For indicator-masked lookups only: a template projecting past the end
        of a product phase reaches ages it never actually uses, and clipping
        keeps the lookup in range without inventing a rate that then gets
        multiplied by zero anyway. Everything else should pass a real age and
        let it raise.
        """
        return np.clip(np.asarray(ages, dtype=np.int64), self.min_age, self.max_age)

    def q_at(self, ages, sex=None, year=None, duration=None):
        """``q_x`` by whole age — the annual view of this basis.

        The lookup the annual templates use, and the same one
        ``period_mortality`` uses per age: table rate, held flat above the
        last tabulated age, times the improvement factor for the calendar
        year. With no improvement scale it is the raw table.

        ``sex`` may be omitted when the basis carries only one. ``year``
        defaults to ``year_start``, where improvement is neutral, so a
        caller that does not model calendar time gets the base table.

        ``duration`` is whole years since selection, broadcast against
        ``ages``. Omitted — the default — the lookup is ultimate.
        """
        ages = np.asarray(ages, dtype=np.int64)
        if np.any(ages < self.min_age) or np.any(ages > self.max_age):
            raise KeyError(
                f"age(s) outside mortality table range "
                f"[{self.min_age}, {self.max_age}]"
            )
        if sex is None:
            if len(self.sexes) != 1:
                raise ValueError(
                    f"basis carries sexes {self.sexes}; q_at needs one of them"
                )
            sex_index = np.zeros_like(ages)
        else:
            sex_index = self.sex_indices(sex).reshape(-1)
            if np.ndim(ages) == 0 and sex_index.size == 1:
                # One age, one sex — a spot check rather than a block.
                sex_index = sex_index[0]
            else:
                # Ages arrive per policy, or per (policy, scenario) under the
                # stochastic executor. Align on the leading model-point axis.
                while sex_index.ndim < np.ndim(ages):
                    sex_index = sex_index[..., None]
                sex_index = np.broadcast_to(sex_index, np.shape(ages))
        year = self.year_start if year is None else year
        if duration is not None:
            duration = np.broadcast_to(
                np.asarray(duration, dtype=np.int64), np.shape(ages)
            )
        return self.q(
            ages, sex_index, np.broadcast_to(year, np.shape(ages)), duration
        )

    def periodic_rate(self, ages, sub_period, freq: int, sex=None, year=None,
                      method: str = "udd", duration=None):
        """``q`` over one of ``freq`` equal sub-periods *within* a year of age.

        The dateless counterpart to ``period_mortality``. Where that one
        splits a payment period across the two ages it straddles — which
        needs a date of birth — this splits a single year of age into
        ``freq`` parts, which needs only the age. Products priced by entry
        age rather than valued from a date of birth use this one.

        ``sub_period`` is ``0 .. freq - 1``, counting from the policy
        anniversary. ``duration`` is whole years since selection, and is
        applied to the *annual* rate before it is split — the select basis
        chooses which year of mortality applies, and the split then divides
        that year, so the two are independent.

        - ``"udd"``: uniform distribution of deaths, so deaths accrue evenly
          across the year and the conditional rate rises through it:
          ``(q/m) / (1 - (k/m) q)``. This is the same statement as the first
          term of VPLA's own UDD split, with ``pct_before = k/m`` and
          ``pct_within = 1/m``.
        - ``"constant_force"``: a constant hazard, so every sub-period
          carries the same rate ``1 - (1 - q)^(1/m)``.

        Both telescope back exactly: the product of ``1 - q`` over a full
        year of sub-periods returns the annual survival ``1 - q``, so
        splitting the year cannot change how many policies reach the end of
        it. Unlike the straddling split, neither can exceed 1 — the UDD
        denominator is bounded below by ``1/m``, and at ``q = 1`` the last
        sub-period rate is exactly 1.
        """
        q = self.q_at(ages, sex=sex, year=year, duration=duration)
        return split_annual(q, sub_period, freq, method=method)

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

    def period_mortality(self, dob, valuation, sex, freq: int, n_periods: int,
                         entry=None):
        """Death probability over each of ``n_periods`` payment periods.

        Returns shape ``(n_policies, n_periods)``. Period ``k`` starts
        ``k * 12 / freq`` months after the valuation date — added in one
        step from the valuation date, never accumulated, because month
        addition does not compose.

        ``entry`` is each policy's date of selection, needed only for a
        select basis. Duration is read at the start of each piece the period
        splits into, the same convention the age already follows: the piece
        before the birthday takes the duration at the period start, the
        piece after it takes the duration at the birthday. Anniversaries and
        birthdays need not coincide, and this keeps each rate matched to the
        span it actually covers.
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

        dur_first = dur_second = None
        if entry is not None and self.select_period:
            entered = DateArray.coerce(entry)
            entered = DateArray(entered.year[:, None], entered.month[:, None],
                                entered.day[:, None])
            dur_first = start.whole_years_since(entered)
            dur_second = next_bday.whole_years_since(entered)

        # Both ages are read at the period start's calendar year, as VPLA does.
        year = start.year
        q_first = self.q(np.maximum(first_age, self.min_age), sex_index, year,
                         dur_first)
        q_second = self.q(np.maximum(second_age, self.min_age), sex_index, year,
                          dur_second)

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

    def survival_curve(self, dob, valuation, sex, freq: int, n_periods: int,
                       entry=None):
        """``ₖp_x`` for ``k = 0 .. n_periods - 1``, shape ``(n_policies,
        n_periods)``. Entry 0 is 1 by construction.

        The per-period survival factor is clipped into ``[0, 1]`` before it
        is accumulated. That is the one place this class departs from the
        original on well-formed input — and it is a no-op on well-formed
        input, which is why the bitwise tests still hold.

        It is not a no-op on the input the original actually runs.
        ``period_mortality`` follows VPLA in splitting a period that
        straddles a birthday **additively**, and that sum is not a
        probability: with ``q`` around 0.8 or above it exceeds 1, and the
        accumulated survival goes *negative* and stays there. CPM2014 —
        VPLA's production table — reaches ``q = 1`` at its last age, so any
        valuation that is not on a policy anniversary hits this above age
        115.

        This is hygiene rather than a correction. By the time the split can
        overflow, survival on CPM2014 has already decayed to ~1e-8, and the
        clip moves an annuity factor by ~1e-9 relative at worst; valued on
        an anniversary, ``q = 1`` drives survival to exactly zero and the
        overflow never happens. The rate itself is deliberately left alone,
        so parity on ``period_mortality`` is untouched — what is refused is
        only letting a survival probability leave this method outside
        ``[0, 1]``. See docs/vpla-review.md §6.15.
        """
        q = self.period_mortality(dob, valuation, sex, freq, n_periods, entry)
        survival = np.ones_like(q)
        if n_periods > 1:
            np.cumprod(np.clip(1.0 - q[:, :-1], 0.0, 1.0), axis=1,
                       out=survival[:, 1:])
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


