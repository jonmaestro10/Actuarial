"""US statutory reserves: the modified net premium, and the cap that bites.

Execution plan §5, item C2. Two halves that a statutory valuation needs and
that the repository already had the pieces for:

**Formulaic reserves.** A modified net-premium reserve is a net-premium
reserve with an *expense allowance* granted in the first year, and the
methods differ only in how much they allow. RFC-018's
:mod:`engine.library.reserves` already has the primitives and both extremes
— nothing allowed (net level) and everything allowed (full preliminary
term) — so this module adds the one that sits between them and is the one
US statute actually prescribes: **CRVM**, which is full preliminary term
with the allowance capped.

**Asset adequacy.** Cash-flow testing asks whether the assets backing a
block, run forward with its liabilities under a set of scenarios, run out.
That is the same question VM-20 asks, and — this is the point — the same
*object*: an accumulated deficiency, discounted, maximised over dates.
RFC-016 already computes it. What differs between asset adequacy and a
principle-based reserve is not the arithmetic but **the reduction across
scenarios**: a handful of prescribed paths reduced by a maximum, against
thousands reduced by a CTE. So this module reuses the machinery and makes
the reduction an argument rather than an assumption.

The finding: first-year strain is exactly the cap's bite
-------------------------------------------------------
A modified method is a pair of net premiums — ``alpha`` in year one,
``beta`` thereafter — and the whole method is one number, the expense
allowance ``E = beta - alpha``. Given ``E``:

    beta = P + E / ä             alpha = beta - E
    V_t  = A_t - beta · ä_t      (t >= 1),   V_0 = 0

with ``P`` the net level premium and ``ä`` the annuity-due factor at issue.
Full preliminary term is the ``E`` that makes ``alpha`` the one-year term
cost, and it drives the first-year reserve to exactly zero. CRVM allows the
same ``E`` **unless** it exceeds the allowance a twenty-payment whole life
would earn at the same age, in which case the cap applies.

Because ``V_1`` is linear in ``E`` and vanishes at ``E_fpt``, the first-year
CRVM reserve has a closed form:

    V_1 = (E_fpt - E) · ä_{x+1:n-1} / ä_{x:n}

**The first year's statutory strain is exactly proportional to how much the
cap bit**, and it is zero for every plan where the cap does not bind. That
is asserted to the last bits in tests/test_statutory.py rather than argued
here, and it has a consequence worth stating: the reserve is continuous in
plan design but **not differentiable** where the cap starts binding, so a
first-order sensitivity computed on one side of that boundary mispredicts
the other — the same shape of trap as RFC-026's counterparty band cliff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from engine.library.reserves import (
    annuity_due,
    assurance,
    benefit_value,
    net_premium,
    prospective_reserve,
)
from engine.report.pbr import (
    accumulated_surplus,
    cte,
    greatest_present_value_of_accumulated_deficiency,
    path_discount_factors,
)

#: The reference plan whose allowance caps CRVM: a twenty-payment whole
#: life at the same issue age. Named rather than inlined because it is the
#: whole content of the method, and because a regime that moves it moves
#: every first-year reserve in the book.
CRVM_REFERENCE_PAYMENTS = 20

#: How a set of scenarios is reduced to one number. Asset adequacy tests a
#: handful of prescribed paths and takes the worst; a principle-based
#: reserve tests thousands and takes a tail expectation. Same deficiency,
#: different question.
REDUCTIONS = ("maximum", "cte")


class StatutoryError(ValueError):
    """A statutory figure this module will not report.

    A term too short for a modified method to mean anything, an unknown
    reduction, an adequacy test with no scenarios, asset and liability
    cashflows that disagree about how long the projection is.
    """


# --------------------------------------------------------------------------
# The expense allowance
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpenseAllowance:
    """How much of the first year's cost a method lets the office defer.

    ``granted`` is what the method actually allows: the smaller of the full
    preliminary term allowance and the cap. ``binds`` says which — and it
    is the first thing to look at on any block, because the two sides of
    that comparison behave completely differently in the first year.
    """

    full_preliminary_term: float
    cap: float
    granted: float
    binds: bool

    def bite(self) -> float:
        """How much the cap took away. Zero when it does not bind."""
        return self.full_preliminary_term - self.granted

    def __fingerprint__(self):
        return {"full_preliminary_term": self.full_preliminary_term,
                "cap": self.cap, "granted": self.granted, "binds": self.binds}


def _one_year_term_cost(basis, age: int, rate: float,
                        sum_assured: float) -> float:
    """``c_x`` — the net cost of one year's death cover, per unit."""
    return float(sum_assured * assurance(basis, age, 1, rate)[0])


