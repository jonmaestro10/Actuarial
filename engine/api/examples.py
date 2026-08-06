"""A worked request for every template this deployment can actually run.

The API is discoverable — :func:`engine.api.catalogue.catalogue` walks
:mod:`engine.library` and every template it finds is offered — and that
discoverability stops exactly at the point a caller has to write a request.
``ModelPoint`` is an open attribute bag with no schema, so *which* fields a
template needs is not something the catalogue can say, and a request missing
one fails inside the projection with an ``AttributeError`` naming an
attribute rather than an input.

:func:`engine.core.modeldoc.modelpoint_fields` closes half of that: it reads
the source and says which fields exist and which are optional. It cannot say
what a *plausible* value is — that a term assurance is written at 40 for
twenty-five years and not at 400 for three — and a demonstration needs the
values, not just the names.

So this module carries one worked example per template, and is honest about
what that is: a specimen, not a recommendation. Nothing here is calibrated,
none of it is anybody's assumption basis, and the only claims made for these
numbers are that they parse, that they run, and that they exercise the
template's shape. ``tests/test_api_demo.py`` asserts all three, and asserts
that every example supplies every field its model requires — so an example
cannot rot into a lie while the template moves under it.

Every template has one
----------------------
The catalogue offers eighteen and all eighteen are here. It was eight of
sixteen a few commits ago, and every one that joined was kept out by the
**request schema** rather than by anything about the templates:

* ``PayoutAnnuity``, ``PensionBuyout`` and ``LongevitySwap`` need a
  :class:`~engine.data.basis.ValuationBasis` (``LongevitySwap`` needs two,
  one per leg) and their model points carry dates.
  :func:`~engine.api.catalogue.build_assumptions` now takes a ``kind`` and
  :func:`~engine.api.catalogue.coerce_dates` turns an ISO-8601 string into a
  :class:`datetime.date` at the HTTP boundary.
* ``IncomeProtection`` needs a
  :class:`~engine.data.multistate.TransitionMatrix`, which is an object-valued
  *field* rather than a whole basis, and is now carried as
  ``assumptions.transitions``. ``LongTermCare`` arrived **after** that was
  built and had a worked example from its first commit, which is what the
  sequencing was for.

* ``UnitLinkedGMDB``, ``UnitLinkedGMxB``, ``VariablePayoutAnnuity`` and
  ``FixedIndexedAnnuity`` need a **bound scenario set** — the fourth needs an
  index-crediting rule as well, which itself reads index returns from one.
  ``scenarios`` is now a top-level request key and
  ``assumptions.index_credit`` an object-valued field (RFC-068).

That mattered beyond the demonstration. The evidence pack's specimen set
walks ``EXAMPLES``, so a template with no example is invisible to it, and
half the catalogue was in that position as recently as RFC-066.

:data:`UNAVAILABLE` is now **empty**, and is kept rather than deleted:
``GET /models`` still answers the question, and the next template to land on
something the schema cannot express should say so there rather than quietly
failing to appear. ``tests/test_api_demo.py`` asserts that the two sets
partition the catalogue, so the two cannot drift apart.

Four of the eighteen specimens bind a scenario set, which puts them in a
**third executor class**: they run under the stochastic executor and under
no other, because a template that reads ``self.scenarios`` cannot be given
``None``. RFC-068 names the class and the bridge that holds it —
``ScenarioSet.single(s)``, one scenario run alone reproducing that column of
the slab bitwise — and :mod:`engine.report.evidence` reports it as a claim
rather than as a failure.
"""

from __future__ import annotations

import copy
import math


