"""Assumption objects.

Phase 0 keeps these deliberately small: a mortality table keyed by integer
age, flat lapse, flat interest, flat crediting. Versioned/immutable
assumption snapshots and table I/O arrive with the data layer in Phase 1.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

import numpy as np


class MortalityTable:
    """Annual mortality rates ``q_x`` keyed by contiguous integer ages.

    Lookups outside the table raise rather than extrapolate: silently
    invented rates are an accuracy bug, not a convenience. Templates that
    need masked lookups (ages only reachable outside a product phase) clip
    the age and multiply by an indicator — see the library conventions.
    """

    def __init__(self, qx: Mapping[int, float]):
        if not qx:
            raise ValueError("empty mortality table")
        keys = sorted(qx)
        self.min_age, self.max_age = keys[0], keys[-1]
        if keys != list(range(self.min_age, self.max_age + 1)):
            raise ValueError("mortality table ages must be contiguous")
        for age in keys:
            if not 0.0 <= qx[age] <= 1.0:
                raise ValueError(f"q_x[{age}] = {qx[age]} outside [0, 1]")
        self._qx = MappingProxyType(dict(qx))
        self._dense = np.array([qx[age] for age in keys], dtype=np.float64)

    @classmethod
    def flat(cls, q: float, min_age: int = 0, max_age: int = 130) -> "MortalityTable":
        return cls({age: q for age in range(min_age, max_age + 1)})

    def q(self, age: int) -> float:
        try:
            return self._qx[age]
        except KeyError:
            raise KeyError(f"age {age} not in mortality table") from None

    def q_at(self, ages):
        """Vectorized lookup: scalar or integer array of ages, all in range."""
        idx = np.asarray(ages)
        if np.any(idx < self.min_age) or np.any(idx > self.max_age):
            raise KeyError(
                f"age(s) outside mortality table range "
                f"[{self.min_age}, {self.max_age}]"
            )
        return self._dense[idx - self.min_age]

    def clip_age(self, ages):
        """Clamp ages into table range, for indicator-masked lookups only."""
        return np.clip(ages, self.min_age, self.max_age)

    @property
    def ages(self) -> range:
        return range(self.min_age, self.max_age + 1)


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


class Assumptions:
    """A named, read-only bundle of assumptions passed to a model."""

    def __init__(self, *, mortality: MortalityTable, lapse: float = 0.0,
                 interest: float = 0.0, expense_per_policy: float = 0.0,
                 crediting_rate: float = 0.0, amc: float = 0.0,
                 dynamic_lapse: "DynamicLapse | None" = None,
                 gmdb_fee: float = 0.0, gmab_fee: float = 0.0,
                 gmwb_fee: float = 0.0):
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
