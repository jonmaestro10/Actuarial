"""Actual against expected: analysis of surplus, and where a variance lands.

Every reporting overlay in this library names the same thing as its own
open edge. RFC-012: "**Experience variance.** Everything here is expected
against expected, so revenue and expenses cancel on claims and the service
result is exactly the two margins unwinding. Splitting actual from expected
is what turns this into a reporting run rather than a projection of one."
RFC-015, RFC-017 and RFC-023 repeat it.

This is that split, and it has two halves that are usually run together and
should not be.

The first half is arithmetic, and it does not have one answer
------------------------------------------------------------
A book was expected to make X and made Y. Attributing ``Y − X`` to
mortality, lapses, interest and expenses is not a calculation with a right
answer, because **the effects interact**. Heavier mortality on a book that
also lapsed less is not the sum of the two effects measured separately;
there is a cross term, and somebody has to own it.

Three ways to divide it, and the differences are not small:

``sequential``
    Peel the drivers off one at a time in a chosen order. Adds up exactly,
    and **the answer depends on the order** — each driver is measured in
    the presence of the ones before it and the absence of the ones after.

``isolated``
    Measure each driver alone against the base. Order-independent, and
    **does not add up**: what is left over is the interaction, reported as
    a residual rather than pushed into whichever line was measured last.

``shapley``
    The average of a driver's marginal contribution over *every* order.
    Order-independent **and** exact — the two properties the other methods
    have one each of. It is the unique allocation with them, which is a
    theorem rather than a preference.

The engine does not choose. :func:`sequential` reports the order it used,
:func:`isolated` reports its residual, and :func:`contribution_range` says
how much the order was worth — because an analysis of surplus quoted
without that is an opinion presented as a measurement.

The second half is classification, and it is where the profit is decided
------------------------------------------------------------------------
Under IFRS 17 the same adverse variance goes to two completely different
places depending on which service it relates to:

- a variance on **current or past service** — claims incurred, expenses
  paid — goes straight to profit or loss;
- a variance relating to **future service** — §B96(a), and §B97(a) for
  premiums received relating to future coverage — adjusts the **CSM**, and
  never appears in the result at all.

So an adverse £100 is either a £100 hit to this year's profit or a £100
reduction in a margin that unwinds over decades. The standard says which
category a thing belongs to; it does not say how to tell an *experience*
variance from a *change in estimate* on the same number, and that judgement
moves profit between years without moving a single cashflow.
:func:`allocate` makes the split explicit and refuses to guess at it.
"""

from __future__ import annotations

import copy
from itertools import combinations
from math import factorial

import numpy as np

#: How the interaction between drivers is divided.
ALLOCATIONS = ("sequential", "isolated", "shapley")

#: Which service period a variance relates to, and so where it lands.
#: IFRS 17 §B96(a) and §B97(a).
SERVICE_PERIODS = ("current", "future")

#: Above this many drivers the exhaustive methods stop being reasonable:
#: they evaluate every subset, and every evaluation is a full projection.
MAX_DRIVERS = 12

#: Assumption fields that cannot move on their own. ``Assumptions`` stores
#: the lapse rate twice — as ``lapse`` and inside ``dynamic_lapse`` — and
#: different templates read different ones: ``TermLife`` takes
#: ``periodic_lapse()`` off the scalar, ``UnitLinked`` takes
#: ``dynamic_lapse.rate(...)``. Moving one and not the other gives a run
#: that is on the actual basis for some products and the expected basis for
#: others, silently. :class:`engine.report.solvency2.Stress` handles the
#: same coupling explicitly; this is the same list, in the one place a
#: driver swap can consult it.
COUPLED_FIELDS = {
    "lapse": ("lapse", "dynamic_lapse"),
    "dynamic_lapse": ("lapse", "dynamic_lapse"),
    "expenses": ("expenses", "expense_per_policy"),
    "expense_per_policy": ("expenses", "expense_per_policy"),
}


class Decomposition:
    """What each driver contributed, and what the method left over."""

    def __init__(self, contributions: dict, total: float, method: str,
                 order: tuple | None = None):
        self.contributions = dict(contributions)
        self.total = float(total)
        self.method = method
        self.order = order

    @property
    def explained(self) -> float:
        return float(sum(self.contributions.values()))

    @property
    def residual(self) -> float:
        """The interaction the method could not attribute.

        Zero by construction for ``sequential`` and ``shapley``; the whole
        point of the ``isolated`` method that it is not.
        """
        return self.total - self.explained

    def reconciles(self, tolerance: float = 1e-9) -> bool:
        scale = max(1.0, abs(self.total))
        return abs(self.residual) <= tolerance * scale

    def __repr__(self) -> str:
        return (f"Decomposition({self.method}, total={self.total:,.2f}, "
                f"residual={self.residual:,.2f})")