def _gompertz(ages: range, a: float = 0.0004, b: float = 1.09,
              base: int = 30) -> dict:
    """An illustrative mortality curve, ``q_x = a * b ** (x - base)``.

    Written as a formula rather than as eighty magic numbers, because the
    formula is the honest description: this is a smooth curve chosen to
    rise the way a real one does, and it is not a published table, not
    anybody's experience, and not fit for anything but a demonstration.
    The same shape :mod:`tests.test_ifrs17` measures its end-to-end
    overlay on.

    Wide enough to cover any age the form is likely to be edited to.
    :class:`~engine.data.assumptions.MortalityTable` clamps a lookup past
    its last age rather than extrapolating, so a narrow table would answer
    an edited model point quietly and wrongly.

    Rounded to eight places, which is far finer than any table is quoted to
    and keeps the specimen readable when it is shown as JSON in a form.

    Keyed by a **string** age, because everything in this module is a JSON
    request body and a JSON object has no other kind of key. Keeping the
    Python integers would make the specimen a thing that had to be
    converted before it was what it claims to be — and, since
    :meth:`engine.api.store.RunStore.identify` fingerprints the request as
    submitted, it would give the same example two identifiers depending on
    whether it arrived over HTTP or was passed to the store directly.
    :func:`~engine.api.catalogue.build_assumptions` takes either.
    """
    return {str(age): round(min(a * b ** (age - base), 1.0), 8)
            for age in ages}


#: The specimen table the life examples share.
MORTALITY = _gompertz(range(18, 111))


def _gompertz_by_sex(ages: range) -> dict:
    """The same curve for two sexes, the layout a real table comes in.

    :class:`~engine.data.mortality.MortalityBasis` is keyed by sex code
    because published tables are, and the female rates here are a flat 85%
    of the male ones — which is roughly the right shape and is emphatically
    not anybody's calibration. Same caveat as :func:`_gompertz`.
    """
    return {"M": _gompertz(ages),
            "F": {age: round(q * 0.85, 8)
                  for age, q in _gompertz(ages).items()}}


def _improvement(ages: range, rate: float = 0.01) -> dict:
    """A flat improvement scale, which is the simplest shape the basis
    accepts and enough to show that the axis exists."""
    return {sex: {str(age): rate for age in ages} for sex in ("M", "F")}


#: The basis-chassis specimens' mortality, in the ``{sex: {age: q}}`` layout
#: the request schema now carries.
BASIS_MORTALITY = {
    "rates": _gompertz_by_sex(range(18, 116)),
    "year_start": 2014,
    "improvement": _improvement(range(18, 116)),
}

#: A flat 4% annual curve, long enough for an annuitant to run off on.
BASIS_CURVE = {"rates": 0.04, "freq": 1, "horizon_years": 60}


def _lognormal(horizon: int, *, n_scenarios: int = 64, vol: float = 0.18,
               seed: int = 20_260_101) -> dict:
    """A generated scenario set, in the request schema's ``lognormal`` form.

    ``drift`` is ``log(1.04)``, which makes the set risk-neutral with respect
    to the flat 4% the basis specimens discount at: ``E[1 + r] = 1.04``. It
    is written to eight places to keep the specimen readable as JSON, and
    **the rounding costs the martingale property** in the eighth decimal —
    which is stated here rather than left for somebody to rediscover from a
    reconciliation that misses by 3e-9. A specimen is not a calibration; the
    one thing it is claiming is a shape.

    ``seed`` pins the stream, so the same request builds the same set. What
    it does *not* pin is the stream across NumPy versions — see
    :mod:`engine.api.catalogue` on the two identities, and
    ``tests/test_api_scenarios.py``, which asserts the digest of the values
    this returns so a moved stream fails loudly.

    Sixty-four scenarios: enough for the guarantee cashflows to be visibly
    stochastic rather than a smooth curve, and small enough that the
    evidence pack — which runs every specimen more than once — stays cheap.
    """
    return {"kind": "lognormal", "n_scenarios": n_scenarios,
            "horizon": horizon, "drift": round(math.log(1.04), 8),
            "vol": vol, "seed": seed}


