"""Golden tests: FixedAnnuity against closed forms.

Flat mortality q, flat interest i, flat crediting g, unit initial policies,
deferral n, horizon T (payments truncated at the horizon):

    pols_if(t)        = (1 - q)^t
    fund_eoy(t)       = P (1 + g)^(t+1)                    for t < n
    PV payments       = A * sum_{t=n}^{T-1} ((1-q) v)^t     (deferred annuity-due)
    PV death benefits = P q (1+g) v * sum_{t=0}^{n-1} ((1-q)(1+g) v)^t
"""

import pytest

from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.fixed_annuity import FixedAnnuity

Q = 0.015
I = 0.03
G = 0.02
N = 10          # deferral years
T = 40          # projection horizon
P = 100_000.0   # single premium
A = 9_000.0     # annual payment

V = 1.0 / (1.0 + I)
REL = 1e-12


def build(q=Q, i=I, g=G, n=N):
    mp = ModelPoint(
        age_at_entry=50, defer_years=n, premium=P, annual_payment=A, init_pols=1
    )
    assumptions = Assumptions(
        mortality=MortalityTable.flat(q), interest=i, crediting_rate=g
    )
    return FixedAnnuity(mp=mp, assumptions=assumptions, proj_len=T)


def test_survivorship_closed_form():
    m = build()
    for t in range(T + 1):
        assert m.pols_if(t) == pytest.approx((1 - Q) ** t, rel=REL)


def test_fund_accumulation_closed_form():
    m = build()
    for t in range(N):
        assert m.fund_eoy_per_pol(t) == pytest.approx(P * (1 + G) ** (t + 1), rel=REL)
    for t in range(N, T + 1):
        assert m.fund_eoy_per_pol(t) == 0.0


def test_pv_payments_is_deferred_annuity_due():
    m = build()
    expected = A * sum(((1 - Q) * V) ** t for t in range(N, T))
    assert m.pv_payments() == pytest.approx(expected, rel=REL)


def test_pv_death_benefits_closed_form():
    m = build()
    r = (1 - Q) * (1 + G) * V
    expected = P * Q * (1 + G) * V * (1 - r**N) / (1 - r)
    assert m.pv_death_benefits() == pytest.approx(expected, rel=REL)


def test_no_deferral_is_immediate_annuity():
    m = build(n=0)
    expected = A * sum(((1 - Q) * V) ** t for t in range(T))
    assert m.pv_payments() == pytest.approx(expected, rel=REL)
    assert m.pv_death_benefits() == 0.0


def test_payments_zero_during_deferral_and_positive_after():
    m = build()
    for t in range(N):
        assert m.payments(t) == 0.0
    for t in range(N, T + 1):
        assert m.payments(t) > 0.0
