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

Two reserves, not one
---------------------
§3.B: "All components in the aggregate reserve shall be determined
**post-reinsurance ceded and pre-reinsurance ceded** as outlined in Section
5." Every amount this module reports is therefore a :class:`BasisPair`, and
the pair is not a number and an adjustment. §5.A.2.a determines the
post-ceded DR/SR "reflecting the effects of reinsurance treaties …
including, where appropriate, all projected reinsurance premiums or other
costs and all reinsurance recoveries"; §5.A.2.b determines the pre-ceded
ones "ignoring the effects of reinsurance ceded within the projections".
Those are **two projections**, and nothing here derives one from the other.

The formulaic component is the exception, and there the text *does*
subtract: §5.A.1, "for the reserve amount valued using requirements in
VM-A, VM-C, VM-M, and VM-V, the post-reinsurance ceded reserve is
determined by subtracting the reinsurance reserve credit" — §5.A.3 adding
that the methodology "produces reserves on a pre-reinsurance ceded basis".
:meth:`ReservingGroup.formulaic` is that subtraction and the only place in
this module where one basis is computed from the other.

Two things about §5 that no first-principles design would have contained:

- **§5.A.2.a.iv is an additive charge, not a netting.** Where a treaty does
  not qualify for credit for reinsurance and treating it as if it did
  "would result in a reduction to the company's surplus, then the company
  shall increase the aggregate reserve by the absolute value of such
  reductions in surplus". :class:`AggregateReserve` takes it as
  ``non_qualifying_surplus_reduction`` and **adds** it.
- **§5.A.3 lets the two bases disagree about the method.** "It is possible
  that the pre-reinsurance-ceded reserves would pass the relevant exclusion
  test … while the post-reinsurance-ceded reserves might not, or vice
  versa." So a group can be stochastic on one basis and formulaic on the
  other, and :class:`ReservingGroup` carries a method per basis rather than
  one method and two numbers.

What §5 leaves to the actuary, and this module does not invent: the
**starting assets on the ceded portion** (§5.A.2.b.i–ii). The text gives
acceptable approaches — assets similar to those supporting the retained
portion, scaling up each retained asset, or modelling an identifiable
portfolio where a funds-withheld, modified-coinsurance or trust arrangement
has one — and choosing among them is a modelling decision. It is an input
to the pre-ceded projection, so it arrives here already made. Likewise
§5.A.2.a.iii's counterparty-default margin, which is required *only* where
"the company has knowledge that a counterparty is financially impaired" and
explicitly not otherwise; charging one always would be the natural instinct
and is not what the text says.

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

from engine.report.pbr import (
    CTE_LEVEL,
    accumulated_surplus,
    cte,
    path_discount_factors,
    scenario_reserves,
)

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

#: §3.B's two bases, on both of which every component must be determined.
#: The held reserve is the post-reinsurance-ceded one; the
#: pre-reinsurance-ceded one is required alongside it, not instead of it.
REINSURANCE_BASES = ("pre_ceded", "post_ceded")

