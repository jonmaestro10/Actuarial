"""VM-22: the reserve for a non-variable annuity, and where the floor goes.

Execution plan §5, item C1, corrected against the text (see RFC-039's
"Corrected against the Valuation Manual" section). The 2026 VM-22 framework
brings non-variable annuities — fixed deferred, fixed indexed, payout and
structured settlement business — under a principle-based reserve built on
the CTE machinery RFC-016 already had.

Quotations below are from the NAIC *Valuation Manual*, 1 January 2026
edition, chapter VM-22, with section numbers.

**The aggregate reserve is a sum, not a maximum.** §3.A: the aggregate
reserve "shall equal the SR … **plus** the DR for contracts that pass the
Single Scenario Test, **plus** the reserve for any contracts valued under
applicable requirements in VM-A, VM-C, VM-M, and VM-V." Those are
*partitions of the book* — each group of contracts is valued one way, and
the groups add. That is the opposite shape from VM-20's three-way maximum
over the same block, and getting it wrong turns a sum of disjoint parts
into a maximum over them. :class:`AggregateReserve` models the partition.

**The cash surrender value floors each scenario, inside the tail.** §4.B.1:
"The scenario reserve for any given scenario shall not be less than the
cash surrender value in aggregate on the valuation date for the group of
contracts modeled in the projection", and only then (§3.F.5.a.iii)
"Calculate the CTE (70) of the aggregate scenario reserves." The floor goes
*under the tail statistic*, not over it — and the difference is not
cosmetic, because ``CTE(max(F, X)) >= max(F, CTE(X))`` always, so flooring
outside understates the reserve. :func:`aggregate_stochastic_reserve`
applies it where the text puts it, and :func:`floor_outside_reserve`
computes the wrong-order figure so the gap can be reported rather than
assumed away.

The finding, sharpened: three orderings, not two
------------------------------------------------
A stochastic reserve is a tail statistic over a **book**; a cash surrender
value belongs to a **contract**. There are three places the floor can go:

- **floor outside** — ``max(sum f, CTE(sum X))``: the natural reading, and
  the one this module shipped before the text was checked;
- **floor inside** — ``CTE(max(sum f, sum X_s))``: what §4.B.1 prescribes;
- **seriatim** — ``sum max(f_i, CTE(X_i))``: what a system that reserves
  contract by contract produces.

Only one inequality holds in general: **floor outside <= prescribed**,
because ``max(F, X)`` dominates both arguments pointwise and a CTE is
monotone. Reading §4.B.1 the natural way therefore *understates* the
reserve, and by up to the whole width of the tail.

**The prescribed figure is not bracketed by the other two, and that is the
finding.** Reserving contract by contract is supposed to be the
conservative thing to do — every actuary's intuition says aggregation can
only help — and against the prescribed ordering it is not. Two contracts
whose scenario reserves are ``[0, 0, 0, 150]`` each, with a surrender value
of 100 each: every contract's own CTE(50) is 75, below its own floor, so
seriatim reserves 100 + 100 = 200. Pooled, the scenario reserves are
``[0, 0, 0, 300]``, floored at the aggregate 200, and CTE(50) of
``[200, 200, 200, 300]`` is **250**. The aggregate reserve exceeds the sum
of the standalone ones by 50, with no diversification anywhere in sight.

The mechanism: seriatim gives each contract its own floor *and* its own
tail, while the prescribed calculation applies the **summed** floor to the
**pooled** scenario reserve, so a tail in which the pool clears the summed
floor is a tail no individual contract had. The old claim that "seriatim is
never smaller" was true of the natural misreading and is false of the text.

The earlier finding survives underneath it: **the floor can eat the
diversification benefit entirely**. On a block whose surrender value binds
in every scenario, all three placements collapse to the same number and
pooling has bought nothing, however uncorrelated the block.
:func:`aggregation_decomposition` splits ``seriatim − floor outside`` into
a floor effect and a diversification effect and reports the prescribed
figure alongside, rather than assuming where it falls.

Known deviations from the text, still open
-----------------------------------------
Two places where this module is **knowingly not what §4 says**. Both are
recorded rather than quietly carried, both push the reserve the same way
(upward, so the error is conservative rather than deficient), and both are
pinned by tests in tests/test_vm22.py so they cannot be forgotten:

1. **The greatest present value is taken per contract, then summed.**
   §3.F.5.a.ii says "Combine the present values for each model segment and
   take the greatest present value **in aggregate** for each scenario" —
   aggregate first, reduce second. This module reduces first (RFC-016's
   ``scenario_reserves`` maximises over dates per contract) and sums after.
   Since ``Σ max ≥ max Σ``, the result is an overstatement. Fixing it needs
   :class:`Contract` to carry the discounted deficiency *path* rather than
   the reduced scenario reserve, which is a real change and is not made
   here.

2. **The greatest present value is floored at zero.** RFC-016 floors it —
   "a *surplus* is not a negative reserve" — and VM-22 explicitly does not:
   §4.B.1.a carries the guidance note "The greatest present value of
   accumulated deficiencies **can be negative**." The floor lives in
   :mod:`engine.report.pbr`, which VM-20 and VM-21 share, so removing it
   there would change two other chapters on the strength of a third's
   text. VM-22 needs its own unfloored path.

Both are the same shape as the floor-placement error this module already
had corrected: reduce-then-aggregate where the text aggregates-then-reduces.
That is worth naming, because it is evidently the mistake this framework
invites.

What is carried, and what is still the actuary's
------------------------------------------------
:class:`VM22Basis` is a dated parameter set, in the manner of
:mod:`engine.report.market_risk`'s 2015/35 and 2026/269 texts. It now
carries the numbers the text states — CTE 70 (§3.D.2) and the 6.0% SERT
cap (§7.C.1) — and refuses to invent the one it does not: §7.C.1 sets the
threshold at "the lesser of 6.0% and the percentage change that would
trigger the company's materiality standard", and a company's materiality
standard is a company's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from engine.report.pbr import CTE_LEVEL, cte, scenario_reserves

#: How a group of contracts is valued. §3.A adds one reserve per group.
METHODS = ("stochastic", "deterministic", "formulaic")

#: §3.F.1's Reserving Categories. Contracts in different categories **may
#: not be aggregated** when determining the SR or DR — with one exception,
#: §3.F.2, which permits payout and accumulation together where the company
#: manages them in an integrated risk-management process and within a
#: single portfolio or portfolios sharing an ALM strategy.
RESERVING_CATEGORIES = ("payout_annuity", "longevity_reinsurance",
                        "accumulation")

#: The only pair §3.F.2 allows to be combined, and only on attestation.
COMBINABLE = frozenset({"payout_annuity", "accumulation"})

#: How a component may be left out. A component absent for any other
#: reason is a missing calculation.
EXCLUSION_BASES = ("ratio_test", "demonstration", "certification")

#: §7.C.1: the ratio must be "less than the lesser of 6.0% and the
#: percentage change that would trigger the company's materiality
#: standard". This is the 6.0%; the other half is the company's.
SERT_CAP = 0.06


class VM22Error(ValueError):
    """A VM-22 figure this module will not report.

    Every case is one where reporting a number would be worse than
    refusing: a reserve with no groups in it, an exclusion with no stated
    basis, a ratio test with no present value of benefits to divide by, a
    group whose contracts disagree about how many scenarios they were run
    on.
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

    ``materiality_standard`` is ``None`` by default and stays that way:
    §7.C.1 caps the SERT threshold at 6.0% *or* the company's own
    materiality standard, whichever is lower, and only the company knows
    the second. Supplying it can only make the test harder to pass.
    """

    label: str
    cte_level: float = CTE_LEVEL
    #: §7.C.1's fixed cap. The effective threshold is the lesser of this
    #: and ``materiality_standard``.
    sert_cap: float = SERT_CAP
    #: The company's own materiality standard, if it has stated one.
    materiality_standard: float | None = None
    text: str = ""

    def __post_init__(self):
        if not 0.0 <= self.cte_level < 1.0:
            raise VM22Error(f"CTE level {self.cte_level} outside [0, 1)")
        for name in ("sert_cap", "materiality_standard"):
            value = getattr(self, name)
            if value is not None and value < 0.0:
                raise VM22Error(f"{name} {value} is negative")

    @property
    def sert_threshold(self) -> float:
        """§7.C.1: the lesser of the fixed cap and the company's standard."""
        if self.materiality_standard is None:
            return self.sert_cap
        return min(self.sert_cap, self.materiality_standard)

    def variant(self, **changes) -> "VM22Basis":
        """This basis with named parameters replaced."""
        from dataclasses import replace

        return replace(self, **changes)

    def __fingerprint__(self):
        return {"label": self.label, "cte_level": self.cte_level,
                "sert_cap": self.sert_cap,
                "materiality_standard": self.materiality_standard,
                "text": self.text}


