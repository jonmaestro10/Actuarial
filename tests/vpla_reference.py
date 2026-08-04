"""Reference implementation of the VPLA system's actuarial core.

Ported by hand from ``jonmaestro10/VPLA`` at commit ``fe8b47f``
(``application/rate_table.py``, ``mortality_table.py``, ``person.py``,
``calculation_engine.py``). The structural review that produced this port is
docs/vpla-review.md; section numbers below refer to it.

This is reference model #1 from PLAN.md §3.2, so it is written the way a
reference model has to be written: **plain Python, no engine imports, no
NumPy**. A reference that shares machinery with the thing it checks is not a
check. It is O(n²) in places where VPLA is, deliberately — fidelity to the
original beats speed at n = 120.

Scope: annual payment frequency with the valuation date on the member's
birthday, which is where VPLA's fractional-age split collapses to the
tabular ``q_x`` (asserted, not assumed — see ``udd_period_mortality`` and
its test). VPLA's ``dateutil`` calendar plumbing is out of scope until the
engine grows a monthly time axis (review §7.2).

Two departures from the original, both flagged in review §6 and both
deliberate:

- the 2-decimal ``np.around`` inside VPLA's roll-forward is not reproduced —
  money rounding is an output policy, not projection arithmetic;
- deceased-member handling is omitted; every function here assumes live
  members, which is what the reconciliation tests exercise.
"""

from __future__ import annotations

from typing import Mapping, Sequence

# VPLA treats attained age 120 as certain death (mortality_table.py:223).
OMEGA = 120


# --- §3.1 rates and discounting: RateTable ---------------------------------


def expand_rates(rates: Sequence[float], freq: int, n_periods: int) -> list[float]:
    """VPLA ``RateTable.rates_list``.

    Each supplied annual effective rate covers ``freq`` periods; the last
    rate is held flat to the end of the horizon.
    """
    if not rates:
        raise ValueError("at least one rate is required")
    if 12 % freq:
        raise ValueError(f"freq={freq} does not divide 12")
    out: list[float] = []
    for rate in rates:
        out.extend([rate] * freq)
    out.extend([out[-1]] * (n_periods - len(out)))
    return out[:n_periods]


def discount_factors(
    rates: Sequence[float], freq: int, n_periods: int
) -> list[float]:
    """VPLA ``RateTable.discount_factors``: ``df[i] = df[i-1] * (1+r)**(-1/freq)``."""
    per_period = expand_rates(rates, freq, n_periods)
    df = [1.0]
    for i in range(1, n_periods):
        df.append(df[i - 1] * (1.0 + per_period[i - 1]) ** (-1.0 / freq))
    return df


# --- §3.2 mortality: MortalityTable ----------------------------------------


def udd_period_mortality(
    percent_before_first: float,
    percent_within_first: float,
    percent_second: float,
    mortality_first: float,
    mortality_second: float,
) -> float:
    """VPLA ``MortalityTable.udd_calc``.

    The first age's rate is re-based on survival to the start of the period,
    which is what makes this a conditional probability rather than a blend.
    On an exact anniversary at annual frequency the arguments are
    ``(0, 1, 0, q_x, q_{x+1})`` and the result is exactly ``q_x``.
    """
    survive_first = 1.0 - mortality_first * percent_before_first
    if survive_first == 0.0:
        return 1.0
    return (
        percent_within_first / survive_first * mortality_first
        + percent_second * mortality_second
    )


def linear_period_mortality(
    percent_first: float,
    percent_second: float,
    mortality_first: float,
    mortality_second: float,
) -> float:
    """VPLA ``MortalityTable.linear_calc``."""
    return percent_first * mortality_first + percent_second * mortality_second


def table_q(qx: Mapping[int, float], age: int) -> float:
    """VPLA ``mortality_lookup`` without improvement: hold the last tabulated
    age flat, and treat attained age >= 120 as certain death."""
    if age >= OMEGA:
        return 1.0
    return qx[min(age, max(qx))]


def period_mortality(
    qx: Mapping[int, float], age_at_valuation: int, n_periods: int
) -> list[float]:
    """Per-period death probabilities, annual frequency, valuation on the
    member's birthday — the case where ``udd_period_mortality`` collapses to
    the tabular rate."""
    return [table_q(qx, age_at_valuation + k) for k in range(n_periods)]


def survival_factors(q_period: Sequence[float]) -> list[float]:
    """VPLA ``MortalityTable.survival_factors``: ``sf[0] = 1``, cumulative
    product of ``(1 - q)`` thereafter."""
    sf = [1.0]
    for k in range(1, len(q_period)):
        sf.append(sf[k - 1] * (1.0 - q_period[k - 1]))
    return sf


# --- §3.3 annuity factors: Person ------------------------------------------


def annuity_factor(
    df: Sequence[float],
    sf: Sequence[float],
    freq: int,
    certain_periods: int = 0,
) -> float:
    """VPLA ``Person.annuity_factor``: ``Σ_k v_k · ₖp_x / freq``.

    A certain period overwrites survival with 1 for its first
    ``certain_periods`` entries, giving the life-and-certain factor.
    """
    sf = list(sf)
    for k in range(min(certain_periods, len(sf))):
        sf[k] = 1.0
    return sum(df[k] * sf[k] for k in range(len(df))) / freq


