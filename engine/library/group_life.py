"""Group life — one contract, many lives, and a refund that is an option.

The other half of PLAN.md §5.2's last line. A group scheme is a single
policy written over an employer's workforce: yearly renewable term, cover
set as a multiple of salary, a unit rate per mille reviewed at the end of
each rating period, and members joining and leaving with the payroll.

Two things distinguish it from a block of individual policies, and only one
of them is obvious.

**Cover follows salary, not a sum assured.** ``sum_assured`` is a ``@var``
in ``t`` — salary times multiple times escalation — so the amount at risk
grows every year without anybody underwriting it again. A scheme's claims
inflate even when its membership does not.

**The experience refund is a written option, and this is the finding.**
A profit-sharing scheme returns a share of any surplus at the end of each
rating period and charges nothing back when the experience is bad. That
asymmetry is not a detail of the wording; it is the whole economics:

    deterministic cost  =  share × max( E[surplus], 0 )
    actual cost         =  share × E[ max(surplus, 0) ]

and the second is strictly larger whenever the surplus is uncertain. It is
the same shape RFC-010 found under a crediting floor and RFC-020 gave a
line in the accounts as the time value of guarantees, arriving here through
mortality rather than through markets. A deterministic projection of a
profit-sharing scheme is wrong in a known direction, always.

The gap has a closed form here rather than a simulation. Claims on ``n``
lives of equal cover are ``S × Binomial(n, q)``, so the expectation is a
finite sum over the death count and :func:`refund_option_value` computes it
exactly — no scenarios, no seed, no Monte Carlo error.

**Which means small schemes are the expensive ones.** The option is worth
more where the claims ratio is more volatile, and that is precisely the
scheme whose own experience is least credible. The schemes with the
weakest case for experience rating are the ones where granting it costs the
most.

Model point fields: ``age_at_entry`` (int), ``salary``, ``salary_multiple``,
``unit_rate`` (annual premium per mille of sum assured), ``init_pols``
(lives represented), and optionally ``salary_escalation``.

Assumption bindings: ``mortality``, ``lapse`` (withdrawal from the scheme),
``interest``, ``expenses``.

``profit_share``, ``rating_period``, ``retained_margin`` and
``terminal_age`` are class attributes rather than assumptions: they are
terms the scheme was sold on, decided by the two parties, not estimates
handed to the actuary. Same typing as
:class:`~engine.library.with_profits.WithProfitsEndowment`'s bonus basis
and for the same reason.
"""

from __future__ import annotations

import math

import numpy as np

from engine.core.model import Model, pool, var


def binomial_pmf(lives: int, q: float) -> np.ndarray:
    """``P(D = d)`` for ``d = 0 .. lives``, computed in log space.

    Exact to floating point on scheme sizes that overflow a direct binomial
    coefficient, which starts well below the size of a real payroll.
    """
    if lives < 0:
        raise ValueError(f"a scheme of {lives} lives is not a scheme")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"mortality rate {q} is outside [0, 1]")
    deaths = np.arange(lives + 1)
    if q == 0.0:
        pmf = np.zeros(lives + 1)
        pmf[0] = 1.0
        return pmf
    if q == 1.0:
        pmf = np.zeros(lives + 1)
        pmf[lives] = 1.0
        return pmf
    log_choose = np.array([
        math.lgamma(lives + 1) - math.lgamma(d + 1) - math.lgamma(lives - d + 1)
        for d in range(lives + 1)
    ])
    log_pmf = (log_choose + deaths * math.log(q)
               + (lives - deaths) * math.log1p(-q))
    return np.exp(log_pmf)


def deterministic_refund(net_premium: float, sum_assured: float, lives: int,
                         q: float, share: float) -> float:
    """What a best-estimate projection says the refund costs.

    ``share × max(net premium − expected claims, 0)``. One number, and the
    floor is applied to the *mean* — which is the error.
    """
    expected_claims = sum_assured * lives * q
    return float(share * max(net_premium - expected_claims, 0.0))


def expected_refund(net_premium: float, sum_assured: float, lives: int,
                    q: float, share: float) -> float:
    """What the refund actually costs, over the distribution of deaths.

    ``share × E[max(net premium − S·D, 0)]`` with ``D ~ Binomial(n, q)``,
    summed exactly rather than sampled.
    """
    pmf = binomial_pmf(lives, q)
    surplus = net_premium - sum_assured * np.arange(lives + 1)
    return float(share * (pmf * np.maximum(surplus, 0.0)).sum())


def refund_option_value(net_premium: float, sum_assured: float, lives: int,
                        q: float, share: float) -> float:
    """The gap between the two. Never negative, and zero only at ``q = 0``.

    Jensen's inequality on ``max(·, 0)``, which is convex. There is no
    calibration under which a deterministic projection of a profit-sharing
    scheme is right, and the size of the error is what this returns.
    """
    return (expected_refund(net_premium, sum_assured, lives, q, share)
            - deterministic_refund(net_premium, sum_assured, lives, q, share))