def cached(evaluate):
    """Memoise an evaluator over subsets of drivers.

    Every method here evaluates the same subsets repeatedly and each
    evaluation is a full projection, so this is not an optimisation but the
    difference between ``2**n`` runs and ``n! · n`` of them.
    """
    memo: dict = {}

    def wrapped(active):
        key = frozenset(active)
        if key not in memo:
            memo[key] = float(evaluate(key))
        return memo[key]

    wrapped.memo = memo
    return wrapped


def _checked(drivers) -> tuple:
    drivers = tuple(drivers)
    if len(set(drivers)) != len(drivers):
        raise ValueError("driver names must be distinct")
    if not drivers:
        raise ValueError("an analysis of surplus needs at least one driver")
    if len(drivers) > MAX_DRIVERS:
        raise ValueError(
            f"{len(drivers)} drivers needs {2 ** len(drivers)} projections; "
            f"this module stops at {MAX_DRIVERS}. Group the drivers, or run "
            "a sequential analysis, which needs only one per driver"
        )
    return drivers


def sequential(evaluate, order) -> Decomposition:
    """Peel the drivers off one at a time, in ``order``.

    Adds up exactly, and each driver is measured **in the presence of the
    ones before it**. The commonest analysis of surplus, and the one whose
    answer is a function of a decision nobody records.
    """
    order = _checked(order)
    evaluate = cached(evaluate)
    base = evaluate(frozenset())
    contributions, active = {}, set()
    previous = base
    for name in order:
        active.add(name)
        current = evaluate(frozenset(active))
        contributions[name] = current - previous
        previous = current
    return Decomposition(contributions, previous - base, "sequential",
                         order=order)


def isolated(evaluate, drivers) -> Decomposition:
    """Measure each driver alone against the base.

    Order-independent, and **does not add up**. The gap is the interaction
    between the drivers, and reporting it as a residual is the honest
    treatment: pushing it into the line that happened to be measured last
    is what a sequential analysis does without saying so.
    """
    drivers = _checked(drivers)
    evaluate = cached(evaluate)
    base = evaluate(frozenset())
    contributions = {name: evaluate(frozenset([name])) - base
                     for name in drivers}
    return Decomposition(contributions, evaluate(frozenset(drivers)) - base,
                         "isolated")


def shapley(evaluate, drivers) -> Decomposition:
    """The average marginal contribution over every order.

    Order-independent **and** exact. Those are the two properties the other
    two methods have one each of, and the Shapley value is the unique
    allocation with both — plus symmetry (two drivers that always
    contribute the same get the same) and the null property (a driver that
    never changes anything gets nothing).

    Costs ``2**n`` evaluations rather than ``n``, which is the price of not
    having to defend an ordering.
    """
    drivers = _checked(drivers)
    evaluate = cached(evaluate)
    n = len(drivers)
    contributions = {}
    for name in drivers:
        others = [d for d in drivers if d != name]
        total = 0.0
        for size in range(n):
            weight = factorial(size) * factorial(n - size - 1) / factorial(n)
            for subset in combinations(others, size):
                before = frozenset(subset)
                total += weight * (evaluate(before | {name})
                                   - evaluate(before))
        contributions[name] = total
    base = evaluate(frozenset())
    return Decomposition(contributions, evaluate(frozenset(drivers)) - base,
                         "shapley")


def contribution_range(evaluate, drivers) -> dict:
    """``{driver: (lowest, highest)}`` across every possible ordering.

    A driver's contribution under a sequential analysis depends only on the
    **set** of drivers peeled off before it, and every subset is reachable
    as some ordering's prefix — so this is the exact range over all ``n!``
    orderings at the cost of ``2**n`` evaluations rather than ``n!·n``.

    The width is what the ordering decision was worth. An analysis of
    surplus quoted without it is an opinion presented as a measurement.
    """
    drivers = _checked(drivers)
    evaluate = cached(evaluate)
    spans = {}
    for name in drivers:
        others = [d for d in drivers if d != name]
        marginals = []
        for size in range(len(others) + 1):
            for subset in combinations(others, size):
                before = frozenset(subset)
                marginals.append(evaluate(before | {name}) - evaluate(before))
        spans[name] = (min(marginals), max(marginals))
    return spans