#: §4.B.1's second floor: for the longevity reinsurance category, the
#: scenario reserve "shall not be less than 2% of the scheduled longevity
#: benefits payable by the benefit provider within the next 12 months".
LONGEVITY_FLOOR_RATE = 0.02

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
                       pimr: float = 0.0,
                       floor_at_zero: bool = False,
                       category: str | None = None) -> "Contract":
        """Build from the projection, via RFC-016's scenario reserves.

        §4.B.1.a: "The starting asset amount, **less the allocated amount
        of PIMR**, plus the greatest present value … of the projected
        accumulated deficiencies". The pre-tax interest maintenance reserve
        is an allocated balance-sheet amount rather than something a
        projection produces, so it is an argument; it defaults to zero,
        which is the right default for a block that has none and the wrong
        one for a block that does.

        ``floor_at_zero`` defaults to **False** here and to ``True`` in
        :mod:`engine.report.pbr`, and that is the one place VM-22 and the
        other principle-based chapters part company: §4.B.1.a's guidance
        note says "The greatest present value of accumulated deficiencies
        **can be negative**", so a scenario that never goes underwater
        reserves *less* than its starting assets rather than exactly them.
        """
        return cls(id=id,
                   scenario_reserve=scenario_reserves(
                       net_cashflows, earned_rates, starting_assets,
                       floor_at_zero=floor_at_zero)
                   - float(pimr),
                   cash_surrender_value=cash_surrender_value,
                   category=category)

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

    **This is the overstating order.** :class:`Contract` carries a scenario
    reserve whose greatest present value has already been taken, so summing
    contracts computes ``Σ max`` where §3.F.5.a.ii asks for ``max Σ``. Use
    :func:`segment_stochastic_reserve` over :class:`ModelSegment` for the
    prescribed figure; this remains for the single-segment case, where the
    two orders coincide, and for callers written before segments existed.
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
# Model segments — the unit §3.F.4-5 actually aggregates
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSegment:
    """A model segment: a deficiency **path**, not a reduced reserve.

    §3.F.4 lets a company treat a group of contracts within a Reserving
    Category as "a single model segment", and §3.F.5.a says what to do when
    there is more than one: project the accumulated deficiencies and take
    their present value *for each segment*, then "**Combine the present
    values** for each model segment and **take the greatest present value
    in aggregate** for each scenario."

    That order is why this class exists. :class:`Contract` carries a
    scenario reserve with the greatest present value **already taken**, so
    summing contracts computes ``Σ max`` where the chapter asks for
    ``max Σ`` — and since a sum of maxima dominates a maximum of sums, it
    overstates. Getting the order right needs the ``(t, scenario)`` path,
    which is impossible to hold per contract on a real book and entirely
    reasonable per segment, because a segment is already a pooled block.

    That the memory objection dissolves the moment the module adopts the
    chapter's own vocabulary is the argument for this abstraction, and it
    is the same lesson as every other finding in VM-22: the structure the
    text describes is load-bearing.

    ``deficiency_path`` is the discounted accumulated deficiency, one row
    per projection date and one column per scenario — negative where the
    segment is in surplus, which §4.B.1.a's guidance note requires.
    """

    name: str
    deficiency_path: np.ndarray
    starting_assets: float = 0.0
    pimr: float = 0.0
    cash_surrender_value: float = 0.0
    category: str | None = None
    #: §4.B.1: for the longevity reinsurance category, the scenario reserve
    #: may not fall below 2% of the scheduled longevity benefits payable
    #: within the next twelve months. Zero elsewhere and unused.
    longevity_benefits_12m: float = 0.0
    #: §3.F.3: a segment carrying a DR may not be aggregated with one that
    #: does not.
    carries_dr: bool = False

    def __post_init__(self):
        object.__setattr__(self, "deficiency_path",
                           np.atleast_2d(np.asarray(self.deficiency_path,
                                                    dtype=np.float64)))
        if self.deficiency_path.size == 0:
            raise VM22Error(f"segment {self.name!r} has an empty path")
        if self.category is not None \
                and self.category not in RESERVING_CATEGORIES:
            raise VM22Error(
                f"segment {self.name!r} declares category "
                f"{self.category!r}; §3.F.1 has {list(RESERVING_CATEGORIES)}"
            )
        if self.longevity_benefits_12m and \
                self.category != "longevity_reinsurance":
            raise VM22Error(
                f"segment {self.name!r} carries a twelve-month longevity "
                f"benefit but is category {self.category!r}; §4.B.1's 2% "
                f"floor belongs to 'longevity_reinsurance'"
            )

    @property
    def n_scenarios(self) -> int:
        return int(self.deficiency_path.shape[1])

    def floor(self) -> float:
        """§4.B.1's floor for this segment, whichever applies.

        The cash surrender value in general; for longevity reinsurance the
        **greater** of that and 2% of the scheduled benefits payable within
        the next twelve months. Which one applies is decided by the
        Reserving Category, which is what makes the category a calculation
        input rather than a label.
        """
        floor = float(self.cash_surrender_value)
        if self.category == "longevity_reinsurance":
            floor = max(floor, LONGEVITY_FLOOR_RATE
                        * float(self.longevity_benefits_12m))
        return floor

    @classmethod
    def from_cashflows(cls, name: str, net_cashflows, earned_rates, *,
                       starting_assets: float = 0.0, **options
                       ) -> "ModelSegment":
        """Build the discounted deficiency path from a projection.

        The same roll RFC-016 performs, stopped one step earlier: the
        greatest present value is *not* taken here, because §3.F.5.a.ii
        takes it after the segments are combined.
        """
        surplus = accumulated_surplus(net_cashflows, earned_rates,
                                      starting_assets)
        path = -surplus * path_discount_factors(earned_rates)
        return cls(name=name, deficiency_path=path,
                   starting_assets=starting_assets, **options)

    def __fingerprint__(self):
        return {"name": self.name, "deficiency_path": self.deficiency_path,
                "starting_assets": self.starting_assets, "pimr": self.pimr,
                "cash_surrender_value": self.cash_surrender_value,
                "category": self.category,
                "longevity_benefits_12m": self.longevity_benefits_12m,
                "carries_dr": self.carries_dr}