def fpt_allowance(basis, age: int, term: int, rate: float, *,
                  product: str = "term", sum_assured: float = 1.0,
                  maturity: float | None = None) -> float:
    """The expense allowance full preliminary term grants.

    Derived rather than tabulated: it is the ``E`` for which the first
    year's modified net premium is exactly the one-year term cost, which is
    what "treat year one as a one-year term assurance" means.
    """
    if term < 2:
        raise StatutoryError(
            f"a modified reserve needs at least two years, got {term}"
        )
    factor = float(annuity_due(basis, age, term, rate)[0])
    level = net_premium(basis, age, term, rate, product=product,
                        sum_assured=sum_assured, maturity=maturity)
    cost = _one_year_term_cost(basis, age, rate, sum_assured)
    if factor <= 1.0:
        raise StatutoryError(
            "the annuity factor is not above one, so there is no renewal "
            "period for an allowance to be spread over"
        )
    return (level - cost) * factor / (factor - 1.0)


def crvm_allowance(basis, age: int, term: int, rate: float, *,
                   product: str = "term", sum_assured: float = 1.0,
                   maturity: float | None = None,
                   reference_payments: int = CRVM_REFERENCE_PAYMENTS,
                   whole_life_term: int | None = None) -> ExpenseAllowance:
    """CRVM's allowance: full preliminary term, capped.

    The cap is the allowance a whole life paying ``reference_payments``
    premiums would earn at the same issue age. ``whole_life_term`` is how
    far "whole life" is projected — the table's own end, in practice, and
    an argument here because a module that guessed at the end of somebody's
    mortality table would be guessing at the cap.
    """
    granted_fpt = fpt_allowance(basis, age, term, rate, product=product,
                                sum_assured=sum_assured, maturity=maturity)
    horizon = whole_life_term if whole_life_term is not None else term
    if horizon < 2:
        raise StatutoryError("the reference plan needs at least two years")
    # The reference plan: whole life, premiums limited to the reference
    # count. Its allowance is computed the same way, so the comparison is
    # like with like rather than a table lookup nobody can rederive.
    factor = float(annuity_due(basis, age, min(reference_payments, horizon),
                               rate)[0])
    level = net_premium(basis, age, horizon, rate, product="whole_life",
                        sum_assured=sum_assured,
                        premium_term=min(reference_payments, horizon))
    cost = _one_year_term_cost(basis, age, rate, sum_assured)
    if factor <= 1.0:
        raise StatutoryError("the reference plan has no renewal period")
    cap = (level - cost) * factor / (factor - 1.0)
    granted = min(granted_fpt, cap)
    return ExpenseAllowance(full_preliminary_term=granted_fpt, cap=cap,
                            granted=granted, binds=cap < granted_fpt)


# --------------------------------------------------------------------------
# The modified reserve
# --------------------------------------------------------------------------

def modified_premiums(basis, age: int, term: int, rate: float, *,
                      allowance: float, product: str = "term",
                      sum_assured: float = 1.0,
                      maturity: float | None = None) -> tuple[float, float]:
    """``(alpha, beta)`` for a given expense allowance.

    ``beta = P + E / ä`` and ``alpha = beta - E``. Both fall straight out of
    "the modified premiums have the same value at issue as the level one",
    and writing them this way makes the whole family — net level, CRVM,
    full preliminary term — one function of one number.
    """
    factor = float(annuity_due(basis, age, term, rate)[0])
    if factor <= 0.0:
        raise StatutoryError("the annuity factor at issue is zero")
    level = net_premium(basis, age, term, rate, product=product,
                        sum_assured=sum_assured, maturity=maturity)
    beta = level + allowance / factor
    return beta - allowance, beta


