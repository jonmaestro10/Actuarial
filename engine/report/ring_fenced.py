"""Ring-fenced funds: where the diversification goes.

RFC-027 measured what Annex IV's two zeros are worth to a composite — a
life book and a non-life book at the same insurer are assumed to share no
risk, and putting them under one roof saves 19.3%. RFC-027 also named the
restriction that takes it back, and scoped it out:

    **Ring-fenced funds and matching adjustment portfolios** (Article 81
    and Article 217), where the notional SCR of each fund is computed
    separately and diversification between them is not recognised. That is
    a real and material restriction on everything above, and it is its own
    piece.

This is that piece. Articles 80, 81, 216 and 217 of Commission Delegated
Regulation (EU) 2015/35, consolidated version
``02015R0035 — EN — 30.07.2020 — 007.001``.

Ring-fencing costs twice
------------------------
**In the requirement.** Article 217(1) computes a notional Solvency Capital
Requirement for each ring-fenced fund, each matching adjustment portfolio
and the remaining part of the undertaking "in the same manner as if those
... were separate undertakings", 217(2) makes the undertaking's SCR the
**sum** of them, and 217(9) says it in terms: "undertakings shall assume
that there is no diversification of risks between each of the ring-fenced
funds ... and the remaining part".

**In the own funds.** Article 81(1) compares the restricted own-fund items
inside a fund with that fund's notional SCR and reduces the reconciliation
reserve by the excess. Capital trapped in a ring-fenced fund above what
that fund needs is not available to the rest of the undertaking, so it does
not count.

Both hit the same solvency ratio, one in the numerator and one in the
denominator.

The subtlety in Article 217(6)
------------------------------
A fund's notional SCR is **not** its standalone SCR. Paragraph 6 says the
notional requirement for each fund is calculated "using the scenario-based
calculations under which basic own funds **for the undertaking as a whole**
are most negatively affected", and paragraph 7 says how: sum the impact of
each scenario across every fund and the remaining part, and pick the worst
total.

So a fund does not get to choose the scenario that hurts it most. If one
fund's worst case is interest rates falling and another's is rates rising,
one of them is measured under a scenario it would not have picked — and its
notional SCR comes out **below** its standalone one. Ring-fencing therefore
costs less than "lose all diversification", and how much less depends on
whether the funds' exposures point the same way.

This is the same shape RFC-026 found in Article 164(3) and Articles 165(2)
and 188(7): the standard formula repeatedly makes a capital number depend
on *which scenario bound somewhere else*, and then reports the pieces
without it.

Article 216(2)'s escape hatch
-----------------------------
A ring-fenced fund with supervisory approval to use Article 304 of
Directive 2009/138/EC — the duration-based equity risk sub-module — is
**not** adjusted under Article 217 at all. It is calculated "on the
assumption of full diversification between the assets and liabilities of
the ring-fenced funds and the rest of the undertaking". The same fund, the
same risks, and the entire cost of ring-fencing turns on one approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.report.scr import basic_scr


@dataclass
class RingFencedFund:
    """One fund's capital requirements, by module and by scenario.

    ``modules`` maps a Basic SCR risk to the capital it needs under each
    named scenario — ``{"market": {"up": 120.0, "down": 200.0}}``. A module
    that is a factor rather than a scenario has a single entry, and its
    name is conventionally ``"only"``.

    ``restricted_own_funds`` are Article 80(2)'s restricted own-fund items
    within the fund: those that "can only be used to cover losses on a
    defined portion" of the business. Article 80(2) excludes the value of
    future transfers attributable to shareholders, which is a valuation
    question and therefore an input.
    """

    name: str
    modules: dict = field(default_factory=dict)
    restricted_own_funds: float = 0.0
    intangible: float = 0.0

    def scenarios(self, risk: str) -> tuple:
        return tuple(self.modules.get(risk, {}))

    def capital_under(self, choices: dict) -> dict:
        """Module capitals under a named scenario per risk.

        A risk whose chosen scenario it does not carry contributes zero —
        which is right, because a fund with no market risk is not affected
        by the market scenario the undertaking picked.
        """
        out = {}
        for risk, by_scenario in self.modules.items():
            chosen = choices.get(risk)
            if chosen is None:
                out[risk] = max(by_scenario.values(), default=0.0)
            else:
                out[risk] = by_scenario.get(chosen, 0.0)
        return out

    def worst_scenarios(self) -> dict:
        """The scenario that hurts *this* fund most, per risk.

        Not what Article 217(6) uses. Kept because the difference between
        this and the undertaking-level choice is the finding.
        """
        return {risk: max(by_scenario, key=by_scenario.get)
                for risk, by_scenario in self.modules.items() if by_scenario}

    def standalone_scr(self) -> float:
        """What the fund would need if it really were a separate undertaking."""
        return basic_scr(self.capital_under(self.worst_scenarios()),
                         intangible=self.intangible)

    def notional_scr(self, choices: dict) -> float:
        """Article 217(1) and (8), under the undertaking's chosen scenarios."""
        return basic_scr(self.capital_under(choices),
                         intangible=self.intangible)