def segment_scenario_reserves(segments: Sequence[ModelSegment], *,
                              combined_payout_accumulation: bool = False
                              ) -> np.ndarray:
    """§3.F.5.a: combine, **then** take the greatest present value.

    One value per scenario:

        sum of starting assets
        + greatest present value of the **aggregated** deficiencies
        − aggregate PIMR,   floored at the aggregate §4.B.1 floor.

    The order is the whole point. Taking the greatest per segment and
    adding after gives ``Σ max``, which dominates and therefore overstates.
    """
    if not segments:
        raise VM22Error("a VM-22 reserve needs at least one model segment")
    widths = {s.n_scenarios for s in segments}
    if len(widths) != 1:
        raise VM22Error(
            f"segments disagree on scenario count: {sorted(widths)}. Adding "
            f"them would be adding across different futures."
        )
    lengths = {s.deficiency_path.shape[0] for s in segments}
    if len(lengths) != 1:
        raise VM22Error(
            f"segments disagree on projection length: {sorted(lengths)}"
        )
    check_segments_aggregable(
        segments,
        combined_payout_accumulation=combined_payout_accumulation)

    combined = np.zeros_like(segments[0].deficiency_path)
    for segment in segments:
        combined = combined + segment.deficiency_path
    greatest = combined.max(axis=0)          # unfloored, per §4.B.1.a's note
    assets = float(sum(s.starting_assets for s in segments))
    pimr = float(sum(s.pimr for s in segments))
    floor = float(sum(s.floor() for s in segments))
    return np.maximum(assets + greatest - pimr, floor)


def check_segments_aggregable(segments: Sequence[ModelSegment], *,
                              combined_payout_accumulation: bool = False
                              ) -> None:
    """§3.F.1 and §3.F.3, over segments rather than contracts.

    §3.F.3 is the one :class:`Contract` could not express: "groups of
    contracts for which the company calculates a DR … shall not be
    aggregated with any groups of contracts that do not calculate a DR."
    """
    declared = {s.category for s in segments if s.category is not None}
    if len(declared) > 1 and not (declared == COMBINABLE
                                  and combined_payout_accumulation):
        raise VM22Error(
            f"§3.F.1 forbids aggregating Reserving Categories "
            f"{sorted(declared)} when determining the SR or DR. Only "
            f"{sorted(COMBINABLE)} may be combined, and only on §3.F.2's "
            f"criteria — pass combined_payout_accumulation=True to attest "
            f"them."
        )
    dr = {s.carries_dr for s in segments}
    if len(dr) > 1:
        raise VM22Error(
            "§3.F.3: segments for which the company calculates a DR shall "
            "not be aggregated with segments that do not. Reserve them "
            "separately and add the results, per §3.A."
        )


