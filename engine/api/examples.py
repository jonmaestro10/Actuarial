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

Four templates have no example, and they all want the same thing
----------------------------------------------------------------
The catalogue offers eighteen and fourteen of them are here. It was eight
of sixteen a few commits ago, and the ones that joined were kept out by the
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

That mattered beyond the demonstration. The evidence pack's specimen set
walks ``EXAMPLES``, so a template with no example is invisible to it, and
half the catalogue was in that position.

What is left is **one** reason rather than three: ``UnitLinkedGMDB``,
``UnitLinkedGMxB`` and ``VariablePayoutAnnuity`` need a bound scenario set,
and ``FixedIndexedAnnuity`` an index-crediting rule that itself reads index
returns from one. A scenario format is the serialisation that would have to
be invented for a class still moving — the reasoning
:mod:`engine.api.catalogue`'s docstring gives, now applying to one thing
instead of standing in for several.

:data:`UNAVAILABLE` records that, per template, so ``GET /models`` can say
which of the sixteen a caller can actually run here and why the rest are
not. A catalogue that lists a model it cannot run and does not say so is
worse than one that lists fourteen.

The way to run the other four is the same as it has always been: pass your
own ``build`` to :func:`engine.api.app.create_app`, which is where a
scenario set belongs.
"""

from __future__ import annotations

import copy


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
}


#: Why a catalogued template has no example here, by model name. Each entry
#: is what a caller would hit if they wrote the request themselves, so it is
#: a limit of the request schema rather than of the engine.
UNAVAILABLE: dict = {
    "FixedIndexedAnnuity":
        "needs an index-crediting rule on the assumptions, and index "
        "returns from a bound scenario set",
    "UnitLinkedGMDB":
        "needs a bound scenario set — the fund return is a scenario, and "
        "the request schema has no scenario format",
    "UnitLinkedGMxB":
        "needs a bound scenario set for the fund return, and rider fees "
        "beyond the scalars the request schema carries",
    "VariablePayoutAnnuity":
        "needs a bound scenario set for the pool return — the valuation "
        "basis and the dates its model points carry are now expressible, "
        "and the scenario format is not",
}


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