def deferred_annuity_values(
    df: Sequence[float], sf: Sequence[float]
) -> list[float]:
    """VPLA ``Person.annuity_factors``: time-0 value of the payments from
    period ``k`` onward, for every ``k``. Not divided by ``freq`` — the
    caller does that once (which is why the joint factor below matches)."""
    n = len(df)
    return [sum(df[j] * sf[j] for j in range(k, n)) for k in range(n)]


def joint_annuity_factor(
    df: Sequence[float],
    sf_x: Sequence[float],
    q_x: Sequence[float],
    sf_y: Sequence[float],
    joint_percent: float,
    freq: int,
) -> float:
    """VPLA ``Person.joint_annuity_factor`` — the reversionary annuity.

    Per period ``i``: the probability the primary survives to ``i-1`` and
    dies during that period, times the joint percentage, times the time-0
    value of the survivor's payments from ``i`` onward.
    """
    spouse_values = deferred_annuity_values(df, sf_y)
    n = len(df)
    survivor = sum(
        sf_x[i - 1] * q_x[i - 1] * joint_percent * spouse_values[i]
        for i in range(1, n)
    )
    single = sum(df[k] * sf_x[k] for k in range(n))
    return (single + survivor) / freq


def joint_life_factor(
    df: Sequence[float], sf_x: Sequence[float], sf_y: Sequence[float]
) -> float:
    """VPLA ``Person.joint_life_factor``: ``Σ_k v_k · ₖp_x · ₖp_y``.

    Note the original does *not* divide this one by ``freq`` — kept as-is so
    the port stays faithful; callers wanting a per-annum factor divide.
    """
    return sum(df[k] * sf_x[k] * sf_y[k] for k in range(len(df)))


def reversionary_closed_form(
    a_x: float, a_y: float, a_xy: float, joint_percent: float
) -> float:
    """The textbook identity review §3.3 derives from the loop above:
    ``ä_x + j · (ä_y - ä_xy)`` under independent lives."""
    return a_x + joint_percent * (a_y - a_xy)


# --- §4 product logic: CalcEngine ------------------------------------------


def roll_forward(
    account_value: float, pension: float, fund_return: float, contribution: float
) -> float:
    """VPLA ``update_values_no_valuation``, without the 2-dp rounding.

    The month's pension leaves the account before the fund return is
    credited; new money arrives after.
    """
    return (account_value - pension) * (1.0 + fund_return) + contribution


def pool_adjustment(
    retrospective: Sequence[float], prospective: Sequence[float]
) -> float:
    """VPLA ``update_values_valuation``: one pool-wide number,
    ``Σ retrospective / Σ prospective - 1``.

    This is the reduction across the model-point axis that the ``@var`` DSL
    cannot currently express (review §7.1).
    """
    return sum(retrospective) / sum(prospective) - 1.0


def valuation_step(
    account_values: Sequence[float],
    pensions: Sequence[float],
    annuity_factors: Sequence[float],
    contributions: Sequence[float],
    fund_return: float,
    freq: int,
    alive: Sequence[bool] | None = None,
) -> dict[str, list[float] | float]:
    """One full VPLA valuation-date step over a pool.

    ``pensions`` are per payment period (VPLA's ``monthly_pay``);
    ``annuity_factors`` are the members' factors *at this valuation*, i.e.
    already advanced to their new age. Returns the retrospective and
    prospective account values, the pool adjustment, and the
    post-adjustment pensions and account values.

    ``alive`` mirrors VPLA's death handling: a member who died during the
    period keeps a rolled-forward **retrospective** value — which is what
    puts the mortality release into the pool — but has zero prospective
    value, zero pension and zero account value afterwards.

    Faithful to the original, *including* the contribution asymmetry of
    review §6.8: new money sits in the adjustment's denominator but is added
    to the new pension unadjusted.
    """
    if alive is None:
        alive = [True] * len(account_values)
    retrospective = [
        roll_forward(av, p, fund_return, c)
        for av, p, c in zip(account_values, pensions, contributions)
    ]
    # VPLA builds `prospective` from the pension *after* new money has been
    # converted at the member's own factor.
    converted = [c / af / freq for c, af in zip(contributions, annuity_factors)]
    prospective = [
        (p + conv) * af * freq * live
        for p, conv, af, live in zip(pensions, converted, annuity_factors, alive)
    ]
    adjustment = pool_adjustment(retrospective, prospective)
    new_pensions = [
        ((1.0 + adjustment) * p + conv) * live
        for p, conv, live in zip(pensions, converted, alive)
    ]
    new_account_values = [
        p * af * freq for p, af in zip(new_pensions, annuity_factors)
    ]
    return {
        "retrospective": retrospective,
        "prospective": prospective,
        "adjustment": adjustment,
        "pensions": new_pensions,
        "account_values": new_account_values,
    }