def segment_stochastic_reserve(segments: Sequence[ModelSegment], *,
                               basis: VM22Basis = VM22_2026,
                               combined_payout_accumulation: bool = False
                               ) -> float:
    """§3.F.5.a.iii: CTE 70 of the aggregate scenario reserves.

    The prescribed path end to end, and the one to use. Its
    :class:`Contract` counterpart, :func:`aggregate_stochastic_reserve`,
    reduces before it aggregates and therefore overstates — see that
    function's own docstring.
    """
    return cte(segment_scenario_reserves(
        segments,
        combined_payout_accumulation=combined_payout_accumulation),
        basis.cte_level)


# --------------------------------------------------------------------------
# The aggregate reserve: a sum over groups, per §3.A
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BasisPair:
    """One VM-22 amount on both of §3.B's bases.

    §3.B: "All components in the aggregate reserve shall be determined
    post-reinsurance ceded and pre-reinsurance ceded as outlined in Section
    5." For the SR and the DR those are **two projections** — §5.A.2.a
    reflects the treaties, §5.A.2.b ignores them — so this is a pair of
    computed numbers and not a number with an adjustment hanging off it.
    Nothing here derives one basis from the other; the only place in this
    module that does is :meth:`ReservingGroup.formulaic`, because §5.A.1
    says to.

    :meth:`flat` is the case where the two coincide, which is a statement
    about the block rather than a default: a group with no reinsurance
    ceded genuinely has one number, and a group with reinsurance genuinely
    has two.
    """

    pre_ceded: float
    post_ceded: float

    def __post_init__(self):
        for name in REINSURANCE_BASES:
            object.__setattr__(self, name, float(getattr(self, name)))

    @classmethod
    def flat(cls, amount: float) -> "BasisPair":
        """One number on both bases — a group with no reinsurance ceded."""
        return cls(pre_ceded=float(amount), post_ceded=float(amount))

    @property
    def ceded_credit(self) -> float:
        """``pre_ceded − post_ceded``: what ceding is worth on this group.

        Positive where reinsurance reduces the reserve, which is the usual
        direction and not a guaranteed one — a treaty can cost more than it
        recovers, which is the whole premise of §5.A.2.a.iv. Nothing here
        constrains the sign.
        """
        return self.pre_ceded - self.post_ceded

    @property
    def collapsed(self) -> bool:
        """Whether the two bases agree, as they do with no treaty."""
        return self.pre_ceded == self.post_ceded

    def __add__(self, other) -> "BasisPair":
        if isinstance(other, BasisPair):
            return BasisPair(self.pre_ceded + other.pre_ceded,
                             self.post_ceded + other.post_ceded)
        return BasisPair(self.pre_ceded + float(other),
                         self.post_ceded + float(other))

    __radd__ = __add__

    def to_dict(self) -> dict:
        return {"pre_ceded": self.pre_ceded, "post_ceded": self.post_ceded}

    def __fingerprint__(self):
        return self.to_dict()


def _as_pair(amount) -> BasisPair:
    """A :class:`BasisPair`, or a bare number read as a treaty-free block."""
    return amount if isinstance(amount, BasisPair) else BasisPair.flat(amount)


def _check_method(name: str, basis: str, method: str,
                  exclusion: "Exclusion | None") -> None:
    """One basis's method and its stated reason, checked together."""
    if method not in METHODS:
        raise VM22Error(
            f"{method!r} is not a VM-22 method; they are {list(METHODS)}"
        )
    if method == "stochastic" and exclusion is not None:
        raise VM22Error(
            f"group {name!r} carries a stochastic reserve and an exclusion "
            f"from one on the {basis} basis; an exclusion is a statement "
            f"that the number was not needed, not a note attached to one "
            f"that was"
        )
    if method != "stochastic" and exclusion is None:
        raise VM22Error(
            f"group {name!r} is valued as {method!r} rather than "
            f"stochastically on the {basis} basis, which §7 permits only on "
            f"a stated basis; record the Exclusion or compute the SR"
        )


