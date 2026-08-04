"""Assumption objects.

Phase 0 keeps these deliberately small: a mortality table keyed by integer
age, flat lapse, flat interest. Versioned/immutable assumption snapshots and
table I/O arrive with the data layer in Phase 1.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


class MortalityTable:
    """Annual mortality rates ``q_x`` keyed by integer age.

    Lookups outside the table raise rather than extrapolate: silently
    invented rates are an accuracy bug, not a convenience.
    """

    def __init__(self, qx: Mapping[int, float]):
        for age, q in qx.items():
            if not 0.0 <= q <= 1.0:
                raise ValueError(f"q_x[{age}] = {q} outside [0, 1]")
        self._qx = MappingProxyType(dict(qx))

    @classmethod
    def flat(cls, q: float, min_age: int = 0, max_age: int = 130) -> "MortalityTable":
        return cls({age: q for age in range(min_age, max_age + 1)})

    def q(self, age: int) -> float:
        try:
            return self._qx[age]
        except KeyError:
            raise KeyError(f"age {age} not in mortality table") from None

    @property
    def ages(self) -> range:
        keys = sorted(self._qx)
        return range(keys[0], keys[-1] + 1)


class Assumptions:
    """A named, read-only bundle of assumptions passed to a model."""

    def __init__(self, *, mortality: MortalityTable, lapse: float = 0.0,
                 interest: float = 0.0, expense_per_policy: float = 0.0):
        if not 0.0 <= lapse < 1.0:
            raise ValueError(f"lapse rate {lapse} outside [0, 1)")
        self.mortality = mortality
        self.lapse = lapse
        self.interest = interest
        self.expense_per_policy = expense_per_policy
