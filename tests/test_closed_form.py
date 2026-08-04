"""Golden tests: TermLife against textbook closed forms.

With flat mortality q, flat interest i, no lapses, and unit initial policies,
every projected quantity has an exact closed form:

    pols_if(t)   = (1 - q)^t                        for t <= n (0 after)
    PV premiums  = P * sum_{t=0}^{n-1} ((1-q) v)^t  (annuity-due on survivors)
    PV claims    = S * q v * sum_{t=0}^{n-1} ((1-q) v)^t

Engine output must match to 1e-12 relative.
"""

import pytest

from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.term_life import TermLife

Q = 0.01
I = 0.03
N = 20
S = 100_000.0
P = 1_200.0
V = 1.0 / (1.0 + I)

REL = 1e-12


def build(q=Q, i=I, lapse=0.0, n=N):
    mp = ModelPoint(
        age_at_entry=40, term_years=n, sum_assured=S, annual_premium=P, init_pols=1
    )
    assumptions = Assumptions(
        mortality=MortalityTable.flat(q), lapse=lapse, interest=i
    )
    return TermLife(mp=mp, assumptions=assumptions, proj_len=n + 5)


def geometric_sum(r, n):
    return sum(r**t for t in range(n))


def test_survivorship_closed_form():
    m = build()
    for t in range(N):
        assert m.pols_if(t) == pytest.approx((1 - Q) ** t, rel=REL)
    for t in range(N, m.proj_len + 1):
        assert m.pols_if(t) == 0.0


def test_pv_premiums_is_temporary_annuity_due():
    m = build()
    expected = P * geometric_sum((1 - Q) * V, N)
    assert m.pv_premiums() == pytest.approx(expected, rel=REL)


def test_pv_claims_is_term_assurance_apv():
    m = build()
    expected = S * Q * V * geometric_sum((1 - Q) * V, N)
    assert m.pv_claims() == pytest.approx(expected, rel=REL)


def test_net_premium_zeroes_net_pv():
    # Solve P for actuarial equivalence, feed it back in, expect net PV == 0.
    annuity = geometric_sum((1 - Q) * V, N)
    net_premium = S * Q * V
    mp = ModelPoint(
        age_at_entry=40,
        term_years=N,
        sum_assured=S,
        annual_premium=net_premium,
        init_pols=1,
    )
    assumptions = Assumptions(mortality=MortalityTable.flat(Q), lapse=0.0, interest=I)
    m = TermLife(mp=mp, assumptions=assumptions, proj_len=N + 1)
    assert m.net_pv() == pytest.approx(0.0, abs=1e-9 * S * annuity)


def test_lapses_compound_with_mortality():
    lapse = 0.05
    m = build(lapse=lapse)
    for t in range(N):
        assert m.pols_if(t) == pytest.approx(
            ((1 - Q) * (1 - lapse)) ** t, rel=REL
        )


def test_zero_interest_pv_is_plain_sum():
    m = build(i=0.0)
    assert m.pv_claims() == pytest.approx(
        sum(m.series("claims")), rel=REL
    )


def test_probabilities_conserve_lives():
    # Deaths + lapses + survivors account for every starting policy.
    lapse = 0.05
    m = build(lapse=lapse)
    total_deaths = sum(m.pols_death(t) for t in range(N))
    total_lapses = sum(
        m.pols_if(t) * (1 - m.q_x(t)) * lapse for t in range(N)
    )
    survivors_at_maturity = m.pols_if(N - 1) * (1 - m.q_x(N - 1)) * (1 - lapse)
    assert total_deaths + total_lapses + survivors_at_maturity == pytest.approx(
        1.0, rel=REL
    )