def undertaking_scenarios(funds) -> dict:
    """Article 217(6) and (7): the scenario worst for the whole undertaking.

    Paragraph 7 is explicit about the arithmetic — sum the impact of each
    scenario across every fund and the remaining part, then take the worst
    total. So the choice is made on the **sum of module capitals**, before
    any aggregation, and a fund with a small exposure pointing the other
    way does not get a say.
    """
    risks = {risk for fund in funds for risk in fund.modules}
    choices = {}
    for risk in sorted(risks):
        totals = {}
        for fund in funds:
            for scenario, capital in fund.modules.get(risk, {}).items():
                totals[scenario] = totals.get(scenario, 0.0) + capital
        if totals:
            choices[risk] = max(totals, key=totals.get)
    return choices


def own_funds_restriction(fund: RingFencedFund, notional: float) -> float:
    """Article 81(1): the reduction to the reconciliation reserve.

    ``max(0, restricted own-fund items − notional SCR)``. Own funds trapped
    inside a fund count only up to what that fund itself needs; the surplus
    above it cannot cover losses anywhere else and so is taken out.

    Article 81(2)'s derogation — where the assets, liabilities and risk in
    a fund are not material, the *whole* of the restricted own-fund items
    may be removed instead — is a materiality judgement and is left to the
    caller, who can pass a notional of zero to get it.
    """
    return max(fund.restricted_own_funds - notional, 0.0)


@dataclass
class RingFencedPosition:
    """The undertaking's SCR and own funds after Articles 81 and 217."""

    funds: tuple
    choices: dict
    notional: dict
    restrictions: dict
    unrestricted_own_funds: float = 0.0

    @property
    def scr(self) -> float:
        """Article 217(2): the sum of the notional requirements."""
        return float(sum(self.notional.values()))

    @property
    def restriction(self) -> float:
        """Article 81(1), totalled over the funds."""
        return float(sum(self.restrictions.values()))

    @property
    def own_funds(self) -> float:
        trapped = sum(f.restricted_own_funds for f in self.funds)
        return self.unrestricted_own_funds + trapped - self.restriction

    @property
    def solvency_ratio(self) -> float:
        return float("inf") if self.scr == 0.0 else self.own_funds / self.scr

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """Two statements the standard makes that can be checked.

        Article 217(9): the total is the sum of the parts, with no
        diversification anywhere — so it is **at least** the largest
        notional requirement and **exactly** their sum.

        Article 81(1): no fund's restriction exceeds its own restricted
        own-fund items, so the reduction can never take out capital that
        was not trapped in the first place.
        """
        scale = max(1.0, self.scr)
        if abs(self.scr - sum(self.notional.values())) > tolerance * scale:
            return False
        for fund in self.funds:
            if self.restrictions[fund.name] > fund.restricted_own_funds + \
                    tolerance * scale:
                return False
        return True

    def __repr__(self) -> str:
        return (f"RingFencedPosition(SCR={self.scr:,.2f}, "
                f"restriction={self.restriction:,.2f}, "
                f"ratio={self.solvency_ratio:.1%})")


def ring_fenced_scr(funds, *, unrestricted_own_funds: float = 0.0
                    ) -> RingFencedPosition:
    """Articles 217(1), (2), (6), (7) and (9), and Article 81(1)."""
    funds = tuple(funds)
    if len({fund.name for fund in funds}) != len(funds):
        raise ValueError("fund names must be distinct")
    choices = undertaking_scenarios(funds)
    notional = {fund.name: fund.notional_scr(choices) for fund in funds}
    restrictions = {fund.name: own_funds_restriction(fund,
                                                     notional[fund.name])
                    for fund in funds}
    return RingFencedPosition(funds=funds, choices=choices, notional=notional,
                              restrictions=restrictions,
                              unrestricted_own_funds=unrestricted_own_funds)


def merged_scr(funds) -> float:
    """Article 216(2): the same business with full diversification.

    What the undertaking would need if the funds were not ring-fenced —
    module capitals added across funds, then aggregated once through Annex
    IV. This is the counterfactual the cost of ring-fencing is measured
    against, and it is also literally what Article 216(2) prescribes for a
    fund with Article 304 approval.
    """
    funds = tuple(funds)
    choices = undertaking_scenarios(funds)
    combined = {}
    for fund in funds:
        for risk, capital in fund.capital_under(choices).items():
            combined[risk] = combined.get(risk, 0.0) + capital
    intangible = sum(fund.intangible for fund in funds)
    return basic_scr(combined, intangible=intangible)


def ring_fencing_cost(funds) -> dict:
    """What ring-fencing costs, split into its two causes.

    ``lost_diversification`` is Article 217(9) — the sum of the notional
    requirements against the single aggregate the same modules would give.
    ``scenario_relief`` is Article 217(6) working the other way: because
    each fund is measured under the scenario worst for the *undertaking*
    rather than its own, the notional requirements can total less than the
    standalone ones. It is reported separately because it is the only part
    of this regime that runs in the undertaking's favour.
    """
    funds = tuple(funds)
    position = ring_fenced_scr(funds)
    standalone = float(sum(fund.standalone_scr() for fund in funds))
    merged = merged_scr(funds)
    return {
        "merged": merged,
        "standalone_sum": standalone,
        "ring_fenced": position.scr,
        "lost_diversification": position.scr - merged,
        "scenario_relief": standalone - position.scr,
    }
