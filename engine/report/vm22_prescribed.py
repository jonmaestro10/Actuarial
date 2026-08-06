r"""VM-22 §6.C: the prescribed assumptions, and the ones the text brackets.

The dated regulatory data half of the open question C1, C2 and VM-22's
remediation each left on the record. RFC-050 answered the *other* half in the
negative — VM-20 Appendix 1.F prescribes shocks to a generator, so there is
no table to carry (see ``docs/sources/vm20-appendix-1f-scenarios.md``) — and
these are the opposite answer: §6.C holds eleven numeric tables and a
closed-form mortality basis, all dated, all exactly the shape
:mod:`engine.report.market_risk`'s ``DELEGATED_2015``/``DELEGATED_2026``
already has.

Quotations are from the NAIC *Valuation Manual*, 1 January 2026 edition,
chapter VM-22, with section numbers. Provenance and what was read:
``docs/sources/vm22-section-6-prescribed-assumptions.md``.

The square brackets are the NAIC's, and the pattern had no way to say so
---------------------------------------------------------------------------
§6.C.2's expense escalation is written ``[1.025]^(valuation year – 2015)``
with ``[2.5%]`` inflation thereafter. Those brackets are the only ones in the
section, and in NAIC drafting they mark a figure **still under discussion**.

Carrying them as ordinary floats would give them the same standing as the
$50/$100/$75 that are not bracketed, which is a claim the text does not make.
So :class:`Provisional` is a ``float`` subclass — the arithmetic is
unchanged, and the value is self-identifying at the point of definition.
:meth:`PrescribedAssumptions.provisional_fields` *derives* the list by
inspecting its own values rather than maintaining a second list beside them,
because a hand-kept list of which figures are provisional is a list that
drifts from the figures.

Anything computed from one says so: :class:`PrescribedExpense` carries
``provisional``, and it is ``True`` for every expense under this text,
because the escalation is unavoidable.

What is carried, and what is refused
------------------------------------
Five of the eleven tables are here, transcribed from the primary text and
checked against it:

- **Table 6.1**, the base maintenance expense by contract type (§6.C.2.a),
  with §6.C.2.c's $35 for a contract the company does not administer.
- **Tables 6.2 and 6.3**, prescribed partial withdrawal rates for qualified
  and non-qualified contracts (§6.C.4).
- **Tables 6.7 and 6.8**, the *F*\ :sub:`x` mortality factors for individual
  annuities in the Accumulation and Payout Annuity Reserving Categories
  (§6.C.8.i–ii).

The remaining six — three sets of base lapse rates and three
*F*\ :sub:`x` sets for structured settlements — are **recorded and not
carried**, and :func:`fx_factor` **refuses** a category whose table is absent
rather than falling back to one that is present. A mortality factor from the
wrong category is a plausible number that no test would catch, which is
precisely the failure mode this chapter has already produced eight times.

The reason those six are absent is sharper than transcription effort: each
crosses a **second dimension** — an age band against a surrender-charge
duration, or a contract-year band against sex — and a table whose second
dimension is read wrongly is a plausible number in *every* cell rather than
an obviously missing one. They need a read of their own.

What this does **not** build
----------------------------
The additional standard projection amount itself. §6 is the CTEPA method, and
§3.C makes it "only required for disclosure purposes pursuant to VM-31" for
year-end 2026 — so it is not a reserve floor yet, which is why the
assumptions land before the calculation. This module carries the prescribed
inputs; nothing here computes an SPA, and
:mod:`engine.report.vm22` is unchanged.

The mortality basis is a formula over two artefacts that live in **VM-M** —
the 2012 IAM Basic Mortality Table (VM-M §2.C) and Projection Scale G2
(VM-M §1.J.1.c). Neither is carried here, so
:func:`prescribed_mortality_rate` takes them as arguments. That is the same
shape :func:`engine.report.vm22.stochastic_exclusion_test` uses for the
prescribed scenarios, and for the same reason: a chapter's own data is not
this chapter's to invent.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np


class PrescribedError(ValueError):
    """A prescribed figure this module will not supply.

    Every case is one where returning a number would be worse than
    refusing: a Reserving Category whose table is not carried, a contract
    type §6.C.2 has no base expense for, or a projection year before the
    valuation.
    """


class Provisional(float):
    """A figure the text carries in square brackets.

    In NAIC drafting that marks a number still under discussion. This is a
    ``float`` — the arithmetic is exactly what it would be otherwise — and
    it exists so that a figure's standing travels with the figure rather
    than in a comment beside it.
    """

    __slots__ = ("note",)

    def __new__(cls, value, note: str = ""):
        self = super().__new__(cls, value)
        object.__setattr__(self, "note", note)
        return self

    def __repr__(self) -> str:
        return f"Provisional({float(self)!r})"


#: §6.C.2.a, Table 6.1: the base maintenance expense by contract type, and
#: §6.C.2.c's amount where the company does not administer the contract.
BASE_MAINTENANCE_EXPENSE = {
    "payout_annuity": 50.0,
    "accumulation_with_glb": 100.0,
    "accumulation": 75.0,
    "not_administered": 35.0,
}

#: §6.C.2.b: "Seven basis points of the projected account value for each year
#: in the projection." For a contract with no account value the text applies
#: it to the present value of benefits instead, which is the caller's to
#: supply.
ACCOUNT_VALUE_EXPENSE_RATE = 0.0007

#: §6.C.2.a's base year: the escalation runs from 2015.
EXPENSE_BASE_YEAR = 2015

#: Table 6.7, §6.C.8.i. ``(attained age, F female no-GLB, F male no-GLB,
#: F female with-GLB, F male with-GLB)``. Ages at or below 50 take the first
#: row and ages at or above 105 take 100%, both as the table states.
_FX_ACCUMULATION = (
    (50, 1.5000, 1.2000, 1.2500, 1.0500),
    (51, 1.5000, 1.2000, 1.2500, 1.0500),
    (52, 1.5000, 1.2000, 1.2500, 1.0500),
    (53, 1.5000, 1.1800, 1.2500, 1.0160),
    (54, 1.5000, 1.1600, 1.2500, 0.9820),
    (55, 1.5000, 1.1400, 1.2500, 0.9480),
    (56, 1.5000, 1.1200, 1.2500, 0.9140),
    (57, 1.5000, 1.1000, 1.2500, 0.8800),
    (58, 1.4400, 1.0700, 1.1900, 0.8600),
    (59, 1.3800, 1.0400, 1.1300, 0.8400),
    (60, 1.3200, 1.0100, 1.0700, 0.8200),
    (61, 1.2600, 0.9800, 1.0100, 0.8000),
    (62, 1.2000, 0.9500, 0.9500, 0.7800),
    (63, 1.1760, 0.9700, 0.9400, 0.8000),
    (64, 1.1520, 0.9900, 0.9300, 0.8200),
    (65, 1.1280, 1.0100, 0.9200, 0.8400),
    (66, 1.1040, 1.0300, 0.9100, 0.8600),
    (67, 1.0800, 1.0500, 0.9000, 0.8800),
    (68, 1.1000, 1.0560, 0.9260, 0.8900),
    (69, 1.1200, 1.0620, 0.9520, 0.9000),
    (70, 1.1400, 1.0680, 0.9780, 0.9100),
    (71, 1.1600, 1.0740, 1.0040, 0.9200),
    (72, 1.1800, 1.0800, 1.0300, 0.9300),
    (73, 1.1940, 1.0800, 1.0440, 0.9400),
    (74, 1.2080, 1.0800, 1.0580, 0.9500),
    (75, 1.2220, 1.0800, 1.0720, 0.9600),
    (76, 1.2360, 1.0800, 1.0860, 0.9700),
    (77, 1.2500, 1.0800, 1.1000, 0.9800),
    (78, 1.2360, 1.0800, 1.1000, 0.9900),
    (79, 1.2220, 1.0800, 1.1000, 1.0000),
    (80, 1.2080, 1.0800, 1.1000, 1.0100),
    (81, 1.1940, 1.0800, 1.1000, 1.0200),
    (82, 1.1800, 1.0800, 1.1000, 1.0300),
    (83, 1.1640, 1.0840, 1.1000, 1.0440),
    (84, 1.1480, 1.0880, 1.1000, 1.0580),
    (85, 1.1320, 1.0920, 1.1000, 1.0720),
    (86, 1.1160, 1.0960, 1.1000, 1.0860),
    (87, 1.1000, 1.1000, 1.1000, 1.1000),
    (88, 1.0960, 1.1000, 1.0960, 1.1000),
    (89, 1.0920, 1.1000, 1.0920, 1.1000),
    (90, 1.0880, 1.1000, 1.0880, 1.1000),
    (91, 1.0840, 1.1000, 1.0840, 1.1000),
    (92, 1.0800, 1.1000, 1.0800, 1.1000),
    (93, 1.0780, 1.1000, 1.0780, 1.1000),
    (94, 1.0760, 1.1000, 1.0760, 1.1000),
    (95, 1.0740, 1.1000, 1.0740, 1.1000),
    (96, 1.0720, 1.1000, 1.0720, 1.1000),
    (97, 1.0700, 1.1000, 1.0700, 1.1000),
    (98, 1.0620, 1.0900, 1.0620, 1.0900),
    (99, 1.0540, 1.0800, 1.0540, 1.0800),
    (100, 1.0460, 1.0700, 1.0460, 1.0700),
    (101, 1.0380, 1.0600, 1.0380, 1.0600),
    (102, 1.0300, 1.0500, 1.0300, 1.0500),
    (103, 1.0200, 1.0330, 1.0200, 1.0330),
    (104, 1.0100, 1.0170, 1.0100, 1.0170),
)

#: The table's own floor and cap. "<=50" is the first row; ">=105" is 100%.
FX_MIN_AGE, FX_MAX_AGE = 50, 105

#: Which Reserving Categories have an *F*\ :sub:`x` table carried here.
#: §6.C.8 gives five; two of the five belong to structured settlements and
#: are not transcribed, nor is the payout-annuity set.
FX_CATEGORIES_CARRIED = ("accumulation", "payout_annuity")

#: The categories §6.C.8 covers, including the ones not carried — so a
#: refusal can tell a caller whether they asked for something that exists.
FX_CATEGORIES = ("accumulation", "payout_annuity",
                 "structured_settlement_standard",
                 "structured_settlement_substandard")


@dataclass(frozen=True)
class PrescribedAssumptions:
    """A dated set of VM-22 §6.C's prescribed assumptions.

    Dated for the reason :mod:`engine.report.market_risk` carries two
    calibrations: a valuation is performed under a *text*, texts are
    amended, and a module that bakes one in silently revalues last year's
    business the moment it is upgraded.

    :meth:`provisional_fields` is **derived** from the values rather than
    listed beside them. A hand-kept list of which figures the text brackets
    is a list that drifts from the figures.
    """

    label: str
    #: §6.C.2.a: the base expense multiplied by ``escalation ** (valuation
    #: year − 2015)``. Bracketed in the text.
    expense_escalation: float = Provisional(
        1.025, "§6.C.2.a, written [1.025]")
    #: §6.C.2.a: "increased by an assumed annual inflation rate of [2.5%]".
    expense_inflation: float = Provisional(
        0.025, "§6.C.2.a, written [2.5%]")
    account_value_rate: float = ACCOUNT_VALUE_EXPENSE_RATE
    text: str = ""

    def provisional_fields(self) -> tuple:
        """The fields whose value the text carries in square brackets."""
        return tuple(f.name for f in fields(self)
                     if isinstance(getattr(self, f.name), Provisional))

    @property
    def has_provisional(self) -> bool:
        return bool(self.provisional_fields())

    def __fingerprint__(self):
        return {"label": self.label,
                "expense_escalation": float(self.expense_escalation),
                "expense_inflation": float(self.expense_inflation),
                "account_value_rate": self.account_value_rate,
                "provisional": list(self.provisional_fields()),
                "text": self.text}


#: The 2026 text, carrying what §6.C states.
VM22_PRESCRIBED_2026 = PrescribedAssumptions(
    label="VM-22 §6.C (2026)",
    text="NAIC Valuation Manual, 1 January 2026 edition, chapter VM-22, "
         "Section 6.C. Table 6.1 and Table 6.7 are carried; the other nine "
         "prescribed tables are recorded in docs/sources/ and not "
         "transcribed. The escalation and inflation figures are bracketed "
         "in the text and are therefore provisional.",
)


@dataclass(frozen=True)
class PrescribedExpense:
    """One projection year's prescribed maintenance expense.

    ``provisional`` is ``True`` where any figure behind the number is one
    the text brackets — which, under the 2026 text, is every expense,
    because the escalation is unavoidable.
    """

    amount: float
    base: float
    escalated: float
    account_value_component: float
    provisional: bool

    def __fingerprint__(self):
        return {"amount": self.amount, "base": self.base,
                "escalated": self.escalated,
                "account_value_component": self.account_value_component,
                "provisional": self.provisional}


def maintenance_expense(contract_type: str, valuation_year: int,
                        projection_year: int = 0, *,
                        account_value: float = 0.0,
                        administered: bool = True,
                        basis: PrescribedAssumptions = VM22_PRESCRIBED_2026
                        ) -> PrescribedExpense:
    """§6.C.2: the prescribed maintenance expense for one projection year.

    "Each contract for which the company is responsible for administration
    incurs an annual expense equal to the Base Maintenance Expense
    Assumption shown in the table below for each product type multiplied by
    [1.025]^(valuation year – 2015) in the first projection year, and
    increased by an assumed annual inflation rate of [2.5%] for subsequent
    projection years", plus "[s]even basis points of the projected account
    value for each year in the projection".

    ``administered=False`` is §6.C.2.c — a contract the company does not
    administer, "e.g., if the contract were assumed by the company in a
    reinsurance transaction in which only the risks associated with a
    guaranteed benefit rider were transferred" — which takes $35 and,
    reading the clause as written, **no** account-value component: §6.C.2
    says the expense is "(a) plus (b) … **or** (c)", so (c) stands alone.

    Two escalations, and they are not the same one. The first runs from 2015
    to the **valuation** date and is applied once; the second runs over the
    **projection** and compounds per year. Collapsing them into a single
    exponent is the natural simplification and gives the wrong answer for
    every valuation after 2015.
    """
    if projection_year < 0:
        raise PrescribedError(
            f"projection year {projection_year} is before the valuation; "
            f"§6.C.2's inflation applies to 'subsequent projection years' "
            f"and there are none before the first"
        )
    key = "not_administered" if not administered else contract_type
    if key not in BASE_MAINTENANCE_EXPENSE:
        raise PrescribedError(
            f"§6.C.2's Table 6.1 has no base expense for {contract_type!r}; "
            f"it carries {sorted(BASE_MAINTENANCE_EXPENSE)}"
        )
    base = BASE_MAINTENANCE_EXPENSE[key]
    escalated = base * float(basis.expense_escalation) ** (
        valuation_year - EXPENSE_BASE_YEAR)
    inflated = escalated * (1.0 + float(basis.expense_inflation)) ** \
        projection_year
    account = (0.0 if not administered
               else basis.account_value_rate * float(account_value))
    return PrescribedExpense(
        amount=inflated + account, base=base, escalated=escalated,
        account_value_component=account,
        provisional=basis.has_provisional,
    )


def fx_factor(age, sex: str, *, category: str = "accumulation",
              guaranteed_living_benefit: bool = False) -> np.ndarray:
    r"""Table 6.7's *F*\ :sub:`x`, the adjustment to the 2012 IAM Basic table.

    §6.C.8: the factors "represent adjustments to the 2012 IAM Basic
    Mortality Table brought up to the current period using Projection Scale
    G2 … Such adjustments reflect emerging experience, including the impact
    of how historical mortality improvement has differed from the G2 scale."

    Ages at or below 50 take the first row and ages at or above 105 take
    100%, both as the table states rather than as an extrapolation.

    Only the Accumulation Reserving Category's table is carried. Another
    category is **refused** rather than served from this one: §6.C.8 gives a
    different set per category, and a factor from the wrong one is a
    plausible number nothing downstream would question.
    """
    if category not in FX_CATEGORIES:
        raise PrescribedError(
            f"§6.C.8 has no factor set for {category!r}; it covers "
            f"{list(FX_CATEGORIES)}"
        )
    if category not in FX_CATEGORIES_CARRIED:
        raise PrescribedError(
            f"§6.C.8's factors for {category!r} are recorded in "
            f"docs/sources/vm22-section-6-prescribed-assumptions.md and are "
            f"not transcribed here. Only {list(FX_CATEGORIES_CARRIED)} is "
            f"carried, and serving that table for a different category "
            f"would be a plausible number from the wrong section."
        )
    sex = str(sex).upper()
    if sex not in ("M", "F"):
        raise PrescribedError(
            f"Table 6.7 is quoted by sex; {sex!r} is not 'M' or 'F'"
        )
    if category == "payout_annuity":
        if guaranteed_living_benefit:
            raise PrescribedError(
                "Table 6.8 is not split by guaranteed living benefit; §6.C.8 "
                "gives one pair of columns for the Payout Annuity Reserving "
                "Category, and asking for a split it does not have would be "
                "answered from the accumulation table"
            )
        table = np.array(_FX_PAYOUT, dtype=np.float64)
        column = 1 if sex == "F" else 2
    else:
        column = {("F", False): 1, ("M", False): 2,
                  ("F", True): 3,
                  ("M", True): 4}[(sex, guaranteed_living_benefit)]
        table = np.array(_FX_ACCUMULATION, dtype=np.float64)
    ages = np.clip(np.asarray(age), FX_MIN_AGE, FX_MAX_AGE)
    factors = np.where(ages >= FX_MAX_AGE, 1.0,
                       np.interp(ages, table[:, 0], table[:, column]))
    return factors


def prescribed_mortality_rate(q_2012, g2, fx, n: int):
    """§6.C.8.i: ``q_x^(2012+n) = q_x^2012 (1 − G2_x)^n × F_x``.

    The 2012 IAM Basic Mortality Table (VM-M §2.C) and Projection Scale G2
    (VM-M §1.J.1.c) are **arguments**, not data carried here. They belong to
    another chapter, and this module inventing them would be the same error
    as inventing the prescribed scenarios — see
    :func:`engine.report.vm22.stochastic_exclusion_test`, which takes its
    prescribed inputs the same way and for the same reason.

    Note where the factor sits: *outside* the improvement, multiplying the
    projected rate rather than the base one. Applying it before the
    improvement gives a different number at every ``n`` except zero.
    """
    if n < 0:
        raise PrescribedError(
            f"n is {n}: §6.C.8's formula projects forward from 2012, not back"
        )
    q = np.asarray(q_2012, dtype=np.float64)
    scale = np.asarray(g2, dtype=np.float64)
    if np.any(scale >= 1.0) or np.any(scale < 0.0):
        raise PrescribedError(
            "Projection Scale G2 is an annual improvement rate in [0, 1); "
            "a value outside that would make (1 − G2)^n change sign or grow"
        )
    return q * (1.0 - scale) ** n * np.asarray(fx, dtype=np.float64)


#: Table 6.8, §6.C.8.ii: *F*\ :sub:`x` for individual annuities in the Payout
#: Annuity Reserving Category, other than structured settlements.
#: ``(attained age, female, male)``. Same floor and cap as Table 6.7.
_FX_PAYOUT = (
    (50, 1.2500, 1.0000),
    (51, 1.2500, 1.0000),
    (52, 1.2500, 1.0000),
    (53, 1.2500, 1.0000),
    (54, 1.2500, 1.0000),
    (55, 1.2500, 1.0000),
    (56, 1.2500, 1.0000),
    (57, 1.2500, 1.0000),
    (58, 1.2060, 0.9900),
    (59, 1.1620, 0.9800),
    (60, 1.1180, 0.9700),
    (61, 1.0740, 0.9600),
    (62, 1.0300, 0.9500),
    (63, 1.0100, 0.9540),
    (64, 0.9900, 0.9580),
    (65, 0.9700, 0.9620),
    (66, 0.9500, 0.9660),
    (67, 0.9300, 0.9700),
    (68, 0.9440, 0.9860),
    (69, 0.9580, 1.0020),
    (70, 0.9720, 1.0180),
    (71, 0.9860, 1.0340),
    (72, 1.0000, 1.0500),
    (73, 1.0160, 1.0700),
    (74, 1.0320, 1.0900),
    (75, 1.0480, 1.1100),
    (76, 1.0640, 1.1300),
    (77, 1.0800, 1.1500),
    (78, 1.0800, 1.1600),
    (79, 1.0800, 1.1700),
    (80, 1.0800, 1.1800),
    (81, 1.0800, 1.1900),
    (82, 1.0800, 1.2000),
    (83, 1.0800, 1.2000),
    (84, 1.0800, 1.2000),
    (85, 1.0800, 1.2000),
    (86, 1.0800, 1.2000),
    (87, 1.0800, 1.2000),
    (88, 1.0900, 1.1900),
    (89, 1.1000, 1.1800),
    (90, 1.1100, 1.1700),
    (91, 1.1200, 1.1600),
    (92, 1.1300, 1.1500),
    (93, 1.1300, 1.1500),
    (94, 1.1300, 1.1500),
    (95, 1.1300, 1.1500),
    (96, 1.1300, 1.1500),
    (97, 1.1300, 1.1500),
    (98, 1.1140, 1.1300),
    (99, 1.0980, 1.1100),
    (100, 1.0820, 1.0900),
    (101, 1.0660, 1.0700),
    (102, 1.0500, 1.0500),
    (103, 1.0330, 1.0330),
    (104, 1.0170, 1.0170),
)

#: §6.C.4, Tables 6.2 and 6.3: prescribed partial withdrawal rates for
#: Accumulation Reserving Category contracts, by attained-age band and by
#: whether the contract has a guaranteed living benefit not yet exercised.
#: The bands are the text's own — "59 and under", "60 – 64", … "80 and
#: over" — recorded as their lower bound.
PARTIAL_WITHDRAWAL_BANDS = (0, 60, 65, 70, 75, 80)

_PARTIAL_WITHDRAWALS = {
    # (qualified, without GLB / with GLB prior to exercising)
    "qualified": ((0.0165, 0.0095), (0.0210, 0.0115), (0.0235, 0.0140),
                  (0.0395, 0.0270), (0.0480, 0.0430), (0.0630, 0.0580)),
    "non_qualified": ((0.0160, 0.0115), (0.0160, 0.0115), (0.0160, 0.0115),
                      (0.0160, 0.0165), (0.0160, 0.0165), (0.0160, 0.0165)),
}

#: Which tax treatments §6.C.4 gives a table for.
WITHDRAWAL_TREATMENTS = tuple(_PARTIAL_WITHDRAWALS)


def partial_withdrawal_rate(age, *, qualified: bool,
                            guaranteed_living_benefit: bool = False):
    """§6.C.4, Tables 6.2 and 6.3: the prescribed partial withdrawal rate.

    Banded by attained age, and the bands are the text's rather than a
    smoothing of it — "59 and under" is a step, not the start of a gradient,
    so a life aged 59 and one aged 60 take different rates and nothing here
    interpolates between them. Interpolating would produce a rate the text
    does not contain, at every age between the band edges.

    The **qualified** table grades with age and the **non-qualified** one
    barely moves: 1.60% at every age without a guaranteed living benefit,
    and two values with one. That is the tax treatment showing through —
    required minimum distributions drive withdrawals on qualified money and
    there is no equivalent pressure on non-qualified — and it is why the two
    are separate tables rather than one with an adjustment.
    """
    table = _PARTIAL_WITHDRAWALS["qualified" if qualified
                                 else "non_qualified"]
    ages = np.atleast_1d(np.asarray(age))
    if np.any(ages < 0):
        raise PrescribedError("attained age is not negative")
    column = 1 if guaranteed_living_benefit else 0
    edges = np.asarray(PARTIAL_WITHDRAWAL_BANDS)
    index = np.clip(np.searchsorted(edges, ages, side="right") - 1,
                    0, len(edges) - 1)
    rates = np.array([row[column] for row in table], dtype=np.float64)
    out = rates[index]
    return out if np.ndim(age) else float(out[0])
