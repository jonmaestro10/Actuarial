"""Assumption objects for the annual templates.

Flat lapse, flat interest, flat crediting, and a mortality table keyed by
integer age. The mortality is no longer a second implementation: it is a
unisex, non-improving view over
:class:`~engine.data.mortality.MortalityBasis`, the VPLA basis promoted in
docs/rfc-002-basis.md and held to bitwise parity against the original.

That matters beyond tidiness. ``Assumptions.mortality`` will equally take a
full ``MortalityBasis``, so the annual templates get sex-distinct rates and
improvement scales without being rewritten — they already look mortality up
through ``q_at``/``clip_age``, which both classes provide.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

import numpy as np

from engine.data.decrements import Decrements
from engine.data.mortality import MortalityBasis

#: Sex code for a table that does not distinguish.
UNISEX = "U"


class MortalityTable:
    """Annual ``q_x`` by contiguous integer age, one set of rates for all.

    A thin view over ``MortalityBasis`` rather than its own lookup, so there
    is exactly one implementation of "read a rate out of a table, hold the
    last age flat" in the engine.

    Lookups outside the table raise rather than extrapolate: silently
    invented rates are an accuracy bug, not a convenience. Templates that
    need masked lookups (ages only reachable outside a product phase) clip
    the age and multiply by an indicator — see the library conventions.
    """

    def __init__(self, qx: Mapping[int, float]):
        if not qx:
            raise ValueError("empty mortality table")
        keys = sorted(qx)
        if keys != list(range(keys[0], keys[-1] + 1)):
            raise ValueError("mortality table ages must be contiguous")
        for age in keys:
            if not 0.0 <= qx[age] <= 1.0:
                raise ValueError(f"q_x[{age}] = {qx[age]} outside [0, 1]")
        self._qx = MappingProxyType(dict(qx))
        self.basis = MortalityBasis(
            {UNISEX: dict(qx)}, year_start=0, use_improvement=False
        )
        self.min_age, self.max_age = self.basis.min_age, self.basis.max_age

    @classmethod
    def flat(cls, q: float, min_age: int = 0, max_age: int = 130) -> "MortalityTable":
        return cls({age: q for age in range(min_age, max_age + 1)})

    def q(self, age: int) -> float:
        try:
            return self._qx[age]
        except KeyError:
            raise KeyError(f"age {age} not in mortality table") from None

    def q_at(self, ages, sex=None, year=None, duration=None):
        """Vectorized lookup: scalar or integer array of ages, all in range.

        ``duration`` is accepted and passed on so that a template written
        for a select basis runs unchanged against a flat table; with no
        select rates behind it the argument cannot move a number.
        """
        return self.basis.q_at(ages, duration=duration)

    def periodic_rate(self, ages, sub_period, freq, sex=None, year=None,
                      method="udd", duration=None):
        """``q`` over one of ``freq`` sub-periods within a year of age."""
        return self.basis.periodic_rate(
            ages, sub_period, freq, method=method, duration=duration
        )

    def clip_age(self, ages):
        """Clamp ages into table range, for indicator-masked lookups only."""
        return self.basis.clip_age(ages)

    @property
    def ages(self) -> range:
        return range(self.min_age, self.max_age + 1)

    def __fingerprint__(self):
        return {"basis": self.basis}


class DynamicLapse:
    """Lapse rate as a function of how well funded a guarantee is.

    Policyholders holding an in-the-money guarantee lapse less than the base
    assumption; those whose account has outgrown its guarantee lapse more.
    The driver is the **funded ratio** — account value over guaranteed
    amount — and the shape is linear in that ratio between a floor and a
    cap:

    ``rate = base * clip(1 + sensitivity * (funded - 1), floor, cap)``

    At ``funded == 1`` the multiplier is exactly 1, so ``base`` is the
    at-the-money rate. ``sensitivity = 0`` reproduces a flat lapse
    assumption **bitwise**, which is what lets a dynamic-lapse template be
    checked against a static one.

    Dividing by the guarantee rather than by the account value is
    deliberate: a GMWB account can be drawn down to exactly zero, and a
    driver that divided by it would need a fudge factor at precisely the
    point where the assumption matters most.

    Dynamic lapse shapes differ by company and by regulator; this is one
    accepted form, not the only one. A product needing a different shape
    overrides its ``lapse_rate`` variable — that is what the escape hatch in
    docs/rfc-001-dsl.md is for.
    """

    def __init__(self, base: float, *, sensitivity: float = 0.0,
                 floor: float = 0.5, cap: float = 1.5):
        if not 0.0 <= base < 1.0:
            raise ValueError(f"base lapse rate {base} outside [0, 1)")
        if sensitivity < 0.0:
            raise ValueError(
                f"sensitivity {sensitivity} must be >= 0: a better funded "
                "guarantee cannot make a policyholder less likely to lapse"
            )
        if not 0.0 <= floor <= 1.0 <= cap:
            raise ValueError(
                f"need floor <= 1 <= cap (got floor={floor}, cap={cap}) so "
                "that the base rate is the at-the-money rate"
            )
        if base * cap >= 1.0:
            raise ValueError(f"base * cap = {base * cap} reaches a 100% lapse rate")
        self.base = base
        self.sensitivity = sensitivity
        self.floor = floor
        self.cap = cap

    def funded_ratio(self, guarantee, account_value):
        """Account value over guaranteed amount; 1 (neutral) with no guarantee."""
        guarantee = np.asarray(guarantee, dtype=np.float64)
        account_value = np.asarray(account_value, dtype=np.float64)
        guaranteed = guarantee > 0.0
        return np.where(
            guaranteed, account_value / np.where(guaranteed, guarantee, 1.0), 1.0
        )

    def multiplier(self, guarantee, account_value):
        funded = self.funded_ratio(guarantee, account_value)
        return np.clip(
            1.0 + self.sensitivity * (funded - 1.0), self.floor, self.cap
        )

    def rate(self, guarantee, account_value):
        return self.base * self.multiplier(guarantee, account_value)

    def __fingerprint__(self):
        return {"base": self.base, "sensitivity": self.sensitivity,
                "floor": self.floor, "cap": self.cap}


class Assumptions:
    """A named, read-only bundle of assumptions passed to a model."""

    def __init__(self, *, mortality: "MortalityTable | MortalityBasis",
                 lapse: float = 0.0,
                 interest: float = 0.0, expense_per_policy: float = 0.0,
                 crediting_rate: float = 0.0, amc: float = 0.0,
                 dynamic_lapse: "DynamicLapse | None" = None,
                 gmdb_fee: float = 0.0, gmab_fee: float = 0.0,
                 gmwb_fee: float = 0.0, base_year: int | None = None,
                 freq: int = 1, fractional_ages: str = "udd",
                 decrements: "Decrements | str | None" = None):
        if freq < 1 or 12 % freq:
            raise ValueError(f"payment frequency {freq} must divide 12")
        if not 0.0 <= lapse < 1.0:
            raise ValueError(f"lapse rate {lapse} outside [0, 1)")
        if not 0.0 <= amc < 1.0:
            raise ValueError(f"AMC {amc} outside [0, 1)")
        for name, fee in (("gmdb_fee", gmdb_fee), ("gmab_fee", gmab_fee),
                          ("gmwb_fee", gmwb_fee)):
            if not 0.0 <= fee < 1.0:
                raise ValueError(f"{name} {fee} outside [0, 1)")
        if dynamic_lapse is not None and lapse not in (0.0, dynamic_lapse.base):
            raise ValueError(
                f"lapse={lapse} conflicts with dynamic_lapse.base="
                f"{dynamic_lapse.base}; set one or the other"
            )
        self.mortality = mortality
        #: Calendar year of projection time zero, for improvement scales.
        #: Defaults to the basis's own base year, where improvement is
        #: neutral — so supplying a plain table changes nothing.
        self.base_year = (
            getattr(mortality, "year_start", 0) if base_year is None else base_year
        )
        # A flat lapse assumption is the zero-sensitivity dynamic one, so
        # templates never branch on which was supplied.
        self.dynamic_lapse = dynamic_lapse or DynamicLapse(lapse)
        self.lapse = self.dynamic_lapse.base
        self.interest = interest
        self.expense_per_policy = expense_per_policy
        self.crediting_rate = crediting_rate
        self.amc = amc
        self.gmdb_fee = gmdb_fee
        self.gmab_fee = gmab_fee
        self.gmwb_fee = gmwb_fee
        #: Payment periods per year. ``t`` counts periods, so at ``freq = 1``
        #: it counts years and every rate below is the annual one unchanged.
        self.freq = freq
        self.fractional_ages = fractional_ages
        #: How competing decrements combine within a period. Defaults to
        #: ``sequential`` — the fixed order the templates have always
        #: applied — so supplying nothing changes nothing, bit for bit.
        if isinstance(decrements, str) or decrements is None:
            decrements = Decrements(decrements or "sequential")
        self.decrements = decrements

    def __fingerprint__(self):
        """Every assumption that can change a projected number."""
        return {
            "mortality": self.mortality,
            "dynamic_lapse": self.dynamic_lapse,
            "interest": self.interest,
            "expense_per_policy": self.expense_per_policy,
            "crediting_rate": self.crediting_rate,
            "amc": self.amc,
            "gmdb_fee": self.gmdb_fee,
            "gmab_fee": self.gmab_fee,
            "gmwb_fee": self.gmwb_fee,
            "base_year": self.base_year,
            "freq": self.freq,
            "fractional_ages": self.fractional_ages,
            "decrements": self.decrements,
        }

    # --- per-period views of annual assumptions ---------------------------
    #
    # Every one of these is an identity at freq = 1, exactly and not merely
    # to tolerance, which is what lets the annual golden suite stand as the
    # proof that making the templates frequency-aware moved nothing.

    def years_elapsed(self, t: int) -> int:
        """Whole years of policy duration completed by period ``t``."""
        return t // self.freq

    def sub_period(self, t: int) -> int:
        """Position of period ``t`` within its policy year, ``0 .. freq-1``."""
        return t % self.freq

    def periods(self, years):
        """Payment periods spanned by a duration given in years."""
        return years * self.freq

    def periodic_q(self, ages, t: int, sex=None, duration=None):
        """Mortality over period ``t`` for a life aged ``ages`` at its start.

        The year of age is split into ``freq`` sub-periods by
        ``fractional_ages`` — see
        :meth:`engine.data.mortality.MortalityBasis.periodic_rate`. At
        ``freq = 1`` under UDD this is the tabular rate, bit for bit.

        ``duration`` is whole years since the life was selected, for a
        select-and-ultimate basis. Against an ultimate-only basis it is
        inert, so a template can pass it unconditionally.
        """
        table = self.mortality
        return table.periodic_rate(
            table.clip_age(ages), self.sub_period(t), self.freq,
            sex=sex, year=self.base_year + self.years_elapsed(t),
            method=self.fractional_ages, duration=duration,
        )

    def periodic_lapse(self):
        """Lapse over one period, from the annual rate under a constant force
        — so ``freq`` periods of it compound back to the annual rate."""
        if self.freq == 1:
            return self.lapse
        return 1.0 - (1.0 - self.lapse) ** (1.0 / self.freq)

    def per_period(self, annual_amount):
        """An annual cashflow spread evenly over the periods of a year."""
        return annual_amount / self.freq

    def discount(self, t: int):
        """Discount factor from the start of period ``t`` back to time 0."""
        return (1.0 + self.interest) ** (-t / self.freq if self.freq != 1 else -t)

    def annual_q(self, ages, sex=None, offset: int = 0, duration=None):
        """``q_x`` at whole ages, ``offset`` years after projection time zero.

        The single mortality lookup the annual templates use. Ages are
        clipped into the table before the read, because a template projecting
        past the end of a product phase reaches ages it masks out anyway.

        Pass a full ``MortalityBasis`` as ``mortality`` and the same call
        picks up sex-distinct rates and an improvement scale; pass a plain
        ``MortalityTable`` and it is the raw table, unchanged.
        """
        table = self.mortality
        return table.q_at(
            table.clip_age(ages), sex=sex, year=self.base_year + offset,
            duration=duration,
        )