@dataclass(frozen=True)
class ReservingGroup:
    """One group of contracts and the reserves it carries, on both bases.

    §3.A adds one reserve per group: the SR for groups modelling
    stochastically, the DR for groups that passed the Single Scenario Test,
    and the formulaic reserve for groups that passed the exclusion test and
    elected not to model. A group that is not carrying a stochastic reserve
    says why, in ``exclusion``.

    ``amount`` is a :class:`BasisPair`; a bare number is read as
    :meth:`BasisPair.flat`, which says the group cedes nothing and both of
    §3.B's bases give the same reserve.

    ``method`` and ``exclusion`` describe the **post-reinsurance-ceded**
    valuation — the one that goes on the balance sheet — and describe the
    pre-ceded one too unless ``pre_ceded_method`` is given. That override
    exists because §5.A.3 says the bases may part company: "it is possible
    that the pre-reinsurance-ceded reserves would pass the relevant
    exclusion test … while the post-reinsurance-ceded reserves might not,
    or vice versa". A group can therefore be formulaic pre-ceded and
    stochastic post-ceded, which is two valuations of one group rather than
    two numbers from one. Where ``pre_ceded_method`` is given,
    ``pre_ceded_exclusion`` stands alone and is not inherited — a different
    method needs its own stated reason, or none.
    """

    name: str
    method: str
    amount: BasisPair
    exclusion: Exclusion | None = None
    #: §5.A.3's override: the method on the pre-ceded basis, where it
    #: differs. ``None`` means the same valuation on both bases.
    pre_ceded_method: str | None = None
    pre_ceded_exclusion: Exclusion | None = None

    def __post_init__(self):
        object.__setattr__(self, "amount", _as_pair(self.amount))
        _check_method(self.name, "post-ceded", self.method, self.exclusion)
        if self.pre_ceded_method is None:
            if self.pre_ceded_exclusion is not None:
                raise VM22Error(
                    f"group {self.name!r} states a pre-ceded exclusion but "
                    f"no pre-ceded method; §5.A.3's split is a difference of "
                    f"valuation, so name the method it applies to"
                )
        else:
            _check_method(self.name, "pre-ceded", self.pre_ceded_method,
                          self.pre_ceded_exclusion)

    @property
    def methods(self) -> dict:
        """How this group is valued on each of §3.B's two bases."""
        return {"post_ceded": self.method,
                "pre_ceded": self.pre_ceded_method or self.method}

    @property
    def exclusions(self) -> dict:
        """Why the SR was left out on each basis, where it was."""
        if self.pre_ceded_method is None:
            return {"post_ceded": self.exclusion, "pre_ceded": self.exclusion}
        return {"post_ceded": self.exclusion,
                "pre_ceded": self.pre_ceded_exclusion}

    @classmethod
    def formulaic(cls, name: str, pre_ceded_amount: float, *,
                  exclusion: Exclusion,
                  reinsurance_reserve_credit: float = 0.0
                  ) -> "ReservingGroup":
        """§5.A.1: post-ceded = pre-ceded − the reinsurance reserve credit.

        The one component where the two bases are a number and an
        adjustment rather than two projections. §5.A.3: the VM-A/C/M/V
        methodology "produces reserves on a pre-reinsurance ceded basis.
        Therefore, the reserve must be adjusted for any reinsurance ceded
        accordingly" — so the stated amount is the pre-ceded one and the
        credit is subtracted from it, not added to anything.

        A credit larger than the reserve it is taken against is refused
        rather than reported as a negative reserve: a reserve credit cannot
        exceed the reserve ceded, so an amount that does is a data error,
        and reporting it would net a liability into an asset silently.
        """
        credit = float(reinsurance_reserve_credit)
        gross = float(pre_ceded_amount)
        if credit < 0.0:
            raise VM22Error(
                f"group {name!r} states a reinsurance reserve credit of "
                f"{credit:,.2f}; a credit is what ceding is worth and is not "
                f"negative. If ceding costs more than it recovers, that is "
                f"§5.A.2.a.iv's surplus charge on the aggregate reserve."
            )
        if credit > gross:
            raise VM22Error(
                f"group {name!r} takes a reinsurance reserve credit of "
                f"{credit:,.2f} against a pre-ceded reserve of {gross:,.2f}. "
                f"A credit cannot exceed the reserve ceded, and §5.A.1's "
                f"subtraction would report a negative reserve."
            )
        return cls(name=name, method="formulaic",
                   amount=BasisPair(pre_ceded=gross,
                                    post_ceded=gross - credit),
                   exclusion=exclusion)

    def __fingerprint__(self):
        return {"name": self.name, "method": self.method,
                "amount": self.amount, "exclusion": self.exclusion,
                "pre_ceded_method": self.pre_ceded_method,
                "pre_ceded_exclusion": self.pre_ceded_exclusion}