def modified_reserve(basis, age: int, term: int, rate: float, *,
                     allowance: float, product: str = "term",
                     sum_assured: float = 1.0,
                     maturity: float | None = None) -> np.ndarray:
    """The reserve on a modified net premium basis, per duration.

    Zero at issue by construction — the modified premiums are solved to
    make it so — and the renewal premium ``beta`` from there on.
    """
    _, beta = modified_premiums(basis, age, term, rate, allowance=allowance,
                                product=product, sum_assured=sum_assured,
                                maturity=maturity)
    reserve = prospective_reserve(basis, age, term, rate, premium=beta,
                                  product=product, sum_assured=sum_assured,
                                  maturity=maturity)
    # The modified basis defines V_0 as zero; the prospective expression
    # gives the same thing up to float noise, and the definition wins.
    reserve = np.asarray(reserve, dtype=np.float64).copy()
    reserve[0] = 0.0
    return reserve


def crvm_reserve(basis, age: int, term: int, rate: float, *,
                 product: str = "term", sum_assured: float = 1.0,
                 maturity: float | None = None,
                 reference_payments: int = CRVM_REFERENCE_PAYMENTS,
                 whole_life_term: int | None = None) -> np.ndarray:
    """The Commissioners Reserve Valuation Method reserve, per duration.

    Identical to :func:`~engine.library.reserves.full_preliminary_term`
    wherever the cap does not bind — asserted in the tests rather than
    hoped for — and strictly above it wherever it does.
    """
    allowance = crvm_allowance(
        basis, age, term, rate, product=product, sum_assured=sum_assured,
        maturity=maturity, reference_payments=reference_payments,
        whole_life_term=whole_life_term,
    )
    return modified_reserve(basis, age, term, rate,
                            allowance=allowance.granted, product=product,
                            sum_assured=sum_assured, maturity=maturity)


def first_year_strain(basis, age: int, term: int, rate: float, *,
                      product: str = "term", sum_assured: float = 1.0,
                      maturity: float | None = None,
                      reference_payments: int = CRVM_REFERENCE_PAYMENTS,
                      whole_life_term: int | None = None) -> float:
    """The closed form for the first-year CRVM reserve.

    ``V_1 = (E_fpt - E) · ä_{x+1:n-1} / ä_{x:n}`` — the cap's bite, scaled
    by the share of the annuity that lies in the renewal period. Computed
    independently of :func:`crvm_reserve` on purpose: the test that matters
    is that the two agree, which is the identity rather than a restatement
    of it.
    """
    allowance = crvm_allowance(
        basis, age, term, rate, product=product, sum_assured=sum_assured,
        maturity=maturity, reference_payments=reference_payments,
        whole_life_term=whole_life_term,
    )
    at_issue = float(annuity_due(basis, age, term, rate)[0])
    renewal = float(annuity_due(basis, age + 1, term - 1, rate)[0])
    return allowance.bite() * renewal / at_issue


@dataclass(frozen=True)
class StatutoryReserve:
    """The formulaic reserve a statement actually carries.

    The greater of the modified reserve and the cash surrender value, per
    duration, with ``binding`` saying which one it is at each. A statutory
    minimum that is the cash value rather than the method's reserve is a
    different thing to explain, and knowing where the crossover falls is
    the first question anybody asks about a plan.
    """

    method: str
    reserve: np.ndarray
    cash_value: np.ndarray
    allowance: ExpenseAllowance | None = None

    @property
    def value(self) -> np.ndarray:
        return np.maximum(self.reserve, self.cash_value)

    @property
    def binding(self) -> np.ndarray:
        """``"cash_value"`` where the floor bites, ``"reserve"`` elsewhere."""
        return np.where(self.cash_value >= self.reserve, "cash_value",
                        "reserve")

    def crossover(self) -> int | None:
        """First duration at which the method's reserve overtakes the floor."""
        above = np.flatnonzero(self.reserve > self.cash_value)
        return int(above[0]) if above.size else None

    def __fingerprint__(self):
        return {"method": self.method, "reserve": self.reserve,
                "cash_value": self.cash_value, "allowance": self.allowance}


