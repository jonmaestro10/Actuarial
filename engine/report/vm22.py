"""VM-22: the reserve for a non-variable annuity, and where it is computed.

Execution plan §5, item C1. The 2026 VM-22 framework brings non-variable
annuities — fixed deferred, fixed indexed, payout and structured settlement
business — under a principle-based reserve of the same shape VM-20 and
VM-21 already have: a stochastic reserve that is a conditional tail
expectation over scenario reserves, a deterministic reserve, a floor, and
exclusion tests that let a company skip the expensive components when it
can show they do not bind.

The machinery for the tail statistic is RFC-016's and is not rebuilt here.
:mod:`engine.report.pbr` already computes the accumulated-deficiency roll,
the greatest present value, the scenario reserves and the CTE, and does so
identically for all three chapters, because they *are* identical — the
chapters differ in scope, assumptions and floors, not in what a CTE is.
What this module adds is the part VM-22 does differently, and one finding
about it.

**The finding: where you aggregate decides the reserve, and by two
separable amounts.** A stochastic reserve is a tail statistic over a *book*,
and a cash-surrender-value floor is a property of a *contract*. Compute the
floor per contract and add up, and you get a different number from adding
up first and flooring once — always a larger one. Layer the CTE's own
subadditivity on top and there are two distinct effects pushing the same
way, which :func:`aggregation_decomposition` separates exactly:

    seriatim − aggregate  =  floor effect  +  diversification effect

Both are non-negative, and the second is the one people talk about. The
sharp edge is that **the floor can eat the diversification benefit
entirely**: on a block where the surrender value binds in aggregate, the
credit for pooling is exactly zero, however uncorrelated the scenarios
are. "Our stochastic reserve fell when we aggregated" is worth nothing
until somebody has checked which component binds — and
:meth:`VM22Reserve.binding` is one attribute.

**What this module will not invent.** The mechanics here are general; the
*numbers* — the exclusion-test threshold, the prescribed scenario set, the
prescribed assumption margins — are the Valuation Manual's, they are dated,
and they are not memorised here. :class:`VM22Basis` carries them as a
named, dated set in the manner of :mod:`engine.report.market_risk`'s
2015/35 and 2026/269 texts, and a ratio test asked to run without a
threshold **raises** rather than defaulting to a plausible one. A reserve
that quietly used a number nobody supplied is precisely the failure a
principle-based framework exists to prevent.

**An excluded component is recorded, never merely absent.** A reserve that
omits its stochastic component because a test was passed and one that omits
it because nobody computed it are different objects here, and only the
first is a valid reserve. :class:`Exclusion` carries the basis — a ratio
test with its numbers, or a certification with a name against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np

from engine.report.pbr import CTE_LEVEL, cte, scenario_reserves

#: The components a VM-22 reserve is the maximum of, in the order a
#: reviewer reads them: the floor, the single-scenario valuation, the tail.
COMPONENTS = ("cash_surrender_value", "deterministic", "stochastic")

#: How a component may be left out. A component absent for any other
#: reason is a missing calculation.
EXCLUSION_BASES = ("ratio_test", "certification")


class VM22Error(ValueError):
    """A VM-22 reserve this module will not report.

    Every case is one where reporting a number would be worse than
    refusing: a reserve with no components in it, an exclusion with no
    stated basis, a ratio test with no threshold, a group whose contracts
    disagree about how many scenarios they were run on.
    """


# --------------------------------------------------------------------------
# The dated basis
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class VM22Basis:
    """A dated VM-22 parameter set.

    Dated rather than constant, for the reason RFC-014's market-risk module
    carries both 2015/35 and 2026/269: a valuation is performed under a
    *text*, texts are amended, and a module that bakes one in silently
    revalues last year's business under this year's rules the moment it is
    upgraded.

    ``cte_level`` is 0.70 — the tail level the principle-based chapters
    share, and the one number here this module is willing to name.
    ``stochastic_exclusion_ratio`` is deliberately ``None``: the threshold
    is prescribed by the Valuation Manual, it is not the same across
    chapters or across amendments, and inventing a default would produce
    exclusion decisions that look computed and are not. Supply it from the
    text you are valuing under.
    """

    label: str
    cte_level: float = CTE_LEVEL
    #: Threshold for :func:`stochastic_exclusion_test`, from the text.
    #: ``None`` means "not supplied", and the test refuses to run.
    stochastic_exclusion_ratio: float | None = None
    #: Free-text provenance: which text, as of when. Carried into the
    #: reserve so a report says what it was computed under.
    text: str = ""

    def __post_init__(self):
        if not 0.0 <= self.cte_level < 1.0:
            raise VM22Error(f"CTE level {self.cte_level} outside [0, 1)")
        ratio = self.stochastic_exclusion_ratio
        if ratio is not None and ratio < 0.0:
            raise VM22Error(f"exclusion ratio {ratio} is negative")

    def variant(self, **changes) -> "VM22Basis":
        """This basis with named parameters replaced.

        The escape hatch a company needs when it values under a text this
        repo does not carry, and the mechanism by which a threshold reaches
        the exclusion test at all.
        """
        from dataclasses import replace

        return replace(self, **changes)

    def __fingerprint__(self):
        return {"label": self.label, "cte_level": self.cte_level,
                "stochastic_exclusion_ratio": self.stochastic_exclusion_ratio,
                "text": self.text}


#: The 2026 framework, carrying only what this module is willing to assert:
#: the tail level. The exclusion threshold is left unset on purpose — see
#: :class:`VM22Basis`.
VM22_2026 = VM22Basis(
    label="VM-22 (2026)",
    cte_level=CTE_LEVEL,
    text="NAIC Valuation Manual chapter 22, operative 1 January 2026; "
         "thresholds and prescribed sets are supplied by the valuation "
         "actuary, not carried here",
)


# --------------------------------------------------------------------------
# Exclusion
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Exclusion:
    """Why a component of the reserve is not there.

    The distinction this exists to preserve: a stochastic reserve omitted
    because a test was passed and one omitted because nobody ran it are
    the same *absence* and completely different *reserves*. Only the first
    is valid, and the difference is not recoverable from a number.
    """

    component: str
    basis: str
    note: str = ""
    #: For a ratio test: what it computed and what it was held to.
    ratio: float | None = None
    threshold: float | None = None
    #: For a certification: who signed it. A certification is a person's
    #: judgement, so an unsigned one is not a certification.
    certified_by: str | None = None

    def __post_init__(self):
        if self.component not in COMPONENTS:
            raise VM22Error(
                f"{self.component!r} is not a VM-22 component; they are "
                f"{list(COMPONENTS)}"
            )
        if self.basis not in EXCLUSION_BASES:
            raise VM22Error(
                f"an exclusion needs a basis in {list(EXCLUSION_BASES)}, "
                f"got {self.basis!r}"
            )
        if self.basis == "certification" and not self.certified_by:
            raise VM22Error(
                "a certification is somebody's judgement: name the actuary "
                "who made it, or compute the component"
            )
        if self.basis == "ratio_test" and self.ratio is None:
            raise VM22Error(
                "a ratio-test exclusion carries the ratio it computed; an "
                "exclusion nobody can recheck is an assertion"
            )

    def __fingerprint__(self):
        return {"component": self.component, "basis": self.basis,
                "note": self.note, "ratio": self.ratio,
                "threshold": self.threshold,
                "certified_by": self.certified_by}


@dataclass(frozen=True)
class ExclusionTest:
    """The outcome of a stochastic exclusion ratio test."""

    ratio: float
    threshold: float
    baseline: float
    adverse: float

    @property
    def passed(self) -> bool:
        """Passed means the stochastic reserve may be omitted."""
        return self.ratio < self.threshold

    def exclusion(self, note: str = "") -> Exclusion:
        """The :class:`Exclusion` this test earns, or a refusal."""
        if not self.passed:
            raise VM22Error(
                f"the exclusion test did not pass: ratio {self.ratio:.6g} is "
                f"not below {self.threshold:.6g}. The stochastic reserve is "
                f"required."
            )
        return Exclusion(component="stochastic", basis="ratio_test",
                         ratio=self.ratio, threshold=self.threshold, note=note)

    def __fingerprint__(self):
        return {"ratio": self.ratio, "threshold": self.threshold,
                "baseline": self.baseline, "adverse": self.adverse}


def stochastic_exclusion_test(baseline: float, adverse: Sequence[float], *,
                              basis: VM22Basis,
                              threshold: float | None = None) -> ExclusionTest:
    """Ratio of the most adverse prescribed scenario to a baseline.

    The shape of every principle-based stochastic exclusion test: value the
    block under a baseline and under a set of prescribed adverse scenarios,
    and if the worst of them exceeds the baseline by less than the
    prescribed proportion, the tail is not where the reserve lives and the
    stochastic reserve may be omitted.

    ``threshold`` comes from the text — from ``basis`` if it carries one,
    or from the caller. There is no default, and that is the point: a
    threshold nobody supplied would make an exclusion decision that looks
    computed and is not.

    The denominator is the baseline, so a baseline of zero has no ratio
    rather than an infinite one; the test refuses instead of dividing.
    """
    limit = threshold if threshold is not None \
        else basis.stochastic_exclusion_ratio
    if limit is None:
        raise VM22Error(
            f"{basis.label} carries no stochastic exclusion threshold and "
            f"none was supplied. It is prescribed by the text being valued "
            f"under; pass threshold=..., or basis.variant("
            f"stochastic_exclusion_ratio=...). This module will not pick one."
        )
    values = np.asarray(list(adverse), dtype=np.float64)
    if values.size == 0:
        raise VM22Error("the exclusion test needs at least one adverse "
                        "scenario")
    if baseline == 0.0:
        raise VM22Error(
            "the exclusion ratio divides by the baseline reserve, and this "
            "one is zero; there is no ratio to compute"
        )
    worst = float(values.max())
    ratio = (worst - baseline) / abs(baseline)
    return ExclusionTest(ratio=ratio, threshold=float(limit),
                         baseline=float(baseline), adverse=worst)


# --------------------------------------------------------------------------
# The reserve
# --------------------------------------------------------------------------

class VM22Reserve:
    """The greatest of the components VM-22 requires, and which one binds.

    Same instinct as :class:`~engine.report.pbr.MinimumReserve` and a
    different component set: a cash-surrender-value floor rather than a net
    premium reserve. Improving a component that does not bind changes
    nothing, so *which* binds is the first question about a block and the
    last one before anybody claims a reserve moved.

    Components omitted under an :class:`Exclusion` are carried, not
    dropped: a reserve that can say "the stochastic component was excluded
    by a ratio test at 3.1%" is a different artifact from one that is
    silent about it.
    """

    def __init__(self, *, cash_surrender_value: float | None = None,
                 deterministic: float | None = None,
                 stochastic: float | None = None,
                 exclusions: Iterable[Exclusion] = (),
                 basis: VM22Basis = VM22_2026):
        supplied = {"cash_surrender_value": cash_surrender_value,
                    "deterministic": deterministic,
                    "stochastic": stochastic}
        self.basis = basis
        self.components = {name: float(value)
                           for name, value in supplied.items()
                           if value is not None}
        self.exclusions = {entry.component: entry for entry in exclusions}

        both = set(self.components) & set(self.exclusions)
        if both:
            raise VM22Error(
                f"component(s) {sorted(both)} are both computed and excluded; "
                f"an exclusion is a statement that the number was not needed, "
                f"not a note attached to one that was"
            )
        if not self.components:
            raise VM22Error(
                "every component is missing; a reserve with nothing in it is "
                "a calculation that did not happen, not a zero"
            )
        unaccounted = [name for name in COMPONENTS
                       if name not in self.components
                       and name not in self.exclusions]
        if unaccounted:
            raise VM22Error(
                f"component(s) {unaccounted} were neither computed nor "
                f"excluded. Omitting one is a decision with a basis; record "
                f"it as an Exclusion or compute it."
            )

    @property
    def value(self) -> float:
        return max(self.components.values())

    @property
    def binding(self) -> str:
        """The component that sets the reserve.

        Ties resolve to :data:`COMPONENTS` order, so a floor that exactly
        equals the stochastic reserve is reported as the floor — the
        conservative reading, and a stable one, since a tie broken by
        dictionary order would move with the input.
        """
        best = self.value
        for name in COMPONENTS:
            if name in self.components and self.components[name] == best:
                return name
        raise VM22Error("no binding component")  # pragma: no cover

    def headroom(self) -> dict:
        """How far each computed component sits below the binding one."""
        return {name: self.value - value
                for name, value in self.components.items()}

    def to_dict(self) -> dict:
        return {
            "basis": self.basis.label,
            "value": self.value,
            "binding": self.binding,
            "components": dict(self.components),
            "excluded": {name: entry.basis
                         for name, entry in self.exclusions.items()},
        }

    def __repr__(self) -> str:
        return (f"VM22Reserve({self.value:,.2f}, binding={self.binding!r}, "
                f"basis={self.basis.label!r})")

    def __fingerprint__(self):
        return {"basis": self.basis, "components": dict(self.components),
                "exclusions": [self.exclusions[k]
                               for k in sorted(self.exclusions)]}


# --------------------------------------------------------------------------
# Where the aggregation happens
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Contract:
    """One contract's contribution to a VM-22 valuation.

    ``scenario_reserve`` is one value per scenario — normally
    :func:`~engine.report.pbr.scenario_reserves` for this contract — and
    every contract in a group must carry the same number of them, in the
    same scenario order, or the sum across contracts is a sum across
    different futures.
    """

    id: str
    scenario_reserve: np.ndarray
    cash_surrender_value: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "scenario_reserve",
                           np.asarray(self.scenario_reserve,
                                      dtype=np.float64).ravel())
        if self.scenario_reserve.size == 0:
            raise VM22Error(f"contract {self.id!r} has no scenario reserves")

    @classmethod
    def from_cashflows(cls, id: str, net_cashflows, earned_rates, *,
                       starting_assets: float = 0.0,
                       cash_surrender_value: float = 0.0) -> "Contract":
        """Build from the projection, via RFC-016's scenario reserves."""
        return cls(id=id,
                   scenario_reserve=scenario_reserves(
                       net_cashflows, earned_rates, starting_assets),
                   cash_surrender_value=cash_surrender_value)

    def __fingerprint__(self):
        return {"id": self.id, "scenario_reserve": self.scenario_reserve,
                "cash_surrender_value": self.cash_surrender_value}