#: The 2026 framework, carrying the numbers the text states.
VM22_2026 = VM22Basis(
    label="VM-22 (2026)",
    cte_level=CTE_LEVEL,                       # §3.D.2
    sert_cap=SERT_CAP,                         # §7.C.1
    text="NAIC Valuation Manual, 1 January 2026 edition, chapter VM-22. "
         "CTE 70 per §3.D.2; SERT cap 6.0% per §7.C.1, whose effective "
         "threshold is the lesser of that and the company's materiality "
         "standard. Prescribed scenarios (VM-20 Appendix 1.F) and "
         "prescribed assumption sets are not carried here.",
)


# --------------------------------------------------------------------------
# Exclusion
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Exclusion:
    """Why a group of contracts is not carrying a stochastic reserve.

    §7.B gives three routes — the ratio test, a demonstration, and a
    qualified actuary's certification — and the distinction this preserves
    is that a component omitted because a test was passed and one omitted
    because nobody ran it are the same *absence* and completely different
    *reserves*.
    """

    basis: str
    note: str = ""
    ratio: float | None = None
    threshold: float | None = None
    certified_by: str | None = None

    def __post_init__(self):
        if self.basis not in EXCLUSION_BASES:
            raise VM22Error(
                f"an exclusion needs a basis in {list(EXCLUSION_BASES)}, "
                f"got {self.basis!r}"
            )
        if self.basis == "certification" and not self.certified_by:
            raise VM22Error(
                "§7.B.3's certification is a qualified actuary's judgement: "
                "name the actuary who made it, or compute the reserve"
            )
        if self.basis == "ratio_test" and self.ratio is None:
            raise VM22Error(
                "a ratio-test exclusion carries the ratio it computed; an "
                "exclusion nobody can recheck is an assertion"
            )

    def __fingerprint__(self):
        return {"basis": self.basis, "note": self.note, "ratio": self.ratio,
                "threshold": self.threshold,
                "certified_by": self.certified_by}


