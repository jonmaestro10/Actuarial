"""The Solvency Capital Requirement, assembled — and what the adjustment costs.

RFC-014 built the life underwriting stresses and RFC-026 the market risk
module. Both produce a *module* capital. This is the layer above: Annex IV's
aggregation into a Basic SCR, Article 204's operational charge, and the
piece RFC-014 named as out of scope in as many words —

    **The loss-absorbing capacity of technical provisions and deferred tax**
    (the "adjustment"), which needs the with-profits and tax structure of a
    specific fund.

RFC-019 built the with-profits structure and `engine/data/tax.py` has had a
tax basis since PLAN §5.1. So the adjustment can be built, and Article 103
of Directive 2009/138/EC can finally be written down in full::

    SCR = BSCR + SCR_operational + Adj

Every number here is transcribed from the Official Journal: Directive
2009/138/EC (Articles 103, 104 and 108, and Annex IV) and Commission
Delegated Regulation (EU) 2015/35 in the consolidated version
``02015R0035 — EN — 30.07.2020 — 007.001`` (Articles 203 to 207). Commission
Delegated Regulation (EU) 2026/269 does not amend Articles 205 to 207, so
unlike RFC-026's market risk parameters this layer has one regime.

The minus sign in front of Article 206
--------------------------------------
Article 103 says the SCR is the **sum** of three items, one of which is the
adjustment — so the adjustment has to be negative for it to adjust anything.
It is. Article 206(1) reads::

    Adj_TP = − max(min(BSCR − nBSCR, FDB), 0)

with a leading minus that the machine-readable renderings of the regulation
lose, because the character comes through as a mojibake ``Ä`` in the
consolidated PDF's text layer. Drop it and nothing raises: the SCR is simply
wrong by twice the adjustment, in the direction that looks prudent.

So the sign convention here is the regulation's and not a preference. Both
adjustments are **negative or zero**, both are *added*, and
:meth:`SolvencyCapitalRequirement.reconciles` checks that the total is the
sum of its parts rather than trusting it.

The three things that are easy to get wrong
-------------------------------------------
**The net BSCR is not the gross BSCR scaled.** Article 206(2) recomputes
every scenario-based module allowing the shock to reduce future
discretionary benefits, and then re-aggregates. Absorption therefore changes
the *mix* of the modules, and a correlation matrix is not linear in its
inputs, so ``BSCR − nBSCR`` is not the sum of the module-level absorptions.
RFC-026 found the same shape in Article 164(3); here it is one level up.

**Taking relief from technical provisions costs you relief from deferred
tax.** Article 207(1) makes the deferred tax loss ``BSCR + Adj_TP +
SCR_op``, and ``Adj_TP`` is negative — so absorbing a loss in the with-profits
fund shrinks the loss the tax line is allowed to absorb. At a tax rate ``t``
each unit of technical-provision absorption buys only ``1 − t`` of SCR.

**The intangible module is added outside the square root.** Article 203's
charge is 80% of the intangible assets and Annex IV point 1 adds it to the
aggregate rather than into it, so it receives no diversification benefit at
all — the only module in the standard formula that does not.

Where the standard is judgement, this takes the answer
------------------------------------------------------
Article 207(2a) lets an undertaking use an *increase* in deferred tax assets
only if it can demonstrate probable future taxable profit, and 207(2c) sets
conditions on the projection that produces that demonstration. No amount of
arithmetic settles it. :class:`DeferredTaxes` therefore takes the
recognisable amount as an input, in the same way RFC-014 takes the risk
margin's run-off driver: the module does the clamping the article requires
and does not pretend to do the demonstration.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.report.solvency2 import CorrelationMatrix

#: Annex IV point 1 to Directive 2009/138/EC. The order is the order of the
#: published matrix.
BSCR_RISKS = ("market", "default", "life", "health", "non_life")

#: Article 203: 80% of the intangible assets recognised under Article 12(2).
INTANGIBLE_FACTOR = 0.8

#: Article 204(1): the operational charge is capped at 30% of the Basic SCR
#: — before the unit-linked expense term, which escapes the cap.
OPERATIONAL_CAP = 0.30
UNIT_LINKED_EXPENSE_FACTOR = 0.25


def basic_scr_correlation() -> CorrelationMatrix:
    """Annex IV point 1's correlation matrix.

    Everything correlates with everything at 0.25 except three cells: life
    against non-life and health against non-life are **zero**, and default
    against non-life is **0.5**. The zeros are the interesting ones — the
    standard formula asserts that a life book and a non-life book at the
    same insurer share no risk at all, which is the single largest
    diversification benefit a composite gets.
    """
    matrix = [
        [1.00, 0.25, 0.25, 0.25, 0.25],
        [0.25, 1.00, 0.25, 0.25, 0.50],
        [0.25, 0.25, 1.00, 0.25, 0.00],
        [0.25, 0.25, 0.25, 1.00, 0.00],
        [0.25, 0.50, 0.00, 0.00, 1.00],
    ]
    return CorrelationMatrix(BSCR_RISKS, matrix)


def intangible_capital(value: float) -> float:
    """Article 203: ``SCR_intangible = 0.8 · V_intangible``."""
    return max(float(value), 0.0) * INTANGIBLE_FACTOR


def basic_scr(modules: dict, *, intangible: float = 0.0) -> float:
    """Annex IV point 1: ``sqrt(ΣΣ Corr(i,j)·SCR_i·SCR_j) + SCR_intangible``.

    The intangible charge is **outside** the root, so it diversifies with
    nothing. A hundred of intangible risk adds a hundred to the Basic SCR;
    a hundred of any other module adds a fraction of it.
    """
    return basic_scr_correlation().aggregate(modules) + float(intangible)


@dataclass(frozen=True)
class OperationalRisk:
    """Article 204: the operational charge, from premiums and provisions.

    Not a projection and not a scenario — a factor formula on volumes,
    which is why it sits outside the Basic SCR and outside the correlation
    matrix entirely.

    ``prior_*`` are the premiums earned in the twelve months *before* the
    last twelve, which Article 204(3) uses to charge growth: the charge
    picks up an extra 4% (life) or 3% (non-life) of whatever this year's
    premium exceeds 1.2 times last year's. Technical provisions exclude the
    risk margin and are gross of reinsurance — Article 204(4).
    """

    earned_life: float = 0.0
    earned_life_ul: float = 0.0
    earned_non_life: float = 0.0
    prior_life: float = 0.0
    prior_life_ul: float = 0.0
    prior_non_life: float = 0.0
    tp_life: float = 0.0
    tp_life_ul: float = 0.0
    tp_non_life: float = 0.0
    unit_linked_expenses: float = 0.0

    def premiums_basis(self) -> float:
        """Article 204(3). The growth terms are floored at zero separately,
        so shrinking one line of business cannot offset growth in another."""
        level = (0.04 * (self.earned_life - self.earned_life_ul)
                 + 0.03 * self.earned_non_life)
        life_growth = max(
            0.0,
            0.04 * ((self.earned_life - 1.2 * self.prior_life)
                    - (self.earned_life_ul - 1.2 * self.prior_life_ul)),
        )
        non_life_growth = max(
            0.0, 0.03 * (self.earned_non_life - 1.2 * self.prior_non_life)
        )
        return level + life_growth + non_life_growth

    def provisions_basis(self) -> float:
        """Article 204(4). Note the 0.45% on life against 3% on non-life —
        the same provision attracts nearly seven times the charge on the
        non-life side of a composite."""
        return (0.0045 * max(0.0, self.tp_life - self.tp_life_ul)
                + 0.03 * max(0.0, self.tp_non_life))

    @property
    def basic(self) -> float:
        """Article 204(2): the **worse** of the two bases, never their sum."""
        return max(self.premiums_basis(), self.provisions_basis())

    def capital(self, bscr: float) -> float:
        """Article 204(1): ``min(0.3·BSCR, Op) + 0.25·Exp_ul``.

        Two things worth saying out loud. The cap makes the operational
        charge a function of the *other* risks — a firm that de-risks its
        balance sheet cuts an operational charge that has not changed. And
        the unit-linked expense term is added **outside** the cap, so it is
        the one part of the standard formula's operational charge that
        nothing limits.
        """
        return (min(OPERATIONAL_CAP * float(bscr), self.basic)
                + UNIT_LINKED_EXPENSE_FACTOR * self.unit_linked_expenses)

    def capped(self, bscr: float) -> bool:
        """Is the 30% cap the binding constraint?"""
        return OPERATIONAL_CAP * float(bscr) < self.basic


def technical_provision_adjustment(bscr: float, net_bscr: float,
                                   future_discretionary_benefits: float
                                   ) -> float:
    """Article 206(1): ``− max(min(BSCR − nBSCR, FDB), 0)``.

    Negative or zero, because Article 103 of the Directive *sums* it into
    the SCR.

    Two clamps, and they are the whole character of the sub-module. The
    floor at zero means absorption cannot make the requirement worse. The
    cap at ``FDB`` — technical provisions without risk margin in respect of
    future discretionary benefits — means a fund can only absorb what it
    could actually take away from policyholders: a fund with no
    discretionary benefits gets **no relief at all** however much its
    liabilities would move, and a fund whose absorption already exceeds its
    discretionary benefits gets **nothing further** for absorbing more.
    """
    if future_discretionary_benefits < 0.0:
        raise ValueError(
            f"future discretionary benefits {future_discretionary_benefits} "
            "cannot be negative; they are technical provisions"
        )
    absorbed = min(float(bscr) - float(net_bscr),
                   float(future_discretionary_benefits))
    return -max(absorbed, 0.0)


@dataclass(frozen=True)
class DeferredTaxes:
    """The tax position the Article 207 loss is applied to.

    ``net_liability`` is the deferred tax **liability** less the deferred
    tax asset on the Solvency II balance sheet. A loss releases that
    liability first, which needs no demonstration of anything.

    ``recognisable_asset`` caps the deferred tax **asset** the loss is
    allowed to create beyond that. This is Article 207(2a) — an increase in
    deferred tax assets may be used only where the undertaking can
    demonstrate probable future taxable profit against which it can be
    utilised, under conditions Article 207(2c) sets on the projection. That
    is a judgement, so it is an input: ``0.0`` is a demonstration not made,
    ``None`` is one made in full, and a number is one made in part.
    """

    rate: float
    net_liability: float = 0.0
    recognisable_asset: float | None = None

    def __post_init__(self):
        if not 0.0 <= self.rate < 1.0:
            raise ValueError(f"tax rate {self.rate} must be in [0, 1)")
        if self.recognisable_asset is not None and self.recognisable_asset < 0.0:
            raise ValueError(
                f"recognisable asset {self.recognisable_asset} cannot be "
                "negative"
            )

    def utilised(self, loss: float) -> tuple:
        """``(against the liability, as a new asset)`` for a loss."""
        credit = self.rate * max(float(loss), 0.0)
        against = min(credit, max(self.net_liability, 0.0))
        remainder = credit - against
        if self.recognisable_asset is not None:
            remainder = min(remainder, self.recognisable_asset)
        return against, remainder


def deferred_tax_adjustment(loss: float, taxes: DeferredTaxes) -> float:
    """Article 207: the change in deferred taxes under an instantaneous loss.

    ``loss`` is Article 207(1)'s sum — ``BSCR + Adj_TP + SCR_op`` — and
    note that ``Adj_TP`` is negative, so relief already taken in the
    technical provisions shrinks the loss the tax line is offered.

    Negative or zero: Article 207(3) makes a released liability or a
    recognised asset a *negative* adjustment, and 207(4) makes a positive
    change nil, so this can only reduce the requirement.
    """
    against, recognised = taxes.utilised(loss)
    return -(against + recognised)


@dataclass
class SolvencyCapitalRequirement:
    """Article 103: the Basic SCR, the operational charge, and the adjustment.

    Both adjustments are negative or zero and both are **added**, which is
    the regulation's own convention and the one thing about this
    calculation that is worth checking rather than assuming.
    """

    bscr: float
    operational: float
    adjustment_tp: float = 0.0
    adjustment_dt: float = 0.0
    modules: dict | None = None
    net_modules: dict | None = None

    @property
    def adjustment(self) -> float:
        """Article 205: the sum of the two."""
        return self.adjustment_tp + self.adjustment_dt

    @property
    def scr(self) -> float:
        return self.bscr + self.operational + self.adjustment

    @property
    def undiversified(self) -> float:
        """What the Basic SCR would be with no diversification at all."""
        if self.modules is None:
            return self.bscr
        return float(sum(max(v, 0.0) for v in self.modules.values()))

    @property
    def relief(self) -> float:
        """The adjustment as a share of what it adjusts."""
        gross = self.bscr + self.operational
        return 0.0 if gross <= 0.0 else -self.adjustment / gross

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        """Article 103's sum, and both adjustments the right side of zero.

        The sign is the point. An implementation that dropped Article
        206(1)'s leading minus would report a *larger* SCR and raise
        nothing, so it is checked rather than trusted.
        """
        scale = max(1.0, abs(self.bscr))
        total = self.bscr + self.operational + self.adjustment_tp \
            + self.adjustment_dt
        return (abs(self.scr - total) <= tolerance * scale
                and self.adjustment_tp <= tolerance * scale
                and self.adjustment_dt <= tolerance * scale
                and self.scr >= -tolerance * scale)

    def __repr__(self) -> str:
        return (f"SolvencyCapitalRequirement(BSCR={self.bscr:,.2f}, "
                f"op={self.operational:,.2f}, adj={self.adjustment:,.2f}, "
                f"SCR={self.scr:,.2f})")


def solvency_capital_requirement(
        modules: dict, *, net_modules: dict | None = None,
        intangible: float = 0.0,
        operational: OperationalRisk | None = None,
        future_discretionary_benefits: float = 0.0,
        taxes: DeferredTaxes | None = None) -> SolvencyCapitalRequirement:
    """Assemble the standard-formula SCR.

    ``modules`` is the gross Basic SCR by risk; ``net_modules`` is the same
    thing recomputed under Article 206(2), with each scenario allowed to
    reduce future discretionary benefits. Passing no ``net_modules`` means
    a fund with nothing to absorb with, and the adjustment for technical
    provisions is zero — which is the right answer for a fund with no
    discretionary benefits, and is why it is the default rather than an
    error.
    """
    gross = basic_scr(modules, intangible=intangible)
    net = (basic_scr(net_modules, intangible=intangible)
           if net_modules is not None else gross)
    operational = operational or OperationalRisk()
    charge = operational.capital(gross)
    adj_tp = technical_provision_adjustment(gross, net,
                                            future_discretionary_benefits)
    # Article 207(1): the loss is BSCR + Adj_TP + SCR_op, and Adj_TP is
    # already negative, so the technical provisions have first call on it.
    adj_dt = (deferred_tax_adjustment(gross + adj_tp + charge, taxes)
              if taxes is not None else 0.0)
    return SolvencyCapitalRequirement(
        bscr=gross, operational=charge, adjustment_tp=adj_tp,
        adjustment_dt=adj_dt, modules=dict(modules),
        net_modules=None if net_modules is None else dict(net_modules),
    )


def module_absorptions(modules: dict, net_modules: dict) -> dict:
    """How much each module absorbs, before aggregation.

    Reported because their **sum is not the adjustment**: Article 206(2)
    recomputes each module net and then re-aggregates, and a correlation
    matrix is not linear in its inputs. :func:`absorption_gap` measures the
    difference on a given fund.
    """
    unknown = set(net_modules) - set(modules)
    if unknown:
        raise ValueError(f"{sorted(unknown)} are not gross modules")
    return {name: value - net_modules.get(name, value)
            for name, value in modules.items()}


def absorption_gap(modules: dict, net_modules: dict, *,
                   intangible: float = 0.0) -> float:
    """``Σ module absorptions − (BSCR − nBSCR)``.

    Positive means aggregation gave back less than the modules gave up,
    which is the usual direction: diversification already discounted the
    gross modules, so removing risk from them removes less than its face
    value from the total.
    """
    gross = basic_scr(modules, intangible=intangible)
    net = basic_scr(net_modules, intangible=intangible)
    return float(sum(module_absorptions(modules, net_modules).values())
                 - (gross - net))
