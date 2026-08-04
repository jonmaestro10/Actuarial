"""Reference implementation of the VPLA system's actuarial core.

Transcribed by hand from ``jonmaestro10/VPLA`` at commit ``fe8b47f``
(``application/rate_table.py``, ``mortality_table.py``, ``person.py``,
``calculation_engine.py``). The structural review this came from is
docs/vpla-review.md; section numbers below refer to it. Those calculations
have been checked against Society of Actuaries calculators, so this file is
the specification the engine is held to, not the other way round.

This is reference model #1 from PLAN.md §3.2, so it is written the way a
reference model has to be written: **plain Python, no engine imports, no
NumPy, no vectorization**. One ``relativedelta`` call per period per policy,
exactly as the original. Where VPLA is O(n²) so is this. A reference that
shares machinery with the thing it checks is not a check, and a reference
optimized alongside it stops being a reference.

Three departures from the original, all deliberate:

- the S3 fetch inside a pydantic validator is gone; tables are passed in;
- the 2-decimal ``np.around`` inside the roll-forward is not reproduced —
  money rounding is an output policy, not projection arithmetic (§6.7);
- deceased-member handling in the pool step is expressed as an ``alive``
  flag rather than a date comparison.

Nothing else is changed, including the behaviours the review calls out as
defects: this file has to be wrong the same way VPLA is wrong, or it cannot
measure the difference.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from dateutil.relativedelta import relativedelta

# VPLA treats attained age 120 as certain death (mortality_table.py:223).
OMEGA = 120
# Every VPLA vector is sized at 120 years of payment periods.
HORIZON_YEARS = 120


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


class ReferenceMortalityTable:
    """Literal transcription of VPLA's ``MortalityTable``.

    ``rates`` is ``{sex: {age: q_x}}``. ``improvement`` is either
    ``{sex: {age: rate}}`` (constant scale) or ``{sex: {year: {age: rate}}}``
    (generational), matching the two shapes VPLA detects at runtime from the
    nesting depth of whatever it loaded.
    """

    def __init__(self, rates, *, year_start, improvement=None,
                 use_improvement=True, calc="udd", actual_daycount=True,
                 use_blended_rate=False, blended_male_percent=0.0):
        self.rates = rates
        self.year_start = year_start
        self.improvement = improvement or {}
        self.use_improvement = use_improvement
        self.calc = calc
        self.actual_daycount = actual_daycount
        self.use_blended_rate = use_blended_rate
        self.blended_male_percent = blended_male_percent
        self.max_age = max(max(by_age) for by_age in rates.values())

    def mortality_lookup(self, age: int, sex: str, year: int) -> float:
        """VPLA ``mortality_lookup``: hold the last tabulated age flat,
        optionally blend across sexes, then apply the improvement scale."""
        if age > self.max_age:
            age = self.max_age
        mortality = self.rates[sex][age]
        if self.use_blended_rate:
            mortality = (
                self.blended_male_percent * self.rates["M"][age]
                + (1 - self.blended_male_percent) * self.rates["F"][age]
            )
        if not self.use_improvement:
            return mortality
        scale = self.improvement[sex]
        if isinstance(next(iter(scale.values())), Mapping):
            # Generational: compound one calendar year at a time, holding the
            # last tabulated year flat.
            max_year = max(scale)
            factor = 1.0
            for calc_year in range(self.year_start + 1, year + 1):
                factor *= 1.0 - scale[min(calc_year, max_year)][age]
            return mortality * factor
        return mortality * (1.0 - scale[age]) ** (year - self.year_start)

    def mortality_period(self, dob: date, val_date: date, sex: str,
                         freq: int = 1) -> float:
        """VPLA ``mortality_period``: death probability over one payment
        period starting at ``val_date``."""
        first_age = relativedelta(val_date, dob).years
        second_age = relativedelta(
            val_date + relativedelta(months=12 // freq), dob
        ).years
        if first_age >= OMEGA:
            return 1.0
        current_bday = dob + relativedelta(years=first_age)
        next_bday = current_bday + relativedelta(years=1)
        next_next_bday = current_bday + relativedelta(years=2)
        next_val = val_date + relativedelta(months=12 // freq)

        days_in_period = (next_val - val_date).days
        days_in_year = (next_bday - current_bday).days
        days_in_next_year = (next_next_bday - next_bday).days
        start_in_year = (val_date - current_bday).days
        start_percent = start_in_year / days_in_year
        period_length = days_in_period / days_in_year

        mortality_first = self.mortality_lookup(first_age, sex, val_date.year)
        mortality_second = self.mortality_lookup(second_age, sex, val_date.year)

        if first_age == second_age:
            percent_first_age = period_length
            percent_second_age = 0.0
        else:
            percent_first_age = (days_in_year - start_in_year) / days_in_year
            percent_second_age = (next_val - next_bday).days / days_in_next_year

        if not self.actual_daycount:
            start_percent = round(start_percent * freq, 0) / freq
            percent_first_age = round(percent_first_age * freq, 0) / freq
            percent_second_age = round(percent_second_age * freq, 0) / freq

        if self.calc == "linear":
            return linear_period_mortality(
                percent_first_age, percent_second_age,
                mortality_first, mortality_second,
            )
        return udd_period_mortality(
            start_percent, percent_first_age, percent_second_age,
            mortality_first, mortality_second,
        )

    def survival_factors(self, dob: date, val_date: date, sex: str,
                         freq: int = 1, n_periods: int | None = None) -> list[float]:
        """VPLA ``survival_factors``: cumulative survival from the valuation
        date, one ``mortality_period`` call per period."""
        n = HORIZON_YEARS * freq if n_periods is None else n_periods
        survival = [1.0]
        for i in range(1, n):
            survival.append(
                survival[i - 1]
                * (
                    1.0
                    - self.mortality_period(
                        dob,
                        val_date + (i - 1) * (12 // freq) * relativedelta(months=1),
                        sex,
                        freq,
                    )
                )
            )
        return survival


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

    The original divides by ``freq`` **inside** the sum
    (``sum(np.multiply(df, sf) / freq)``) while ``joint_annuity_factor``
    divides once at the end. That inconsistency is reproduced rather than
    tidied: at ``freq = 12`` the two orders differ in the last bits, and a
    reference that quietly normalises them cannot measure the difference.
    """
    sf = list(sf)
    for k in range(min(certain_periods, len(sf))):
        sf[k] = 1.0
    return sum(df[k] * sf[k] / freq for k in range(len(df)))


def deferred_annuity_values(
    df: Sequence[float], sf: Sequence[float]
) -> list[float]:
    """VPLA ``Person.annuity_factors``: time-0 value of the payments from
    period ``k`` onward, for every ``k``. O(n²), as in the original. Not
    divided by ``freq`` — the caller does that once."""
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
    the transcription stays faithful; callers wanting a per-annum factor
    divide.
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

    The period's pension leaves the account before the fund return is
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