def order_sensitivity(evaluate, drivers) -> dict:
    """The width of each driver's range, as a share of its Shapley value.

    Reported relative because the absolute width of a small driver's range
    says nothing on its own. A driver whose sensitivity exceeds 1 is one
    whose attributed surplus is decided more by the ordering than by the
    experience.
    """
    spans = contribution_range(evaluate, drivers)
    values = shapley(evaluate, drivers).contributions
    sensitivity = {}
    for name, (low, high) in spans.items():
        value = values[name]
        sensitivity[name] = (high - low) / abs(value) if value else float("inf")
    return sensitivity


def swap(expected, actual, fields):
    """Build an evaluator that takes ``fields`` from ``actual``.

    ``expected`` and ``actual`` are two assumption sets. The returned
    callable takes a set of field names and returns an assumption set with
    exactly those taken from ``actual`` and everything else from
    ``expected`` — a shallow copy, so a driver cannot quietly change
    something it was not asked to, which is the same discipline
    :class:`engine.report.solvency2.Stress` works under.

    A driver named in :data:`COUPLED_FIELDS` moves its whole group. The
    lapse rate is stored twice and different templates read different
    copies, so swapping one alone produces a run that is on the actual
    basis for some products and the expected basis for others — with
    nothing to show for it in any output.
    """
    fields = tuple(fields)
    for name in fields:
        if not hasattr(expected, name) or not hasattr(actual, name):
            raise ValueError(
                f"{name!r} is not an attribute of both assumption sets; a "
                "driver that does not exist would silently contribute zero"
            )

    def build(active):
        active = set(active)
        unknown = active - set(fields)
        if unknown:
            raise ValueError(f"{sorted(unknown)} are not among {fields}")
        basis = copy.copy(expected)
        for name in active:
            for field in COUPLED_FIELDS.get(name, (name,)):
                if hasattr(actual, field):
                    setattr(basis, field, getattr(actual, field))
        return basis

    return build


class Attribution:
    """Where each variance lands under IFRS 17, and why."""

    def __init__(self, variances: dict, service: dict):
        unknown = set(variances) - set(service)
        if unknown:
            raise ValueError(
                f"{sorted(unknown)} have no service period; a variance "
                "without one cannot be placed, and defaulting it would put "
                "it in profit or in the CSM by accident"
            )
        bad = {k: v for k, v in service.items() if v not in SERVICE_PERIODS}
        if bad:
            raise ValueError(
                f"service period must be one of {SERVICE_PERIODS}, got {bad}"
            )
        self.variances = dict(variances)
        self.service = dict(service)

    @property
    def profit_or_loss(self) -> float:
        """Current and past service — recognised immediately."""
        return float(sum(v for k, v in self.variances.items()
                         if self.service[k] == "current"))

    @property
    def csm_adjustment(self) -> float:
        """Future service — §B96(a), absorbed by the margin instead."""
        return float(sum(v for k, v in self.variances.items()
                         if self.service[k] == "future"))

    @property
    def total(self) -> float:
        return self.profit_or_loss + self.csm_adjustment

    def reclassified(self, name: str, period: str) -> "Attribution":
        """The same variances with one line moved between service periods.

        Provided so the judgement can be *measured* rather than argued
        about: the standard says where each category goes and says nothing
        about how to tell an experience variance from a change in estimate
        on the same number.
        """
        service = dict(self.service)
        service[name] = period
        return Attribution(self.variances, service)

    def __repr__(self) -> str:
        return (f"Attribution(P&L={self.profit_or_loss:,.2f}, "
                f"CSM={self.csm_adjustment:,.2f})")


def allocate(variances: dict, service: dict) -> Attribution:
    """Split variances between profit or loss and the CSM.

    Refuses to place a variance whose service period is not stated, because
    a default here is a decision about profit dressed up as a convenience.
    """
    return Attribution(variances, service)


def cross_terms(evaluate, drivers) -> float:
    """How much of the total is interaction rather than any single driver.

    The residual an isolated analysis leaves. It is second order in the
    size of the variances, so it is negligible on a quiet year and is not
    on the year anybody wants the analysis for.
    """
    return isolated(evaluate, drivers).residual


def to_table(decomposition: Decomposition) -> np.ndarray:
    """Contributions in the order they were reported, as an array."""
    names = (decomposition.order if decomposition.order is not None
             else tuple(decomposition.contributions))
    return np.array([decomposition.contributions[n] for n in names],
                    dtype=np.float64)