@dataclass(frozen=True)
class ExclusionTest:
    """The outcome of §7.C's stochastic exclusion ratio test."""

    ratio: float
    threshold: float
    baseline: float
    adverse: float
    pv_benefits: float

    @property
    def passed(self) -> bool:
        """§7.C.1: pass if the ratio is **less than** the threshold."""
        return self.ratio < self.threshold

    def exclusion(self, note: str = "") -> Exclusion:
        """The :class:`Exclusion` this test earns, or a refusal."""
        if not self.passed:
            raise VM22Error(
                f"the exclusion test did not pass: ratio {self.ratio:.6g} is "
                f"not below {self.threshold:.6g}. The stochastic reserve is "
                f"required (§7.A.1.a)."
            )
        return Exclusion(basis="ratio_test", ratio=self.ratio,
                         threshold=self.threshold, note=note)

    def __fingerprint__(self):
        return {"ratio": self.ratio, "threshold": self.threshold,
                "baseline": self.baseline, "adverse": self.adverse,
                "pv_benefits": self.pv_benefits}


def stochastic_exclusion_test(baseline: float, adverse: Sequence[float],
                              pv_benefits: float, *,
                              basis: VM22Basis = VM22_2026,
                              threshold: float | None = None) -> ExclusionTest:
    """§7.C's ratio test: ``(b − a) / c`` against the prescribed threshold.

    - ``a`` (``baseline``) — the adjusted scenario reserve under the
      baseline economic scenario ("scenario 9" of VM-20 Appendix 1.F) with
      no adjustment to future mortality improvement.
    - ``b`` (max of ``adverse``) — the **largest** adjusted scenario
      reserve over the 16 prescribed economic scenarios crossed with the
      three mortality-improvement variants.
    - ``c`` (``pv_benefits``) — "the present value of benefits for the
      policies, adjusted for reinsurance by subtracting ceded benefits",
      from the baseline scenario and discounted on the same path as ``a``.

    ``c`` is a **separate quantity from the baseline reserve**, and that is
    the correction this function exists in its present form to record: an
    earlier version divided by ``a``, which is not what §7.C.1 says and
    which gives a different — generally larger, since a reserve is smaller
    than the benefits it funds — ratio.
    """
    limit = threshold if threshold is not None else basis.sert_threshold
    values = np.asarray(list(adverse), dtype=np.float64)
    if values.size == 0:
        raise VM22Error("the exclusion test needs at least one adverse "
                        "scenario")
    if pv_benefits <= 0.0:
        raise VM22Error(
            "§7.C.1 divides by the present value of benefits, and this one "
            "is not positive; there is no ratio to compute"
        )
    worst = float(values.max())
    return ExclusionTest(ratio=(worst - baseline) / float(pv_benefits),
                         threshold=float(limit), baseline=float(baseline),
                         adverse=worst, pv_benefits=float(pv_benefits))