def _stack(contracts: Sequence[Contract]) -> np.ndarray:
    if not contracts:
        raise VM22Error("a VM-22 group needs at least one contract")
    widths = {c.scenario_reserve.size for c in contracts}
    if len(widths) != 1:
        raise VM22Error(
            f"contracts disagree on scenario count: {sorted(widths)}. Adding "
            f"them would be adding across different futures."
        )
    return np.stack([c.scenario_reserve for c in contracts])


def aggregate_stochastic_reserve(contracts: Sequence[Contract], *,
                                 basis: VM22Basis = VM22_2026) -> float:
    """CTE of the summed scenario reserves — the tail of the *book*.

    Summing first is what makes the CTE do its work: a scenario that is bad
    for one contract and good for another is not in the tail of the sum.
    """
    return cte(_stack(contracts).sum(axis=0), basis.cte_level)


def seriatim_stochastic_reserve(contracts: Sequence[Contract], *,
                                basis: VM22Basis = VM22_2026) -> float:
    """Sum of each contract's own CTE — the tail of each *contract*.

    Never smaller than :func:`aggregate_stochastic_reserve`, because a CTE
    is subadditive. Computed here so the difference can be reported rather
    than assumed.
    """
    return float(sum(cte(c.scenario_reserve, basis.cte_level)
                     for c in contracts))