class AggregateReserve:
    """§3.A: the SR plus the DR plus the formulaic reserve, over groups.

    A **sum over a partition of the book**, not a maximum over components
    of one block — which is VM-20's shape and was this module's until the
    text was read. Each group of contracts is valued one way and the groups
    add; asking "which component binds" is a VM-20 question and has no
    answer here, so this reports the composition instead.
    """

    def __init__(self, groups: Iterable[ReservingGroup], *,
                 basis: VM22Basis = VM22_2026,
                 non_qualifying_surplus_reduction: float = 0.0):
        self.groups = list(groups)
        self.basis = basis
        self.non_qualifying_surplus_reduction = float(
            non_qualifying_surplus_reduction)
        if self.non_qualifying_surplus_reduction < 0.0:
            raise VM22Error(
                f"§5.A.2.a.iv increases the aggregate reserve by the "
                f"**absolute value** of the surplus reduction; "
                f"{self.non_qualifying_surplus_reduction:,.2f} is negative "
                f"and would relieve the reserve rather than charge it"
            )
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
    def group_total(self) -> BasisPair:
        """§3.A's sum over the groups, before §5.A.2.a.iv's charge."""
        return sum((group.amount for group in self.groups),
                   BasisPair(0.0, 0.0))

    @property
    def value(self) -> BasisPair:
        """§3.A's sum on both of §3.B's bases, plus §5.A.2.a.iv's charge.

        The charge lands on the **post-ceded** basis only. §5.A.2.a.iv
        arises because a non-qualifying treaty's cash flows are kept out of
        the post-ceded projections, and it puts back the surplus that
        omission flatters; the pre-ceded basis ignores reinsurance ceded
        altogether by construction (§5.A.2.b), so charging it there would
        be adding a reinsurance effect to the basis defined not to have
        one. The text says only "increase the aggregate reserve" and does
        not say on which basis, so this is a reading, and it is recorded
        here rather than left for someone to infer from the arithmetic.
        """
        total = self.group_total
        return BasisPair(
            pre_ceded=total.pre_ceded,
            post_ceded=total.post_ceded + self.non_qualifying_surplus_reduction,
        )

    @property
    def post_ceded(self) -> float:
        """The held reserve: §3.A's sum on §3.B's post-ceded basis."""
        return self.value.post_ceded

    @property
    def pre_ceded(self) -> float:
        """The same reserve ignoring reinsurance ceded, per §5.A.2.b."""
        return self.value.pre_ceded

    def by_method(self) -> dict:
        """The §3.A split, per basis, as :class:`BasisPair` per method.

        A group may be valued one way pre-ceded and another post-ceded
        (§5.A.3), so the two bases are split independently — which is why
        this cannot be a dict of floats keyed by one method per group.

        These sum to :attr:`group_total`, **not** to :attr:`value`:
        §5.A.2.a.iv's charge is not any group's reserve and has no method
        to be attributed to.
        """
        out = {method: BasisPair(0.0, 0.0) for method in METHODS}
        for group in self.groups:
            methods = group.methods
            out[methods["pre_ceded"]] = (out[methods["pre_ceded"]]
                                         + BasisPair(group.amount.pre_ceded,
                                                     0.0))
            out[methods["post_ceded"]] = (out[methods["post_ceded"]]
                                          + BasisPair(0.0,
                                                      group.amount.post_ceded))
        return out

    @property
    def largest(self) -> str:
        """The group contributing most **post-ceded**. Not a binding
        component — a sum has none — but the first thing anybody asks of a
        composition."""
        return max(self.groups, key=lambda g: g.amount.post_ceded).name

    def to_dict(self) -> dict:
        return {
            "basis": self.basis.label,
            "value": self.value.to_dict(),
            "group_total": self.group_total.to_dict(),
            "non_qualifying_surplus_reduction":
                self.non_qualifying_surplus_reduction,
            "by_method": {method: pair.to_dict()
                          for method, pair in self.by_method().items()},
            "groups": [{"name": g.name, "methods": g.methods,
                        "amount": g.amount.to_dict(),
                        "ceded_credit": g.amount.ceded_credit,
                        "excluded": {basis: None if e is None else e.basis
                                     for basis, e in g.exclusions.items()}}
                       for g in self.groups],
        }

    def __repr__(self) -> str:
        return (f"AggregateReserve({self.post_ceded:,.2f} post-ceded, "
                f"{self.pre_ceded:,.2f} pre-ceded, "
                f"{len(self.groups)} group(s), basis={self.basis.label!r})")

    def __fingerprint__(self):
        return {"basis": self.basis, "groups": list(self.groups),
                "non_qualifying_surplus_reduction":
                    self.non_qualifying_surplus_reduction}