# --------------------------------------------------------------------------
# Contracts and groups
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
    #: §3.F.1's Reserving Category. ``None`` means unclassified, which
    #: aggregates freely and **is not a VM-22 reserve** — the chapter
    #: requires the classification, and a pool that never declared one has
    #: not been held to §3.F.1 by anything.
    category: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "scenario_reserve",
                           np.asarray(self.scenario_reserve,
                                      dtype=np.float64).ravel())
        if self.scenario_reserve.size == 0:
            raise VM22Error(f"contract {self.id!r} has no scenario reserves")
        if self.category is not None \
                and self.category not in RESERVING_CATEGORIES:
            raise VM22Error(
                f"contract {self.id!r} declares category "
                f"{self.category!r}; §3.F.1 has {list(RESERVING_CATEGORIES)}"
            )

    @classmethod
    def from_cashflows(cls, id: str, net_cashflows, earned_rates, *,
                       starting_assets: float = 0.0,
                       cash_surrender_value: float = 0.0,
                       pimr: float = 0.0) -> "Contract":
        """Build from the projection, via RFC-016's scenario reserves.

        §4.B.1.a: "The starting asset amount, **less the allocated amount
        of PIMR**, plus the greatest present value … of the projected
        accumulated deficiencies". The pre-tax interest maintenance reserve
        is an allocated balance-sheet amount rather than something a
        projection produces, so it is an argument; it defaults to zero,
        which is the right default for a block that has none and the wrong
        one for a block that does.
        """
        return cls(id=id,
                   scenario_reserve=scenario_reserves(
                       net_cashflows, earned_rates, starting_assets)
                   - float(pimr),
                   cash_surrender_value=cash_surrender_value)

    def __fingerprint__(self):
        return {"id": self.id, "scenario_reserve": self.scenario_reserve,
                "cash_surrender_value": self.cash_surrender_value,
                "category": self.category}


def check_aggregable(contracts: Sequence[Contract], *,
                     combined_payout_accumulation: bool = False) -> None:
    """§3.F.1: contracts in different Reserving Categories may not be pooled.

    The refusal that matters most in this module, because it is the only
    one whose absence made the reserve too *small*. Aggregating buys
    diversification, so a system that pools freely across categories
    reports less than the chapter permits — and every other deviation found
    in VM-22 so far erred the safe way.

    ``combined_payout_accumulation`` is §3.F.2's exception, and it is an
    attestation rather than a computation: the company must manage both
    categories in an integrated risk-management process and within a single
    portfolio or portfolios sharing an ALM strategy. This module cannot
    check either, so it takes the caller's word and records that it did.
    Longevity reinsurance is never combinable.
    """
    declared = {c.category for c in contracts if c.category is not None}
    if len(declared) <= 1:
        return
    if declared == COMBINABLE and combined_payout_accumulation:
        return
    raise VM22Error(
        f"§3.F.1 forbids aggregating Reserving Categories {sorted(declared)} "
        f"when determining the SR or DR. Only "
        f"{sorted(COMBINABLE)} may be combined, and only on §3.F.2's "
        f"criteria — pass combined_payout_accumulation=True to attest that "
        f"the company manages both in one integrated risk-management "
        f"process and within a single portfolio or portfolios sharing an "
        f"ALM strategy."
    )


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


def _total_floor(contracts: Sequence[Contract]) -> float:
    return float(sum(c.cash_surrender_value for c in contracts))


