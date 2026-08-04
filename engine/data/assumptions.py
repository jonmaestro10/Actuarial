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


class Assumptions:
    """A named, read-only bundle of assumptions passed to a model."""

    def __init__(self, *, mortality: MortalityTable, lapse: float = 0.0,
                 interest: float = 0.0, expense_per_policy: float = 0.0,
                 crediting_rate: float = 0.0):
        if not 0.0 <= lapse < 1.0:
            raise ValueError(f"lapse rate {lapse} outside [0, 1)")
        self.mortality = mortality
        self.lapse = lapse
        self.interest = interest
        self.expense_per_policy = expense_per_policy
        self.crediting_rate = crediting_rate