def statutory_reserve(basis, age: int, term: int, rate: float, *,
                      cash_value=0.0, method: str = "crvm",
                      product: str = "term", sum_assured: float = 1.0,
                      maturity: float | None = None,
                      **options) -> StatutoryReserve:
    """The formulaic minimum: a modified reserve, floored by cash value."""
    if method == "crvm":
        allowance = crvm_allowance(basis, age, term, rate, product=product,
                                   sum_assured=sum_assured,
                                   maturity=maturity, **options)
        granted = allowance.granted
    elif method == "net_level":
        allowance, granted = None, 0.0
    elif method == "full_preliminary_term":
        granted = fpt_allowance(basis, age, term, rate, product=product,
                                sum_assured=sum_assured, maturity=maturity)
        allowance = ExpenseAllowance(full_preliminary_term=granted,
                                     cap=math_inf(), granted=granted,
                                     binds=False)
    else:
        raise StatutoryError(
            f"unknown method {method!r}; this module carries 'crvm', "
            f"'net_level' and 'full_preliminary_term'"
        )
    reserve = modified_reserve(basis, age, term, rate, allowance=granted,
                               product=product, sum_assured=sum_assured,
                               maturity=maturity)
    floor = np.broadcast_to(np.asarray(cash_value, dtype=np.float64),
                            reserve.shape).astype(np.float64)
    return StatutoryReserve(method=method, reserve=reserve, cash_value=floor,
                            allowance=allowance)


def math_inf() -> float:
    """``inf``, as the cap on an uncapped method — it never binds."""
    return float("inf")


# --------------------------------------------------------------------------
# Asset adequacy
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AdequacyResult:
    """What cash-flow testing found.

    ``deficiency`` is one number per scenario: the greatest present value
    of accumulated deficiency, which is zero for a scenario whose assets
    never run out. ``additional_reserve`` is those numbers reduced — and
    which reduction was used is recorded, because a maximum over seven
    prescribed paths and a CTE over ten thousand are different statements
    about the same block.
    """

    deficiency: np.ndarray
    additional_reserve: float
    reduction: str
    level: float | None
    worst_scenario: int
    worst_date: int

    @property
    def adequate(self) -> np.ndarray:
        """Per scenario: did the assets last?"""
        return self.deficiency <= 0.0

    @property
    def n_inadequate(self) -> int:
        return int((~self.adequate).sum())

    def __fingerprint__(self):
        return {"deficiency": self.deficiency,
                "additional_reserve": self.additional_reserve,
                "reduction": self.reduction, "level": self.level,
                "worst_scenario": self.worst_scenario,
                "worst_date": self.worst_date}


def asset_adequacy(asset_cashflows, liability_cashflows, earned_rates, *,
                   starting_assets: float = 0.0,
                   reduction: str = "maximum",
                   level: float | None = None) -> AdequacyResult:
    """Cash-flow test a block: do the assets outlast the liabilities?

    ``asset_cashflows`` and ``liability_cashflows`` are
    ``(n_steps, n_scenarios)`` — income in and benefits out — and the net of
    them is rolled forward at ``earned_rates`` exactly as RFC-016 rolls a
    principle-based projection, because it is the same roll.

    ``reduction`` is the honest part. Asset adequacy opinions are written
    against a small set of prescribed paths and the practitioner looks at
    the worst of them; a principle-based reserve looks at a tail
    expectation over thousands. Both are here, neither is a default that
    hides the other, and the result records which was used.
    """
    assets = np.asarray(asset_cashflows, dtype=np.float64)
    liabilities = np.asarray(liability_cashflows, dtype=np.float64)
    rates = np.asarray(earned_rates, dtype=np.float64)
    if assets.shape != liabilities.shape:
        raise StatutoryError(
            f"asset cashflows are {assets.shape} and liability cashflows "
            f"{liabilities.shape}; netting them would be netting different "
            f"projections"
        )
    if assets.size == 0:
        raise StatutoryError("an adequacy test needs at least one scenario")
    if reduction not in REDUCTIONS:
        raise StatutoryError(
            f"reduction must be one of {list(REDUCTIONS)}, got {reduction!r}"
        )

    net = assets - liabilities
    deficiency = greatest_present_value_of_accumulated_deficiency(
        net, rates, starting_assets)
    if reduction == "maximum":
        additional = float(deficiency.max())
    else:
        if level is None:
            raise StatutoryError(
                "a CTE reduction needs a level; the maximum reduction is the "
                "one that needs no parameter"
            )
        additional = cte(deficiency, level)

    worst = int(np.argmax(deficiency))
    surplus = accumulated_surplus(net, rates, starting_assets)
    discounted = -surplus * path_discount_factors(rates)
    worst_date = int(np.argmax(discounted[:, worst]))
    return AdequacyResult(
        deficiency=deficiency, additional_reserve=additional,
        reduction=reduction, level=level, worst_scenario=worst,
        worst_date=worst_date,
    )