def aggregate_reserve(contracts: Sequence[Contract], *,
                      basis: VM22Basis = VM22_2026,
                      deterministic: float | None = None,
                      exclusions: Iterable[Exclusion] = ()) -> VM22Reserve:
    """Aggregate, then floor: the reserve for the book as a book."""
    return VM22Reserve(
        cash_surrender_value=float(sum(c.cash_surrender_value
                                       for c in contracts)),
        deterministic=deterministic,
        stochastic=aggregate_stochastic_reserve(contracts, basis=basis),
        exclusions=exclusions, basis=basis,
    )


def seriatim_reserve(contracts: Sequence[Contract], *,
                     basis: VM22Basis = VM22_2026) -> float:
    """Floor, then aggregate: each contract reserved on its own and summed.

    The comparator. It is not a VM-22 reserve — the chapter's stochastic
    reserve is a tail statistic over a group — but it is what a system that
    reserves contract by contract produces, and the gap between the two is
    :func:`aggregation_decomposition`.
    """
    return float(sum(max(c.cash_surrender_value,
                         cte(c.scenario_reserve, basis.cte_level))
                     for c in contracts))


@dataclass(frozen=True)
class AggregationGap:
    """Why reserving contract by contract costs more, split in two.

    ``gap == floor_effect + diversification_effect``, exactly, and both
    terms are non-negative. The decomposition is the useful part: the
    second term is the one everybody expects, and the first is the one that
    survives when scenarios are perfectly correlated and there is no
    diversification to be had at all.
    """

    aggregate: float
    seriatim: float
    floor_effect: float
    diversification_effect: float
    #: ``True`` when the floor binds on the aggregate reserve — in which
    #: case the diversification effect is damped or gone entirely.
    floor_binds: bool

    @property
    def gap(self) -> float:
        return self.seriatim - self.aggregate

    def __fingerprint__(self):
        return {"aggregate": self.aggregate, "seriatim": self.seriatim,
                "floor_effect": self.floor_effect,
                "diversification_effect": self.diversification_effect,
                "floor_binds": self.floor_binds}