def stochastic_group(name: str, contracts: Sequence[Contract], *,
                     pre_ceded_contracts: Sequence[Contract] | None = None,
                     basis: VM22Basis = VM22_2026,
                     combined_payout_accumulation: bool = False
                     ) -> ReservingGroup:
    """A group carrying the SR of §4, floored where §4.B.1 puts the floor.

    ``contracts`` is the projection that reflects the treaties — §5.A.2.a's
    post-reinsurance-ceded basis, and for a block that cedes nothing simply
    the projection. ``pre_ceded_contracts`` is §5.A.2.b's second
    projection, run "ignoring the effects of reinsurance ceded"; omitting
    it says the group cedes nothing, and the pair collapses.

    This is the :class:`Contract` path and therefore the overstating order
    — see :func:`aggregate_stochastic_reserve`. :func:`segment_group` is
    the prescribed one.
    """
    return _paired_group(
        name,
        lambda cs: aggregate_stochastic_reserve(
            cs, basis=basis,
            combined_payout_accumulation=combined_payout_accumulation),
        contracts, pre_ceded_contracts)


def segment_group(name: str, segments: Sequence[ModelSegment], *,
                  pre_ceded_segments: Sequence[ModelSegment] | None = None,
                  basis: VM22Basis = VM22_2026,
                  combined_payout_accumulation: bool = False
                  ) -> ReservingGroup:
    """A group carrying the SR by §3.F.5.a's order, on both of §3.B's bases.

    The prescribed path: combine the segments' present values, take the
    greatest in aggregate, then CTE 70 — and do it twice, because §5.A.2.a
    and §5.A.2.b are two projections and neither is derivable from the
    other. ``pre_ceded_segments`` omitted says the group cedes nothing.
    """
    return _paired_group(
        name,
        lambda ss: segment_stochastic_reserve(
            ss, basis=basis,
            combined_payout_accumulation=combined_payout_accumulation),
        segments, pre_ceded_segments)


def _paired_group(name: str, reserve, post_ceded, pre_ceded) -> ReservingGroup:
    """One reserve on each basis, from one calculation run twice."""
    post = reserve(post_ceded)
    pre = post if pre_ceded is None else reserve(pre_ceded)
    return ReservingGroup(name=name, method="stochastic",
                          amount=BasisPair(pre_ceded=pre, post_ceded=post))


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
