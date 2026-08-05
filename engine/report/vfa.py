"""IFRS 17: the variable fee approach.

The measurement model for **direct participating contracts** — the family
this engine already models most of. A unit-linked contract with an annual
management charge, a universal-life account value, a fixed-indexed annuity:
in each of them the policyholder holds a share of a pool of assets and the
insurer takes a fee out of it. The insurer's profit is a **variable fee**,
and it moves with the pool.

RFC-012's general measurement model gets that wrong for these contracts, and
visibly so: under the GMM the CSM is a historic-cost balance accreting at a
locked-in rate, so a market move goes straight to profit and loss in the
period it happens. For a contract whose profit *is* a share of the market,
that reports volatility the insurer does not experience.

The variable fee approach fixes it with one change of substance:

    **The CSM absorbs the change in the entity's share of the fair value of
    the underlying items, and the changes in fulfilment cashflows that
    financial variables cause.**

Two consequences follow, and they run in opposite directions.

- A market move no longer hits the period's profit. It adjusts the unearned
  margin, and shows up as more or less profit over the remaining coverage.
- **The CSM is no longer safe.** A fall large enough to exhaust it makes the
  group onerous, and RFC-012's asymmetry then applies in full: the excess
  goes to profit and loss immediately, and a later recovery has to
  extinguish the loss component before it rebuilds a margin. Under the GMM,
  a market move can never do that, because the CSM does not hear about it.

There is no locked-in accretion under the VFA. The CSM's growth *is* the
entity's share of the underlying items' return, which is a current-value
number by construction — so the historic-cost balance inside a current-value
liability, which RFC-012 measured at six times the interest, does not exist
here at all.

What this module reports, and what it does not
----------------------------------------------
``Measurement.profit`` is the **insurance result** — the service result
less insurance finance expense. The investment return on the underlying
items the entity holds is an asset-side number and sits outside it, so in a
period when the pool moves sharply the line here moves with the liability
and not with the entity's bottom line. Total profit still reconciles to the
group's net cash, because the variable fee is a re-measurement rather than
new money: the fee itself is already in the group's cashflows.

The risk mitigation option
--------------------------
§B115 lets an entity that hedges the financial risk elect **not** to adjust
the CSM for the changes it has hedged. The point is to stop a hedge's fair
value moving through profit while the thing it hedges is deferred into the
CSM — an accounting mismatch created by the very treatment above. Electing
it puts financial changes back through profit and loss, which is the GMM
answer, and this module treats it as exactly that rather than as a third
model.
"""

from __future__ import annotations

import numpy as np

from engine.data.rates import YieldCurve
from engine.report.ifrs17 import (
    CoverageUnits, Group, Measurement, RiskAdjustment, measure,
)

#: §B101's three conditions for a contract to be a direct participating one.
#: All three are assessed **at inception and never again**, which is why
#: they are a named tuple of findings rather than a running test: a contract
#: does not stop being a participating contract because a market fell.
ELIGIBILITY_CONDITIONS = (
    "clearly_identified_pool",
    "substantial_share_of_returns",
    "substantial_proportion_varies",
)


class Eligibility:
    """Whether a group qualifies for the variable fee approach.

    Deliberately not a calculation. §B101 is three judgements — is there a
    clearly identified pool of underlying items, does the entity expect to
    pay the policyholder a substantial share of the fair value returns on
    it, and does a substantial proportion of the cashflows vary with those
    items — and an entity makes them from the contract terms. A library
    that inferred them from a cashflow table would be guessing at the
    contract, so this records the answers and refuses to measure a group
    that does not have all three.

    "Substantial" is not defined in the standard. That is the entity's
    judgement and is recorded here as one, not resolved by a threshold
    nobody wrote down.
    """

    def __init__(self, *, clearly_identified_pool: bool,
                 substantial_share_of_returns: bool,
                 substantial_proportion_varies: bool, note: str = ""):
        self.clearly_identified_pool = clearly_identified_pool
        self.substantial_share_of_returns = substantial_share_of_returns
        self.substantial_proportion_varies = substantial_proportion_varies
        self.note = note

    @classmethod
    def direct_participating(cls, note: str = "") -> "Eligibility":
        """All three conditions met — the ordinary case for a unit-linked
        or account-value contract."""
        return cls(clearly_identified_pool=True,
                   substantial_share_of_returns=True,
                   substantial_proportion_varies=True, note=note)

    def __bool__(self) -> bool:
        return all(getattr(self, name) for name in ELIGIBILITY_CONDITIONS)

    def __repr__(self) -> str:
        return f"Eligibility(eligible={bool(self)})"

    def __fingerprint__(self):
        return {name: getattr(self, name) for name in ELIGIBILITY_CONDITIONS}

    def failures(self) -> tuple:
        return tuple(name for name in ELIGIBILITY_CONDITIONS
                     if not getattr(self, name))