#: One runnable request per template, keyed by model name. Each is a
#: complete body for ``POST /runs``: the demonstration loads one into the
#: form and the caller edits it from there.
#:
#: ``outputs`` is named rather than left to default to every variable,
#: because these are the series worth *looking* at — and for the mutual
#: templates, the ones :mod:`engine.api.reports` needs to measure the block
#: under IFRS 17. ``proj_len`` runs one period past the term so that the
#: run-off is visible rather than implied by a truncated chart.
EXAMPLES: dict = {
    "TermLife": {
        "note": "A block of level term assurance — 1,000 policies "
                "written at 40 for 25 years and 400 at 55 for 15 — "
                "priced at roughly a fifth margin over expected cost.",
        "request": {
            "model": "TermLife",
            "proj_len": 26,
            "outputs": ["pols_if", "premiums", "claims", "expenses",
                        "initial_expenses", "pols_death", "pols_lapse"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.05, "interest": 0.03,
                "expense_per_policy": 30.0,
            },
            "modelpoints": [
                {"id": "T1", "age_at_entry": 40, "term_years": 25,
                 "sum_assured": 250_000.0, "annual_premium": 750.0,
                 "init_pols": 1_000.0},
                {"id": "T2", "age_at_entry": 55, "term_years": 15,
                 "sum_assured": 100_000.0, "annual_premium": 1_150.0,
                 "init_pols": 400.0},
            ],
        },
    },
    "Endowment": {
        "note": "A 20-year endowment maturing for its sum assured, showing "
                "maturities dominating claims at the end of the term.",
        "request": {
            "model": "Endowment",
            "proj_len": 21,
            "outputs": ["pols_if", "premiums", "claims", "expenses",
                        "maturities", "pols_death", "pols_lapse"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.04, "interest": 0.03,
                "expense_per_policy": 40.0,
            },
            "modelpoints": [
                {"id": "E1", "age_at_entry": 40, "term_years": 20,
                 "sum_assured": 100_000.0, "annual_premium": 4_200.0,
                 "init_pols": 1_000.0},
            ],
        },
    },
    "WholeLife": {
        "note": "Whole life run to age 100 — the same template as the "
                "endowment, with the maturity benefit switched off by term.",
        "request": {
            "model": "WholeLife",
            "proj_len": 45,
            "outputs": ["pols_if", "premiums", "claims", "expenses",
                        "pols_death", "pols_lapse"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.03, "interest": 0.03,
                "expense_per_policy": 35.0,
            },
            "modelpoints": [
                {"id": "W1", "age_at_entry": 45, "term_years": 45,
                 "sum_assured": 150_000.0, "annual_premium": 2_200.0,
                 "init_pols": 1_000.0},
            ],
        },
    },
    "WithProfitsEndowment": {
        "note": "A with-profits endowment on the pooled executor: asset "
                "share, declared and terminal bonus, and the cost of "
                "smoothing the payout.",
        "request": {
            "model": "WithProfitsEndowment",
            "proj_len": 21,
            "outputs": ["pols_if", "asset_share", "guaranteed_benefit",
                        "maturity_payout", "declared_bonus", "terminal_bonus",
                        "smoothing_cost"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.03, "interest": 0.045,
                "expense_per_policy": 40.0,
            },
            "modelpoints": [
                {"id": "P1", "age_at_entry": 40, "term_years": 20,
                 "sum_assured": 100_000.0, "annual_premium": 3_800.0,
                 "init_pols": 1_000.0},
                {"id": "P2", "age_at_entry": 55, "term_years": 20,
                 "sum_assured": 50_000.0, "annual_premium": 2_600.0,
                 "init_pols": 400.0},
            ],
        },
    },
    "UniversalLife": {
        "note": "Universal life: the account value roll-forward, its cost "
                "of insurance, and the lapse that follows exhaustion.",
        "request": {
            "model": "UniversalLife",
            "proj_len": 31,
            "outputs": ["pols_if", "premiums", "account_value", "coi_due",
                        "interest_credited", "death_claims", "surrenders",
                        "cash_value"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.04, "interest": 0.035,
                "crediting_rate": 0.03, "expense_per_policy": 60.0,
            },
            "modelpoints": [
                {"id": "U1", "age_at_entry": 45, "term_years": 30,
                 "face_amount": 250_000.0, "annual_premium": 3_000.0,
                 "init_pols": 1_000.0},
            ],
        },
    },
    "FixedAnnuity": {
        "note": "A single-premium deferred annuity: five years of "
                "accumulation, then payments for life.",
        "request": {
            "model": "FixedAnnuity",
            "proj_len": 40,
            "outputs": ["pols_if", "payments", "death_benefits",
                        "fund_eoy_per_pol"],
            "assumptions": {
                "mortality": 0.012, "interest": 0.03, "crediting_rate": 0.025,
            },
            "modelpoints": [
                {"id": "A1", "age_at_entry": 60, "defer_years": 5,
                 "premium": 100_000.0, "annual_payment": 8_000.0,
                 "init_pols": 1_000.0},
            ],
        },
    },
    "GroupLife": {
        "note": "A group scheme rated at 2.5 per mille of covered "
                "salary, with the experience refund its rating period "
                "earns back.",
        "request": {
            "model": "GroupLife",
            "proj_len": 10,
            "outputs": ["lives_if", "sum_assured", "premiums", "claims",
                        "expenses", "insurer_result", "experience_refund"],
            "assumptions": {
                "mortality": 0.002, "lapse": 0.08, "interest": 0.03,
                "expense_per_policy": 12.0,
            },
            "modelpoints": [
                {"id": "G1", "age_at_entry": 38, "salary": 55_000.0,
                 "salary_multiple": 3.0, "unit_rate": 2.5,
                 "init_pols": 600.0},
                {"id": "G2", "age_at_entry": 52, "salary": 80_000.0,
                 "salary_multiple": 3.0, "unit_rate": 2.5,
                 "init_pols": 250.0},
            ],
        },
    },
    "CreditLife": {
        "note": "Single-premium decreasing term over a 20-year repayment "
                "loan: cover follows the outstanding balance and the "
                "premium is earned against it.",
        "request": {
            "model": "CreditLife",
            "proj_len": 21,
            "outputs": ["pols_if", "outstanding_balance", "claims",
                        "earned_premium", "unearned_premium_reserve",
                        "refunds", "net_cashflow"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.06, "interest": 0.03,
                "expense_per_policy": 10.0,
            },
            "modelpoints": [
                {"id": "C1", "age_at_entry": 40, "loan_principal": 200_000.0,
                 "loan_rate": 0.06, "loan_term_years": 20,
                 "single_premium": 5_000.0, "init_pols": 1_000.0},
            ],
        },
    },
    "GeneralInsurance": {
        "note": "A five-year general insurance cohort — £1m written, "
                "earned evenly, at a 95% combined ratio: 62% attritional "
                "claims, a 5% catastrophe load kept separate from them, "
                "and 28% expenses.",
        "request": {
            "model": "GeneralInsurance",
            "proj_len": 6,
            "outputs": ["written_premium", "premium_earned",
                        "unearned_premium", "attritional_claims", "cat_load",
                        "claims", "expenses", "underwriting_result", "v"],
            "assumptions": {"mortality": MORTALITY, "interest": 0.03},
            "modelpoints": [
                {"id": "G1", "written_premium": 1_000_000.0,
                 "policy_term_years": 5, "expected_loss_ratio": 0.62,
                 "cat_load_ratio": 0.05, "expense_ratio": 0.28,
                 "init_pols": 1.0, "earning_pattern": "uniform"},
                {"id": "G2", "written_premium": 250_000.0,
                 "policy_term_years": 3, "expected_loss_ratio": 0.70,
                 "cat_load_ratio": 0.02, "expense_ratio": 0.25,
                 "init_pols": 1.0, "earning_pattern": "front"},
            ],
        },
    },
    "IncomeProtection": {
        "note": "Group income protection on the three-state chain — "
                "healthy, sick, dead — with a 5% annual chance of falling "
                "sick and a 20% chance of recovering. Premiums are paid by "
                "the healthy and the benefit by the sick, so waiver of "
                "premium is the model rather than a rider on it.",
        "request": {
            "model": "IncomeProtection",
            "proj_len": 21,
            "outputs": ["healthy", "sick", "dead", "premiums", "benefits",
                        "in_term", "v"],
            "assumptions": {
                "interest": 0.03,
                "mortality": MORTALITY,
                "transitions": {
                    "states": {"names": ["healthy", "sick", "dead"],
                               "absorbing": ["dead"]},
                    "matrix": [[0.940, 0.050, 0.010],
                               [0.200, 0.780, 0.020],
                               [0.000, 0.000, 1.000]],
                },
            },
            "modelpoints": [
                {"id": "IP1", "age_at_entry": 35, "term_years": 20,
                 "annual_premium": 600.0, "annual_benefit": 24_000.0,
                 "init_pols": 1_000.0},
                {"id": "IP2", "age_at_entry": 45, "term_years": 15,
                 "annual_premium": 950.0, "annual_benefit": 18_000.0,
                 "init_pols": 400.0},
            ],
        },
    },
    "LongTermCare": {
        "note": "A block of long-term care issued at 60 on a twenty-year "
                "premium-paying term, with 5% compound inflation "
                "protection. Home care pays half the facility maximum and "
                "is drawn at 70% utilization; facility care draws all of "
                "it, because facility costs generally exceed the cap.",
        "request": {
            "model": "LongTermCare",
            "proj_len": 40,
            "outputs": ["active", "home_care", "facility_care", "dead",
                        "premiums", "benefits", "benefit_maximum",
                        "incidence", "progression", "v"],
            "assumptions": {
                "interest": 0.03,
                "mortality": MORTALITY,
                "transitions": {
                    "states": {"names": ["active", "home_care",
                                         "facility_care", "dead"],
                               "absorbing": ["dead"]},
                    "matrix": [[0.950, 0.030, 0.010, 0.010],
                               [0.080, 0.750, 0.120, 0.050],
                               [0.020, 0.030, 0.850, 0.100],
                               [0.000, 0.000, 0.000, 1.000]],
                },
            },
            "modelpoints": [
                {"id": "L1", "age_at_entry": 60, "premium_years": 20,
                 "annual_premium": 2_400.0,
                 "annual_benefit_maximum": 60_000.0, "init_pols": 1_000.0,
                 "home_care_percent": 0.5, "home_care_utilization": 0.7,
                 "facility_utilization": 1.0, "inflation_rate": 0.05,
                 "inflation_mode": "compound"},
                {"id": "L2", "age_at_entry": 70, "premium_years": 10,
                 "annual_premium": 4_800.0,
                 "annual_benefit_maximum": 90_000.0, "init_pols": 250.0,
                 "home_care_percent": 1.0, "home_care_utilization": 0.6,
                 "facility_utilization": 1.0, "inflation_rate": 0.0,
                 "inflation_mode": "none"},
            ],
        },
    },
    "PayoutAnnuity": {
        "note": "Three annuities in payment on the valuation basis — a "
                "level life annuity, one with a ten-year guarantee, and a "
                "joint life with a 60% reversion to the spouse.",
        "request": {
            "model": "PayoutAnnuity",
            "proj_len": 45,
            "outputs": ["payments", "lives_if", "survivor_lives",
                        "survival", "age", "v"],
            "assumptions": {
                "kind": "valuation_basis",
                "mortality": BASIS_MORTALITY, "curve": BASIS_CURVE,
            },
            "modelpoints": [
                {"id": 'A1', "dob": '1956-01-01', "sex": 'M', "valuation": '2021-01-01', "annual_payment": 12000.0, "init_lives": 1.0, "certain_years": 0.0, "joint_percent": 0.0, "spouse_dob": '1958-06-30', "spouse_sex": 'F'},
                {"id": 'A2', "dob": '1946-06-30', "sex": 'F', "valuation": '2021-01-01', "annual_payment": 6000.0, "init_lives": 3.0, "certain_years": 10.0, "joint_percent": 0.0, "spouse_dob": '1948-03-02', "spouse_sex": 'M'},
                {"id": 'A3', "dob": '1960-02-29', "sex": 'M', "valuation": '2021-01-01', "annual_payment": 24000.0, "init_lives": 1.0, "certain_years": 0.0, "joint_percent": 0.6, "spouse_dob": '1962-11-15', "spouse_sex": 'F'},
            ],
        },
    },
    "PensionBuyout": {
        "note": "A small scheme bought out: two pensioners, one with 3% "
                "escalation, and a deferred member twelve years from "
                "retirement whose pension revalues at 2.5% until then.",
        "request": {
            "model": "PensionBuyout",
            "proj_len": 50,
            "outputs": ["payments", "member_payments", "spouse_payments",
                        "pension_amount", "lives_if", "in_payment", "v"],
            "assumptions": {
                "kind": "valuation_basis",
                "mortality": BASIS_MORTALITY, "curve": BASIS_CURVE,
            },
            "modelpoints": [
                {"id": 'P1', "dob": '1956-01-01', "sex": 'M', "valuation": '2021-01-01', "annual_pension": 12000.0, "init_lives": 1.0, "deferred_years": 0.0, "revaluation_rate": 0.0, "escalation_rate": 0.0, "spouse_percent": 0.0, "spouse_dob": '1958-06-30', "spouse_sex": 'F', "contract": 'buy_out'},
                {"id": 'P2', "dob": '1946-06-30', "sex": 'F', "valuation": '2021-01-01', "annual_pension": 6000.0, "init_lives": 3.0, "deferred_years": 0.0, "revaluation_rate": 0.0, "escalation_rate": 0.03, "spouse_percent": 0.0, "spouse_dob": '1948-03-02', "spouse_sex": 'M', "contract": 'buy_out'},
                {"id": 'P3', "dob": '1975-02-28', "sex": 'M', "valuation": '2021-01-01', "annual_pension": 24000.0, "init_lives": 1.0, "deferred_years": 12.0, "revaluation_rate": 0.025, "escalation_rate": 0.02, "spouse_percent": 0.5, "spouse_dob": '1977-05-04', "spouse_sex": 'F', "contract": 'buy_out'},
            ],
        },
    },
    "LongevitySwap": {
        "note": "The same scheme hedged: the fixed leg is written on a "
                "heavier improvement scale than the projection basis, so "
                "the contracted schedule runs longer than the expected "
                "benefits and the swap values negative at inception — "
                "which is the price of the hedge, not a mispricing.",
        "request": {
            "model": "LongevitySwap",
            "proj_len": 50,
            "outputs": ["floating_leg", "fixed_leg", "net_settlement",
                        "expected_payment", "contracted_payment", "v"],
            "assumptions": {
                "kind": "longevity_swap_basis",
                "projection": {"mortality": BASIS_MORTALITY,
                               "curve": BASIS_CURVE},
                "fixed": {"mortality": {**BASIS_MORTALITY,
                                        "improvement": _improvement(
                                            range(18, 116), 0.03)},
                          "curve": BASIS_CURVE},
            },
            "modelpoints": [
                {"id": 'P1', "dob": '1956-01-01', "sex": 'M', "valuation": '2021-01-01', "annual_pension": 12000.0, "init_lives": 1.0, "deferred_years": 0.0, "revaluation_rate": 0.0, "escalation_rate": 0.0, "spouse_percent": 0.0, "spouse_dob": '1958-06-30', "spouse_sex": 'F'},
                {"id": 'P2', "dob": '1946-06-30', "sex": 'F', "valuation": '2021-01-01', "annual_pension": 6000.0, "init_lives": 3.0, "deferred_years": 0.0, "revaluation_rate": 0.0, "escalation_rate": 0.03, "spouse_percent": 0.0, "spouse_dob": '1948-03-02', "spouse_sex": 'M'},
                {"id": 'P3', "dob": '1975-02-28', "sex": 'M', "valuation": '2021-01-01', "annual_pension": 24000.0, "init_lives": 1.0, "deferred_years": 12.0, "revaluation_rate": 0.025, "escalation_rate": 0.02, "spouse_percent": 0.5, "spouse_dob": '1977-05-04', "spouse_sex": 'F'},
            ],
        },
    },
    "UnitLinkedGMDB": {
        "note": "A unit-linked bond with a return-of-premium death "
                "guarantee, run against 64 lognormal fund paths at 18% "
                "volatility. The guarantee costs nothing on most paths and "
                "everything on a few, which is why it is priced against a "
                "distribution rather than a projection.",
        "request": {
            "model": "UnitLinkedGMDB",
            "proj_len": 21,
            "outputs": ["pols_if", "fund_boy", "fund_eoy", "fund_ret",
                        "fee_income", "gmdb_claims", "gmdb_strain",
                        "maturity_payments", "v"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.04, "interest": 0.03,
                "amc": 0.01, "gmdb_fee": 0.004,
            },
            "scenarios": _lognormal(21),
            "modelpoints": [
                {"id": "U1", "age_at_entry": 45, "term_years": 20,
                 "premium": 100_000.0, "gmdb_guarantee": 100_000.0,
                 "init_pols": 1_000.0},
                {"id": "U2", "age_at_entry": 58, "term_years": 15,
                 "premium": 40_000.0, "gmdb_guarantee": 55_000.0,
                 "init_pols": 400.0},
            ],
        },
    },
    "UnitLinkedGMxB": {
        "note": "The same chassis with every rider switched on at once — "
                "death, maturity and withdrawal guarantees on one contract "
                "— so the three fees and the three strains can be read "
                "side by side. The second policy sets `gmwb_ratchet` to "
                "1.0, which locks the withdrawal base up to the fund at "
                "each anniversary and never back down.",
        "request": {
            "model": "UnitLinkedGMxB",
            "proj_len": 21,
            "outputs": ["pols_if", "fund_eoy", "benefit_base", "fee_income",
                        "gmdb_claims", "gmab_strain", "gmwb_strain",
                        "withdrawals", "maturity_payments", "v"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.04, "interest": 0.03,
                "amc": 0.01, "gmdb_fee": 0.004, "gmab_fee": 0.006,
                "gmwb_fee": 0.005,
            },
            "scenarios": _lognormal(21),
            "modelpoints": [
                {"id": "X1", "age_at_entry": 55, "term_years": 20,
                 "premium": 100_000.0, "gmdb_guarantee": 100_000.0,
                 "gmab_guarantee": 100_000.0, "gmwb_base": 100_000.0,
                 "gmwb_rate": 0.05, "gmwb_ratchet": 0.0,
                 "init_pols": 500.0},
                {"id": "X2", "age_at_entry": 62, "term_years": 15,
                 "premium": 250_000.0, "gmdb_guarantee": 250_000.0,
                 "gmab_guarantee": 275_000.0, "gmwb_base": 250_000.0,
                 "gmwb_rate": 0.04, "gmwb_ratchet": 1.0,
                 "init_pols": 120.0},
            ],
        },
    },
    "FixedIndexedAnnuity": {
        "note": "An FIA with a lifetime withdrawal rider: annual "
                "point-to-point crediting at a 6% cap, floored at zero, so "
                "the account ratchets and never falls. Withdrawals start "
                "in year ten and run for life, which is why the projection "
                "goes to age 105 rather than to a term. The request schema "
                "carries no account basis, so this contract has **no "
                "surrender charge** — its cash value is its account value, "
                "and a real FIA's would not be.",
        "request": {
            "model": "FixedIndexedAnnuity",
            "proj_len": 46,
            "outputs": ["pols_if", "index_credit_rate", "av_eop",
                        "benefit_base", "gaw", "withdrawals", "glwb_strain",
                        "rider_fee_income", "death_benefits", "v"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.05, "interest": 0.035,
                "glwb_fee": 0.0105,
                "index_credit": {"kind": "AnnualPointToPoint", "cap": 0.06,
                                 "participation": 1.0, "spread": 0.0,
                                 "floor": 0.0},
            },
            "scenarios": _lognormal(46),
            "modelpoints": [
                {"id": "F1", "age_at_entry": 60, "premium": 100_000.0,
                 "init_pols": 1_000.0, "glwb_base": 100_000.0,
                 "glwb_rate": 0.05, "withdrawal_start_year": 10,
                 "glwb_rollup": 0.07, "glwb_rollup_years": 10},
            ],
        },
    },
    "FamilyTakaful": {
        "note": "A family takaful plan on the hybrid model: a 30% wakala "
                "fee on contributions, a 20% mudarabah share of investment "
                "profit, and surplus in the participants' risk fund "
                "distributed at 25% of its balance a year. 48 paths at 22% "
                "volatility, which is what makes the qard hasan facility "
                "do anything — the fund is priced to run off at about zero "
                "on the mean path, dips into deficit on roughly a third of "
                "them, and repays the operator's loan out of surplus "
                "before distributing any of it. Pooled, so the interpreted "
                "executor cannot run it (RFC-061); scenario-bound, so "
                "neither deterministic executor can (RFC-068).",
        "request": {
            "model": "FamilyTakaful",
            "proj_len": 16,
            "outputs": ["pols_if", "contribution", "wakala_fee_charged",
                        "tabarru", "pif", "risk_fund_boy", "claims_paid",
                        "qard_drawn", "qard_repaid", "qard_outstanding",
                        "distributable_surplus",
                        "qard_transfer_to_participants", "surplus_paid",
                        "operator_income", "v"],
            "assumptions": {
                "mortality": MORTALITY, "lapse": 0.05, "interest": 0.04,
            },
            "scenarios": _lognormal(16, n_scenarios=48, vol=0.22),
            "modelpoints": [
                {"id": "K1", "age_at_entry": 45, "term_years": 15,
                 "sum_covered": 160_000.0, "annual_contribution": 2_500.0,
                 "init_pols": 1_000.0},
                {"id": "K2", "age_at_entry": 58, "term_years": 12,
                 "sum_covered": 104_000.0, "annual_contribution": 2_000.0,
                 "init_pols": 400.0},
            ],
        },
    },
    "VariablePayoutAnnuity": {
        "note": "A pooled variable payout annuity: three members share a "
                "fund, and every year the whole pool's pensions are scaled "
                "by what it has against what it owes. Each member's "
                "account is set to their own liability at outset, so the "
                "pool starts balanced and every later adjustment is "
                "experience rather than an opening mismatch. The scenarios "
                "are risk-neutral against the basis's own 4%. Pooled, so "
                "the interpreted executor cannot run it — see RFC-061.",
        "request": {
            "model": "VariablePayoutAnnuity",
            "proj_len": 41,
            "outputs": ["lives", "pension", "account_value", "assets",
                        "liability", "adjustment", "payments"],
            "assumptions": {
                "kind": "valuation_basis",
                "mortality": BASIS_MORTALITY, "curve": BASIS_CURVE,
                "revalue_every": 1,
            },
            "scenarios": _lognormal(41, n_scenarios=32, vol=0.10),
            "modelpoints": [
                {"id": 'V1', "dob": '1956-01-01', "sex": 'M', "valuation": '2021-01-01', "pension": 12000.0, "account_value": 190900.0, "init_lives": 1.0},
                {"id": 'V2', "dob": '1946-06-30', "sex": 'F', "valuation": '2021-01-01', "pension": 6000.0, "account_value": 80250.0, "init_lives": 3.0},
                {"id": 'V3', "dob": '1951-03-15', "sex": 'M', "valuation": '2021-01-01', "pension": 24000.0, "account_value": 344700.0, "init_lives": 1.0},
            ],
        },
    },
}


#: Why a catalogued template has no example here, by model name.
#:
#: **Empty**, and that is the point of keeping it: every template the
#: catalogue lists can now be run from a request this deployment carries.
#: The mechanism stays — ``GET /models`` still reports it, and
#: ``tests/test_api_demo.py`` still asserts that the two sets partition the
#: catalogue — because the next template to land on a class the schema
#: cannot express should say so here rather than quietly not appear.
UNAVAILABLE: dict = {}


def example(name: str) -> dict | None:
    """The worked example for ``name``, or ``None`` if there is not one.

    A deep copy: the caller is going to edit it, and a demonstration that
    mutated the specimen would give the next caller a different one.
    """
    found = EXAMPLES.get(name)
    return copy.deepcopy(found) if found is not None else None


def unavailable(name: str) -> str | None:
    """Why ``name`` has no example, if it is a template known to need more
    than the request schema carries."""
    return UNAVAILABLE.get(name)