def aggregate_stochastic_reserve(contracts: Sequence[Contract], *,
                                 basis: VM22Basis = VM22_2026,
                                 combined_payout_accumulation: bool = False
                                 ) -> float:
    """§4.B.1 then §3.F.5.a.iii: floor each scenario, **then** take CTE 70.

    "The scenario reserve for any given scenario shall not be less than the
    cash surrender value in aggregate on the valuation date" — so the floor
    is applied per scenario, at the aggregate level, before the tail
    statistic sees it.

    §3.F.1's category rule is enforced first: pooling across Reserving
    Categories is what would make this number too small, so it is refused
    rather than reported.
    """
    check_aggregable(
        contracts, combined_payout_accumulation=combined_payout_accumulation)
    scenario = _stack(contracts).sum(axis=0)
    floored = np.maximum(scenario, _total_floor(contracts))
    return cte(floored, basis.cte_level)


def floor_outside_reserve(contracts: Sequence[Contract], *,
                          basis: VM22Basis = VM22_2026) -> float:
    """``max(sum of floors, CTE of the unfloored scenario reserves)``.

    The natural misreading, computed on purpose so the gap to the
    prescribed figure can be reported. Never above
    :func:`aggregate_stochastic_reserve`, because ``max(F, X)`` dominates
    both ``F`` and ``X`` pointwise and a CTE is monotone.
    """
    scenario = _stack(contracts).sum(axis=0)
    return max(_total_floor(contracts), cte(scenario, basis.cte_level))


def seriatim_reserve(contracts: Sequence[Contract], *,
                     basis: VM22Basis = VM22_2026) -> float:
    """Floor, then aggregate: each contract reserved alone and summed.

    Not a VM-22 reserve — the chapter's stochastic reserve is a tail
    statistic over a group — but it is what a system that reserves contract
    by contract produces, and the gap is
    :func:`aggregation_decomposition`.
    """
    return float(sum(max(c.cash_surrender_value,
                         cte(c.scenario_reserve, basis.cte_level))
                     for c in contracts))


# --------------------------------------------------------------------------
# The aggregate reserve: a sum over groups, per §3.A
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReservingGroup:
    """One group of contracts and the reserve it carries.

    §3.A adds one reserve per group: the SR for groups modelling
    stochastically, the DR for groups that passed the Single Scenario Test,
    and the formulaic reserve for groups that passed the exclusion test and
    elected not to model. A group that is not carrying a stochastic reserve
    says why, in ``exclusion``.
    """

    name: str
    method: str
    amount: float
    exclusion: Exclusion | None = None

    def __post_init__(self):
        if self.method not in METHODS:
            raise VM22Error(
                f"{self.method!r} is not a VM-22 method; they are "
                f"{list(METHODS)}"
            )
        if self.method == "stochastic" and self.exclusion is not None:
            raise VM22Error(
                f"group {self.name!r} carries a stochastic reserve and an "
                f"exclusion from one; an exclusion is a statement that the "
                f"number was not needed, not a note attached to one that was"
            )
        if self.method != "stochastic" and self.exclusion is None:
            raise VM22Error(
                f"group {self.name!r} is valued as {self.method!r} rather "
                f"than stochastically, which §7 permits only on a stated "
                f"basis; record the Exclusion or compute the SR"
            )

    def __fingerprint__(self):
        return {"name": self.name, "method": self.method,
                "amount": self.amount, "exclusion": self.exclusion}