def aggregation_decomposition(contracts: Sequence[Contract], *,
                              basis: VM22Basis = VM22_2026) -> AggregationGap:
    """Split ``seriatim − aggregate`` into its floor and tail halves.

    Writing ``f_i`` for a contract's surrender value and ``C_i`` for its own
    CTE, with ``C`` the CTE of the sum:

    - ``seriatim   = Σ max(f_i, C_i)``
    - ``midpoint   = max(Σ f_i, Σ C_i)``  — floored once, but undiversified
    - ``aggregate  = max(Σ f_i, C)``

    so ``seriatim − aggregate = (seriatim − midpoint) + (midpoint −
    aggregate)``. The first bracket is the **floor effect**: taking the
    maximum per contract and adding up can only exceed adding up and taking
    the maximum once. The second is the **diversification effect**: ``C ≤
    Σ C_i`` because a CTE is subadditive — and it is *damped by the floor*,
    because both sides are maxima against the same ``Σ f_i``. When the
    floor binds in aggregate the second bracket is exactly zero and pooling
    has bought nothing.
    """
    stacked = _stack(contracts)
    floors = np.array([c.cash_surrender_value for c in contracts],
                      dtype=np.float64)
    own = np.array([cte(row, basis.cte_level) for row in stacked],
                   dtype=np.float64)
    pooled = cte(stacked.sum(axis=0), basis.cte_level)

    total_floor = float(floors.sum())
    seriatim = float(np.maximum(floors, own).sum())
    midpoint = max(total_floor, float(own.sum()))
    aggregate = max(total_floor, pooled)
    return AggregationGap(
        aggregate=aggregate, seriatim=seriatim,
        floor_effect=seriatim - midpoint,
        diversification_effect=midpoint - aggregate,
        floor_binds=total_floor >= pooled,
    )