class GroupLife(Model):
    """A yearly renewable group scheme with an experience refund."""

    #: Share of the rating period's surplus returned to the employer.
    profit_share = 0.50

    #: Years between surplus strikes. The refund falls in the last period
    #: of each one.
    rating_period = 3

    #: Fraction of premium the insurer keeps out of the surplus pot — its
    #: charge for carrying the risk, and the reason a scheme running at
    #: exactly its expected claims still generates no refund.
    retained_margin = 0.10

    #: Age at which cover ceases under the scheme rules.
    terminal_age = 65

    def _field(self, name, default):
        return getattr(self.mp, name, default) * 1.0

    # --- membership ------------------------------------------------------

    @var
    def age(self, t):
        return self.mp.age_at_entry + self.assumptions.years_elapsed(t)

    @var
    def in_cover(self, t):
        """1 while the member is under the scheme's terminal age."""
        return (self.age(t) < self.terminal_age) * 1.0

    @var
    def sum_assured(self, t):
        """Salary times multiple, escalated — the cover in force.

        Recomputed every year rather than fixed at entry, which is what
        makes a scheme's exposure grow without a single new member and
        without any underwriting.
        """
        escalation = self._field("salary_escalation", 0.0)
        years = self.assumptions.years_elapsed(t)
        return (self.mp.salary * self.mp.salary_multiple
                * (1.0 + escalation) ** years * self.in_cover(t))

    @var(assumption="mortality")
    def q_x(self, t):
        return self.assumptions.periodic_q(
            self.age(t), t, sex=getattr(self.mp, "sex", None)
        ) * self.in_cover(t)

    @var(assumption="lapse")
    def withdrawal_rate(self, t):
        """Leaving the scheme — resignation, retirement, redundancy.

        Bound to ``lapse`` because it is the same decrement, and named for
        what it is because nobody surrenders a group certificate.
        """
        return self.assumptions.periodic_lapse() * self.in_cover(t)

    def _split(self, t):
        return self.assumptions.decrements.split(
            self.lives_if(t),
            {"mortality": self.q_x(t), "lapse": self.withdrawal_rate(t)},
        )

    @var
    def lives_if(self, t):
        if t == 0:
            return self.mp.init_pols * 1.0
        return self._split(t - 1)[1] * self.in_cover(t)

    @var
    def deaths(self, t):
        return self._split(t)[0]["mortality"]

    @var
    def withdrawals(self, t):
        return self._split(t)[0]["lapse"]

    # --- cashflows -------------------------------------------------------

    @var
    def claims(self, t):
        return self.deaths(t) * self.sum_assured(t)

    @var
    def premiums(self, t):
        """Unit rate per mille of cover, at the start of the period."""
        a = self.assumptions
        annual = self.lives_if(t) * self.sum_assured(t) * self.mp.unit_rate / 1000.0
        return a.per_period(annual)

    def _expense_bases(self):
        return {"premium": self.mp.unit_rate * self.mp.salary
                * self.mp.salary_multiple / 1000.0,
                "sum_assured": self.mp.salary * self.mp.salary_multiple}

    @var(assumption="expenses")
    def expenses(self, t):
        a = self.assumptions
        annual = a.expenses.renewal.amount(**self._expense_bases())
        return (self.lives_if(t)
                * (a.per_period(annual) * a.inflation_index(t))
                * self.in_cover(t))

    # --- the experience refund -------------------------------------------

    def _strike(self, t):
        """1 in the period a rating period's surplus is struck.

        A branch on ``t`` alone — run structure, not model-point data — so
        it is an ordinary Python expression rather than an indicator.
        """
        periods = self.assumptions.periods(self.rating_period)
        return float((t + 1) % periods == 0)

    @var
    def strike(self, t):
        return self._strike(t)

    @pool
    def scheme_margin(self, t):
        """Premium less claims and expenses across the whole scheme.

        Pooled because an experience refund is struck on the **scheme**,
        not on a member. One life's claim is met out of everybody's
        premium, which is a transfer between model points and exactly what
        a per-policy formula cannot see.

        The insurer's retained margin is taken out of the premium before
        the pot is struck, so a scheme running at precisely its expected
        claims produces no refund at all.
        """
        premium = self.pool_sum(self.premiums(t)) * (1.0 - self.retained_margin)
        return premium - self.pool_sum(self.claims(t) + self.expenses(t))

    @pool
    def surplus_carried(self, t):
        """Scheme surplus accumulated since the last strike.

        Reset by the strike rather than by the calendar: the balance is
        multiplied by ``1 − strike`` at the *previous* period, so the
        period that pays a refund starts from nothing.
        """
        margin = self.scheme_margin(t)
        if t == 0:
            return margin
        carried = self.surplus_carried(t - 1) * (1.0 - self._strike(t - 1))
        return carried * self.assumptions.period_accumulation() + margin

    @pool
    def experience_refund(self, t):
        """Returned to the employer at the end of a rating period.

        **Floored at zero, and that floor is the option.** A deficit is not
        clawed back — the insurer wrote the cover and carries it — so the
        refund is worth ``E[max(surplus, 0)]`` and not
        ``max(E[surplus], 0)``. See :func:`refund_option_value` for the
        difference, computed exactly.
        """
        return (self.profit_share * np.maximum(self.surplus_carried(t), 0.0)
                * self._strike(t))

    @pool
    def insurer_result(self, t):
        """What the insurer keeps in period t, after any refund."""
        return self.scheme_margin(t) - self.experience_refund(t)