class AggregateReserve:
    """§3.A: the SR plus the DR plus the formulaic reserve, over groups.

    A **sum over a partition of the book**, not a maximum over components
    of one block — which is VM-20's shape and was this module's until the
    text was read. Each group of contracts is valued one way and the groups
    add; asking "which component binds" is a VM-20 question and has no
    answer here, so this reports the composition instead.
    """

    def __init__(self, groups: Iterable[ReservingGroup], *,
                 basis: VM22Basis = VM22_2026):
        self.groups = list(groups)
        self.basis = basis
        if not self.groups:
            raise VM22Error(
                "an aggregate reserve with no groups in it is a calculation "
                "that did not happen, not a zero"
            )
        names = [group.name for group in self.groups]
        if len(set(names)) != len(names):
            raise VM22Error(
                f"group names repeat: {sorted(names)}. §3.A adds one reserve "
                f"per group, so two groups with one name is either a double "
                f"count or a lost group."
            )

    @property
    def value(self) -> float:
        return float(sum(group.amount for group in self.groups))

    def by_method(self) -> dict:
        """The §3.A split: how much of the reserve each method contributed."""
        out = {method: 0.0 for method in METHODS}
        for group in self.groups:
            out[group.method] += group.amount
        return out

    @property
    def largest(self) -> str:
        """The group contributing most. Not a binding component — a sum has
        none — but the first thing anybody asks of a composition."""
        return max(self.groups, key=lambda g: g.amount).name

    def to_dict(self) -> dict:
        return {
            "basis": self.basis.label,
            "value": self.value,
            "by_method": self.by_method(),
            "groups": [{"name": g.name, "method": g.method,
                        "amount": g.amount,
                        "excluded": None if g.exclusion is None
                        else g.exclusion.basis}
                       for g in self.groups],
        }

    def __repr__(self) -> str:
        return (f"AggregateReserve({self.value:,.2f}, "
                f"{len(self.groups)} group(s), basis={self.basis.label!r})")

    def __fingerprint__(self):
        return {"basis": self.basis, "groups": list(self.groups)}


def stochastic_group(name: str, contracts: Sequence[Contract], *,
                     basis: VM22Basis = VM22_2026) -> ReservingGroup:
    """A group carrying the SR of §4, floored where §4.B.1 puts the floor."""
    return ReservingGroup(
        name=name, method="stochastic",
        amount=aggregate_stochastic_reserve(contracts, basis=basis),
    )


# --------------------------------------------------------------------------
# Where the floor goes
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AggregationGap:
    """Where the floor goes, and what each placement costs.

    ``floor_outside <= prescribed`` always. ``seriatim`` is **not**
    bracketed with them — it can fall on either side of ``prescribed``, for
    the reason in the module docstring — so it is reported rather than
    assumed to be the conservative end.
    """

    floor_outside: float
    prescribed: float
    seriatim: float
    floor_effect: float
    diversification_effect: float
    floor_binds: bool

    @property
    def gap(self) -> float:
        """``seriatim − floor_outside``, the sum of the two effects."""
        return self.seriatim - self.floor_outside

    @property
    def ordering_cost(self) -> float:
        """What putting the floor inside the tail costs against outside it."""
        return self.prescribed - self.floor_outside

    def __fingerprint__(self):
        return {"floor_outside": self.floor_outside,
                "prescribed": self.prescribed, "seriatim": self.seriatim,
                "floor_effect": self.floor_effect,
                "diversification_effect": self.diversification_effect,
                "floor_binds": self.floor_binds}


def aggregation_decomposition(contracts: Sequence[Contract], *,
                              basis: VM22Basis = VM22_2026) -> AggregationGap:
    """Split ``seriatim − floor_outside``, and place the prescribed figure.

    Writing ``f_i`` for a contract's surrender value, ``C_i`` for its own
    CTE and ``C`` for the CTE of the summed scenario reserves:

    - ``seriatim      = sum max(f_i, C_i)``
    - ``midpoint      = max(sum f_i, sum C_i)`` — floored once, undiversified
    - ``floor_outside = max(sum f_i, C)``

    so the gap is ``(seriatim − midpoint) + (midpoint − floor_outside)``:
    a **floor effect**, because a sum of maxima dominates a maximum of
    sums, and a **diversification effect**, because a CTE is subadditive.
    Both are non-negative and the second is damped by the floor — when the
    floor binds it is exactly zero, and pooling has bought nothing.
    """
    stacked = _stack(contracts)
    floors = np.array([c.cash_surrender_value for c in contracts],
                      dtype=np.float64)
    own = np.array([cte(row, basis.cte_level) for row in stacked],
                   dtype=np.float64)
    scenario = stacked.sum(axis=0)
    pooled = cte(scenario, basis.cte_level)

    total_floor = float(floors.sum())
    seriatim = float(np.maximum(floors, own).sum())
    midpoint = max(total_floor, float(own.sum()))
    outside = max(total_floor, pooled)
    prescribed = cte(np.maximum(scenario, total_floor), basis.cte_level)
    return AggregationGap(
        floor_outside=outside, prescribed=prescribed, seriatim=seriatim,
        floor_effect=seriatim - midpoint,
        diversification_effect=midpoint - outside,
        floor_binds=total_floor >= pooled,
    )
