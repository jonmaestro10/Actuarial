"""Policy reserves — net premium, gross premium, and the modified bases.

PLAN.md §5.2's first bullet asks for "**Term / whole life / endowment** (net
& gross premium reserves)", and the reserves half was the gap. Every
template in this library projects *cashflows*; every overlay in
``engine/report`` then builds its own liability out of them. A policy
reserve is the older and more direct object — the value of the future
benefits less the value of the future premiums, on a stated basis — and it
is what the traditional families are actually valued on.

Everything here is one of two expressions
-----------------------------------------
    prospective:   V_t = A_{x+t}          - P · ä_{x+t}
    retrospective: V_t = P · s_accum(t)   - A_accum(t)

and the first structural fact about them is that **they are equal**. Not
approximately: the same number, to the last bits, whenever the reserve is
valued on the basis the premium was calculated on. That is not a
coincidence to be verified once and forgotten — it is what makes a reserve
well defined, and the moment the two bases differ it stops being true.
:func:`prospective_reserve` and :func:`retrospective_reserve` are both here
so that the identity can be asserted rather than assumed.

What the net premium reserve leaves out
---------------------------------------
**Expenses.** The net premium is solved so the premiums exactly fund the
benefits, which means it funds nothing else — and a policy sold with
acquisition costs has spent real money by the end of its first year that no
part of the net premium was ever going to recover.

So the net premium reserve is nil at issue while the *gross* premium
reserve is **negative** there, by roughly the acquisition cost. A statutory
regime that requires a non-negative reserve on the net premium basis is
therefore requiring the office to find that money from its own capital in
the first year, which is the new business strain, and the modified reserve
bases exist to reduce it:

- :func:`zillmerised_reserve` reduces the reserve by a decreasing
  proportion of an assumed acquisition loading.
- :func:`full_preliminary_term` is the limiting case — treat the first year
  as a one-year term assurance and start the accumulation a year late,
  which sets the first-year reserve to exactly zero.

Both are *presentations* of the same contract, and neither changes a
cashflow. What they change is when the office must hold capital, which is
measured in tests/test_reserves.py rather than described here.

Shape and convention
--------------------
Rates are annual and keyed by integer age, matching
:class:`~engine.data.assumptions.MortalityTable`. Benefits are paid at the
**end** of the year of death; premiums at the **start** of the year. Arrays
run over durations ``0 .. term``, so a reserve series has ``term + 1``
entries and the last one is the maturity value.
"""

from __future__ import annotations

import numpy as np

#: Products this module knows how to solve a net premium for. Each is a
#: benefit shape rather than a separate calculation — the machinery below
#: takes the factors and does not care which shape produced them.
PRODUCTS = ("term", "endowment", "whole_life", "pure_endowment")


def _rates(basis, age: int, periods: int) -> np.ndarray:
    """Annual ``q`` from ``age`` for ``periods`` years, clipped into table."""
    ages = np.arange(age, age + periods)
    return np.asarray(basis.q_at(basis.clip_age(ages)), dtype=np.float64)


def survival(basis, age: int, periods: int) -> np.ndarray:
    """``ₖp_x`` for ``k = 0 .. periods``, so ``periods + 1`` entries.

    Entry 0 is exactly 1.0 by construction rather than by arithmetic — a
    life aged ``x`` has certainly reached age ``x``.
    """
    q = _rates(basis, age, periods)
    curve = np.empty(periods + 1, dtype=np.float64)
    curve[0] = 1.0
    np.cumprod(1.0 - q, out=curve[1:])
    return curve


def discount(rate: float, periods: int) -> np.ndarray:
    """``v^k`` for ``k = 0 .. periods``."""
    return (1.0 + rate) ** -np.arange(periods + 1, dtype=np.float64)


def annuity_due(basis, age: int, term: int, rate: float) -> np.ndarray:
    """``ä_{x+t:n-t}`` for every duration ``t``, one entry per ``0..term``.

    The value, per survivor at ``t``, of a premium of 1 payable at the start
    of each remaining year. Zero at ``t == term``: a contract that has
    matured collects nothing more.
    """
    v, p = discount(rate, term), survival(basis, age, term)
    terms = (v * p)[:term]
    tail = np.concatenate([np.cumsum(terms[::-1])[::-1], [0.0]])
    return _rebase(tail, v, p)


def assurance(basis, age: int, term: int, rate: float) -> np.ndarray:
    """``A^1_{x+t:n-t}`` — the value of 1 payable at the end of the year of
    death, if death happens inside the term. One entry per duration.
    """
    v, p = discount(rate, term), survival(basis, age, term)
    q = _rates(basis, age, term)
    terms = v[1:] * p[:term] * q
    tail = np.concatenate([np.cumsum(terms[::-1])[::-1], [0.0]])
    return _rebase(tail, v, p)