class UnderlyingItems:
    """The pool the policyholder participates in, and the entity's share.

    ``fair_value`` is the pool at each of ``n + 1`` dates — for the
    templates in ``engine/library`` that is the account value or unit fund
    series straight off a run. ``entity_share`` is the proportion of it that
    is the insurer's variable fee.

    The **change** in the entity's share is what adjusts the CSM, and it is
    derived here rather than taken as an input, because a share and a change
    in a share supplied separately are two chances to disagree.
    """

    def __init__(self, fair_value, entity_share: float):
        fair_value = np.asarray(fair_value, dtype=np.float64)
        if fair_value.ndim != 1 or fair_value.size < 2:
            raise ValueError(
                "the underlying items need a fair value at each date, "
                "opening and closing included"
            )
        if np.any(fair_value < 0.0):
            raise ValueError("a pool of underlying items cannot be negative")
        if not 0.0 <= entity_share < 1.0:
            raise ValueError(
                f"entity share {entity_share} outside [0, 1); the entity "
                "takes a fee out of the pool, it does not own the pool"
            )
        self.fair_value = fair_value
        self.entity_share = entity_share

    @classmethod
    def from_run(cls, result, name: str, entity_share: float,
                 periods: int | None = None) -> "UnderlyingItems":
        """The pool from a projection's own output — ``"fund_eoy"`` for the
        unit-linked template, ``"av_eop"`` for the account-value ones."""
        values = np.asarray(result.aggregate(name), dtype=np.float64)
        if periods is not None:
            values = values[:periods + 1]
        return cls(values, entity_share)

    @property
    def n_periods(self) -> int:
        return int(self.fair_value.size - 1)

    def __repr__(self) -> str:
        return (f"UnderlyingItems({self.n_periods} periods, "
                f"entity_share={self.entity_share})")

    def __fingerprint__(self):
        return {"fair_value": self.fair_value,
                "entity_share": self.entity_share}

    def share(self) -> np.ndarray:
        """The entity's share of the pool at each date."""
        return self.entity_share * self.fair_value

    def fee_change(self) -> np.ndarray:
        """Change in the entity's share over each period.

        Positive where the pool grew. This is what the CSM absorbs, and
        what the entity simultaneously earns on the assets it holds — so it
        is deferred rather than created, which is the whole idea.
        """
        return np.diff(self.share())


def measure_vfa(group: Group, *, coverage: CoverageUnits,
                underlying: UnderlyingItems,
                eligibility: Eligibility | None = None,
                risk_adjustment: RiskAdjustment | None = None,
                current: YieldCurve, changes_in_estimate=None,
                financial_changes=None,
                risk_mitigation: bool = False) -> Measurement:
    """Measure a group of direct participating contracts.

    ``financial_changes`` is the series of changes in fulfilment cashflows
    caused by financial variables — a guarantee getting more expensive as
    markets fall, for instance — signed as the fulfilment cashflows are, so
    a positive entry is a worsening. Under the GMM these go to insurance
    finance expense in the period they arise. **Here they adjust the CSM**,
    which is the difference the approach exists to make.

    ``risk_mitigation`` is the §B115 election: financial changes bypass the
    CSM and go to profit and loss, exactly as under the GMM, because the
    entity is holding a derivative whose own fair value already does.
    ``changes_in_estimate`` — non-financial changes — adjusts the CSM either
    way, and is passed straight through.

    There is no ``locked_in`` argument. There is no locked-in accretion
    under this model, and offering a curve that did nothing would be worse
    than not offering one.
    """
    if eligibility is not None and not eligibility:
        raise ValueError(
            "this group does not meet §B101 and cannot be measured under "
            f"the variable fee approach; failing: {eligibility.failures()}"
        )
    n = group.n_periods
    if underlying.n_periods < n:
        raise ValueError(
            f"the underlying items cover {underlying.n_periods} periods and "
            f"the group covers {n}; the pool has to reach the end of the "
            "coverage the CSM is released over"
        )

    fee_change = underlying.fee_change()[:n]
    if financial_changes is None:
        financial = np.zeros(n)
    else:
        financial = np.asarray(financial_changes, dtype=np.float64)
        if financial.shape != (n,):
            raise ValueError(
                f"financial_changes covers {financial.shape} periods, the "
                f"group covers {n}"
            )

    non_financial = (np.zeros(n) if changes_in_estimate is None
                     else np.asarray(changes_in_estimate, dtype=np.float64))
    if non_financial.shape != (n,):
        raise ValueError(
            f"changes_in_estimate covers {non_financial.shape} periods, the "
            f"group covers {n}"
        )

    if risk_mitigation:
        # The election: hedged financial changes stay out of the CSM and go
        # to the insurance finance line — which is the route the GMM sends
        # them down. That is why this is one flag rather than a third
        # measurement model.
        adjustments = non_financial
        mitigated = financial
    else:
        adjustments = non_financial + financial
        mitigated = np.zeros(n)

    result = measure(
        group, coverage=coverage, risk_adjustment=risk_adjustment,
        current=current, changes_in_estimate=adjustments,
        financial_changes=mitigated,
        # The CSM's growth is the entity's share of the pool's movement,
        # not a rate. It is a re-measurement and not new money — the fee
        # itself is already in the group's cashflows — so it moves profit
        # between periods and cannot change the total. The reconciliation
        # to net cash is what holds that to account.
        csm_growth=fee_change,
    )
    result.entity_share = underlying.share()
    result.variable_fee = fee_change
    result.hedged_changes = mitigated
    result.risk_mitigation = risk_mitigation
    return result