def pure_endowment(basis, age: int, term: int, rate: float) -> np.ndarray:
    """``ₙ₋ₜE_{x+t}`` — the value of 1 payable at the end of the term if the
    life survives to it. One entry per duration, and exactly 1.0 at
    ``t == term``."""
    v, p = discount(rate, term), survival(basis, age, term)
    tail = np.full(term + 1, v[term] * p[term], dtype=np.float64)
    return _rebase(tail, v, p)


def _rebase(tail: np.ndarray, v: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Re-base a time-zero value onto a survivor at each duration.

    ``value_t = tail_t / (v_t · ₜp_x)``. Where survival has reached exactly
    zero the factor is zero rather than infinite: a duration nobody reaches
    has no value per survivor, and dividing straight through is the bug
    ``prospective_annuity_factors`` already fixed once in this library.
    """
    reachable = v * p
    return np.divide(tail, reachable, out=np.zeros_like(tail),
                     where=reachable > 0.0)


def benefit_value(basis, age: int, term: int, rate: float, *,
                  product: str = "term",
                  sum_assured: float = 1.0,
                  maturity: float | None = None) -> np.ndarray:
    """Value of the benefits of one of :data:`PRODUCTS`, per duration.

    ``endowment`` is a term assurance **plus** a pure endowment, and it is
    written that way rather than given its own formula — the identity is
    the definition of the product, and a separate implementation would be a
    second chance to get it wrong.

    ``whole_life`` is a term assurance run to the end of the table, so it
    needs no separate branch either; the caller supplies the term.
    """
    if product not in PRODUCTS:
        raise ValueError(f"product must be one of {PRODUCTS}, got {product!r}")
    if product == "pure_endowment":
        return sum_assured * pure_endowment(basis, age, term, rate)
    death = sum_assured * assurance(basis, age, term, rate)
    if product == "endowment":
        payable = sum_assured if maturity is None else maturity
        return death + payable * pure_endowment(basis, age, term, rate)
    return death


def net_premium(basis, age: int, term: int, rate: float, *,
                product: str = "term", sum_assured: float = 1.0,
                maturity: float | None = None,
                premium_term: int | None = None) -> float:
    """The level premium whose value exactly equals the benefits'.

    ``premium_term`` shortens the paying period — a limited-payment whole
    life, say — which raises the premium without touching the benefit.
    """
    benefits = benefit_value(basis, age, term, rate, product=product,
                             sum_assured=sum_assured, maturity=maturity)[0]
    paying = term if premium_term is None else premium_term
    if paying < 1:
        raise ValueError(f"premium term {paying} pays nothing")
    factor = annuity_due(basis, age, paying, rate)[0]
    if factor <= 0.0:
        raise ValueError(
            "the annuity factor is zero, so no level premium can fund this "
            "benefit; check the age against the end of the table"
        )
    return float(benefits / factor)


def prospective_reserve(basis, age: int, term: int, rate: float, *,
                        premium: float, product: str = "term",
                        sum_assured: float = 1.0,
                        maturity: float | None = None,
                        premium_term: int | None = None) -> np.ndarray:
    """``V_t = benefits_t − premium · ä_t``, one entry per duration.

    Looking forward: what the office still owes, less what it will still
    collect. This is the definition a valuation actually uses, because it
    needs no history.
    """
    benefits = benefit_value(basis, age, term, rate, product=product,
                             sum_assured=sum_assured, maturity=maturity)
    paying = term if premium_term is None else premium_term
    factor = _padded_annuity(basis, age, term, rate, paying)
    return benefits - premium * factor


def retrospective_reserve(basis, age: int, term: int, rate: float, *,
                          premium: float, product: str = "term",
                          sum_assured: float = 1.0,
                          maturity: float | None = None,
                          premium_term: int | None = None) -> np.ndarray:
    """``V_t = premium · accumulated premiums − accumulated benefits``.

    Looking back: what has been collected and earned, less what has been
    paid out, per survivor. Equal to the prospective reserve **exactly**
    whenever the two are valued on the basis the premium was solved on —
    which is asserted in the tests rather than trusted here.
    """
    v, p = discount(rate, term), survival(basis, age, term)
    q = _rates(basis, age, term)
    paying = term if premium_term is None else premium_term

    paid = np.zeros(term + 1)
    paid[:paying] = 1.0
    premium_terms = np.concatenate([[0.0], np.cumsum((v * p * paid)[:term])])

    # Only benefits **already paid** enter a retrospective accumulation. A
    # maturity falls due *at* duration ``term``, so it is not among them —
    # subtracting it there made the endowment's closing reserve zero where
    # the prospective one correctly holds the sum assured, and the two
    # definitions disagreed by exactly the sum assured.
    death = np.concatenate([[0.0], np.cumsum(v[1:] * p[:term] * q)])
    benefits = np.zeros(term + 1) if product == "pure_endowment" \
        else sum_assured * death

    return _rebase(premium * premium_terms - benefits, v, p)


def _padded_annuity(basis, age: int, term: int, rate: float,
                    paying: int) -> np.ndarray:
    """Annuity factors over the whole term, zero once premiums have ceased."""
    if paying >= term:
        return annuity_due(basis, age, term, rate)
    short = annuity_due(basis, age, paying, rate)
    return np.concatenate([short[:paying], np.zeros(term + 1 - paying)])


def reserve_recursion_residual(basis, age: int, term: int, rate: float, *,
                               reserve: np.ndarray, premium: float,
                               sum_assured: float = 1.0,
                               premium_term: int | None = None
                               ) -> np.ndarray:
    """How far the reserve series misses its own recursion, per year.

        (V_t + P_t)(1 + i) = q_{x+t} · S + p_{x+t} · V_{t+1}

    The left side is what the office holds after the year's premium and a
    year's interest; the right is what it needs — the claims it expects to
    pay and the reserve it must still hold for the survivors. A reserve
    that satisfies this is *self-financing*, which is the whole point of
    one, and the residual is the direct check that it does.
    """
    q = _rates(basis, age, term)
    paying = term if premium_term is None else premium_term
    paid = np.zeros(term)
    paid[:paying] = premium
    held = (reserve[:term] + paid) * (1.0 + rate)
    needed = q * sum_assured + (1.0 - q) * reserve[1:]
    return held - needed


def zillmerised_reserve(reserve: np.ndarray, *, zillmer: float,
                        annuity: np.ndarray, term: int) -> np.ndarray:
    """The net premium reserve less a running-off acquisition allowance.

    ``V_t − zillmer · ä_{x+t} / ä_x``: the allowance is written off in
    proportion to the premiums still to come, so it is largest at issue and
    exactly nothing at maturity. The reserve is *not* floored at zero here
    — a Zillmerised reserve is allowed to be negative and a regime that
    forbids it says so separately.
    """
    if zillmer < 0.0:
        raise ValueError(f"Zillmer allowance {zillmer} is negative")
    if annuity[0] <= 0.0:
        raise ValueError("the annuity factor at issue is zero")
    return reserve - zillmer * annuity / annuity[0]


def full_preliminary_term(basis, age: int, term: int, rate: float, *,
                          product: str = "term", sum_assured: float = 1.0,
                          maturity: float | None = None) -> np.ndarray:
    """The limiting modified reserve: **exactly zero at the end of year one**.

    The first year is treated as a one-year term assurance funded by a
    one-year premium, and the reserve for the remainder is the net premium
    reserve of a contract issued a year later at age ``x + 1``. Nothing is
    approximated — the first-year reserve is zero by construction, not by
    being small.

    The whole first year's acquisition cost is therefore permitted, which
    is why this basis is the most generous of the three and why regimes
    that allow it cap it.
    """
    if term < 2:
        raise ValueError(
            f"full preliminary term needs at least two years, got {term}"
        )
    later = prospective_reserve(
        basis, age + 1, term - 1, rate,
        premium=net_premium(basis, age + 1, term - 1, rate, product=product,
                            sum_assured=sum_assured, maturity=maturity),
        product=product, sum_assured=sum_assured, maturity=maturity,
    )
    return np.concatenate([[0.0], later])


def gross_premium_reserve(basis, age: int, term: int, rate: float, *,
                          premium: float, product: str = "term",
                          sum_assured: float = 1.0,
                          maturity: float | None = None,
                          initial_expense: float = 0.0,
                          renewal_expense: float = 0.0,
                          claim_expense: float = 0.0,
                          premium_term: int | None = None) -> np.ndarray:
    """Benefits **and expenses**, less the office premium actually charged.

    The reserve a modern valuation holds, and the one that tells the truth
    about the first year: the acquisition cost is spent at issue and no
    part of the premium was ever earmarked to recover it, so the reserve at
    issue is **negative** by about that amount.

    ``initial_expense`` is incurred at issue; ``renewal_expense`` at the
    start of every year premiums are paid; ``claim_expense`` alongside each
    death benefit.
    """
    benefits = benefit_value(basis, age, term, rate, product=product,
                             sum_assured=sum_assured + claim_expense,
                             maturity=maturity)
    if product == "endowment" and claim_expense:
        # A maturity is not a claim in the sense the loading covers, so the
        # extra rides on the death benefit only.
        benefits = benefits - claim_expense * pure_endowment(
            basis, age, term, rate)
    paying = term if premium_term is None else premium_term
    factor = _padded_annuity(basis, age, term, rate, paying)
    reserve = benefits + renewal_expense * factor - premium * factor
    reserve = reserve.copy()
    reserve[0] += initial_expense
    return reserve
