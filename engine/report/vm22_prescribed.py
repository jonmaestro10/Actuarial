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
Ten of the eleven tables are here, transcribed from the primary text and
checked against it:

- **Table 6.1**, the base maintenance expense by contract type (§6.C.2.a),
  with §6.C.2.c's $35 for a contract the company does not administer.
- **Tables 6.2 and 6.3**, prescribed partial withdrawal rates for qualified
  and non-qualified contracts (§6.C.4).
- **Tables 6.4 and 6.6**, base lapse rates by years from surrender-charge
  expiry crossed with attained-age band (§6.C.5).
- **Tables 6.7 and 6.8**, the *F*\ :sub:`x` mortality factors for individual
  annuities in the Accumulation and Payout Annuity Reserving Categories
  (§6.C.8.i–ii).
- **Tables 6.9, 6.10 and 6.11**, the *F*\ :sub:`x` factors for structured
  settlements — standard lives, and substandard lives at rate-ups of 1 to 20
  years and of 21 or more (§6.C.8.iii). RFC-071.

The one that remains — **Table 6.5** — is **recorded and not carried**, and
:func:`fx_factor` **refuses** a category whose table is absent rather than
falling back to one that is present. A mortality factor from the wrong
category is a plausible number that no test would catch, which is precisely
the failure mode this chapter has already produced eight times.

**Table 6.5's absence is the specific one, and the reading is exonerated.**
Its second dimension is the *interest guarantee period* rather than attained
age, and its own Guidance Note supplies three worked examples. Two reproduce
exactly under the straightforward reading; Example 3's contract year 5 comes
out at 2.0% where the text says 1.0%.

That is now known to be the **text's** problem rather than the reading's, and
the argument needs no reading at all. 25% occurs at exactly one cell of the
table and 65% at exactly one other; Example 3's own years 4 and 6 therefore
pin the row offset either side of year 5, forcing it to *1 yr after expiry* —
whose three values are 10.0%, 2.0% and 75.0%. **1.0% appears nowhere in any
at-or-after-expiry row.** Column B at that row is 2.0%, which is what the
reading computes. An enumeration of 144 parameterised readings finds none
reproducing Example 3, and none at all once the row header is taken to mean
what it says.

The refusal stands anyway: which of "the Note has a typo" and "an unstated
axis switch" the drafters intend is theirs to say, and carrying the table on
either would put a plausible number in every cell. See
``docs/sources/vm22-section-6-prescribed-assumptions.md``.

**The structured-settlement sets have a second dimension, and it is not the
same one twice.** Table 6.9 bands contract years 1–5 / 6–10 / ≥11; Tables
6.10 and 6.11 band them 1–10 / 11–20 / 21–30 / ≥31. A table whose second
dimension is read wrongly is a plausible number in *every* cell rather than
an obviously missing one, so :func:`fx_factor` takes ``contract_year`` as a
required argument for these categories and refuses it for the two that have
no such axis.

Two things about them are worth knowing before using one. Their base table is
**not** the 2012 IAM Basic table: §6.C.8.iii projects the **1983 IAM Table
'a'** from **2011**, one year earlier and a different table entirely, which
:func:`mortality_basis` carries and :func:`projection_offset` enforces. And
the substandard factors are *lower* than the standard ones — 55% against
300% at the youngest ages — because §6.C.8.iii applies Actuarial Guideline
IX-A's Constant Extra Death loading **before** the factor, so the two
multiply different rates and are not comparable.

What this does **not** build
----------------------------
The additional standard projection amount itself. §6 is the CTEPA method, and
§3.C makes it "only required for disclosure purposes pursuant to VM-31" for
year-end 2026 — so it is not a reserve floor yet, which is why the
assumptions land before the calculation. This module carries the prescribed
inputs; nothing here computes an SPA, and
:mod:`engine.report.vm22` is unchanged.

The mortality basis is a formula over artefacts that live in **VM-M** — the
2012 IAM Basic Mortality Table (VM-M §2.C), the 1983 IAM Table 'a'
(VM-M §1.M) and Projection Scale G2 (VM-M §1.J.1.c). None is carried here,
so :func:`prescribed_mortality_rate` takes them as arguments. That is the
same shape :func:`engine.report.vm22.stochastic_exclusion_test` uses for the
prescribed scenarios, and for the same reason: a chapter's own data is not
this chapter's to invent. Which of the base tables a category calls for
**is** this chapter's, and :func:`mortality_basis` answers it.
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

#: Tables 6.7 and 6.8's own floor and cap. "<=50" is their first row;
#: ">=105" is 100%. **The floor is theirs alone** — the structured
#: settlement tables run from age 2 (see :data:`FX_STRUCTURED_MIN_AGE`), and
#: reusing this one would silently serve a 50-year-old's factor to a child.
FX_MIN_AGE, FX_MAX_AGE = 50, 105

#: Tables 6.9 to 6.11's floor: "≤2". Structured settlements are written on
#: claimants, and a claimant can be an injured child, so the youngest row of
#: these tables is an age Tables 6.7 and 6.8 never reach. The cap is the
#: same ">=105" at 100%.
FX_STRUCTURED_MIN_AGE = 2

#: Table 6.9's contract-year bands, as lower bounds: "Contract Years 1 to 5",
#: "6 to 10", "≥11".
FX_STANDARD_CONTRACT_YEARS = (1, 6, 11)

#: Tables 6.10 and 6.11's, which are a **different** banding of the same
#: axis: "1 to 10", "11 to 20", "21 to 30", "≥31". Three bands against four,
#: and no boundary in common past the first.
FX_SUBSTANDARD_CONTRACT_YEARS = (1, 11, 21, 31)

#: The age rate-up at which §6.C.8.iii moves from Table 6.10 to Table 6.11.
#: 6.10 is headed "age rate-ups of 1-20 years" and 6.11 "≥21 years".
FX_RATE_UP_SPLIT = 21

#: Which Reserving Categories have an *F*\ :sub:`x` table carried here.
#: §6.C.8 gives four and all four are now transcribed (RFC-071); the refusal
#: below stays because the section can grow one.
FX_CATEGORIES_CARRIED = ("accumulation", "payout_annuity",
                         "structured_settlement_standard",
                         "structured_settlement_substandard")

#: The categories §6.C.8 covers. Kept separate from
#: :data:`FX_CATEGORIES_CARRIED` so a refusal can tell a caller whether they
#: asked for something that exists but is absent, or for something the
#: section does not have — a distinction that survives the two lists being
#: momentarily equal.
FX_CATEGORIES = ("accumulation", "payout_annuity",
                 "structured_settlement_standard",
                 "structured_settlement_substandard")


@dataclass(frozen=True)
class MortalityBasis:
    """The base table an *F*\\ :sub:`x` set multiplies, and the year *n* runs
    from.

    §6.C.8 states this per category and they are **not** the same. §6.C.8.i
    and .ii project the 2012 IAM Basic Mortality Table from 2012; §6.C.8.iii
    projects the 1983 IAM Table 'a' from **2011**. Two base tables and two
    base years, and nothing in the shape of ``q (1 − G2)^n × F`` shows which
    pair a caller used — so the pairing is data here rather than a remark in
    a docstring.
    """

    table: str
    vm_m_section: str
    base_year: int


#: §6.C.8's base tables, by Reserving Category. The structured-settlement
#: entries are the reason this mapping exists: an implementation that reached
#: for the 2012 IAM Basic table and the 2012 base year out of habit would be
#: wrong on both counts, and the arithmetic would not complain.
FX_MORTALITY_BASIS = {
    "accumulation": MortalityBasis(
        "2012 IAM Basic Mortality Table", "VM-M §2.C", 2012),
    "payout_annuity": MortalityBasis(
        "2012 IAM Basic Mortality Table", "VM-M §2.C", 2012),
    "structured_settlement_standard": MortalityBasis(
        "1983 IAM Table 'a'", "VM-M §1.M", 2011),
    "structured_settlement_substandard": MortalityBasis(
        "1983 IAM Table 'a'", "VM-M §1.M", 2011),
}


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


#: §6.C's eleven prescribed tables, split by whether this module carries
#: them. Listed rather than counted, and the provenance text below is built
#: from these rather than restating them — the string travels into
#: ``__fingerprint__`` and therefore into every run record citing this set,
#: so a hand-written count is a claim about coverage that can go stale
#: silently while the digest keeps changing for other reasons. It did: the
#: text said "Table 6.1 and Table 6.7 are carried; the other nine …" for
#: as long as RFC-067 had been carrying seven.
TABLES_CARRIED = ("6.1", "6.2", "6.3", "6.4", "6.6", "6.7", "6.8",
                  "6.9", "6.10", "6.11")

#: The one §6.C does state and this module does not transcribe: Table 6.5,
#: whose Guidance Note contradicts its own grid. See :func:`base_lapse_rate`
#: and ``docs/sources/vm22-table-6-5-reading.md``.
TABLES_NOT_CARRIED = ("6.5",)

_CARRIED = ", ".join(f"Table {t}" for t in TABLES_CARRIED)
_ABSENT = ", ".join(f"Table {t}" for t in TABLES_NOT_CARRIED)
# Derived down to the verb, because the count reached one and "the other 1
# … are recorded" is the kind of sentence a reader stops trusting.
_ABSENT_VERB = "is" if len(TABLES_NOT_CARRIED) == 1 else "are"

#: The 2026 text, carrying what §6.C states.
VM22_PRESCRIBED_2026 = PrescribedAssumptions(
    label="VM-22 §6.C (2026)",
    text=(f"NAIC Valuation Manual, 1 January 2026 edition, chapter VM-22, "
          f"Section 6.C. {len(TABLES_CARRIED)} of "
          f"{len(TABLES_CARRIED) + len(TABLES_NOT_CARRIED)} prescribed "
          f"tables are carried ({_CARRIED}); the other "
          f"{len(TABLES_NOT_CARRIED)} ({_ABSENT}) {_ABSENT_VERB} recorded in "
          f"docs/sources/ and not transcribed. The escalation and inflation "
          f"figures are bracketed in the text and are therefore "
          f"provisional."),
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


_STRUCTURED = ("structured_settlement_standard",
               "structured_settlement_substandard")


def _structured_columns(category: str, sex: str, contract_year,
                        rate_up_years):
    """Pick Table 6.9/6.10/6.11 and the column each contract year lands in.

    Split out because the column arithmetic is where this goes wrong. The
    two bandings differ — three bands against four, sharing only their first
    boundary — so a band index computed against the wrong one is in range,
    is off by one or two, and reads a real cell of a real table.
    """
    if contract_year is None:
        raise PrescribedError(
            f"§6.C.8.iii's factors for {category!r} are quoted by contract "
            f"year as well as attained age; contract_year is required. "
            f"Table 6.9 bands 1-5/6-10/>=11 and Tables 6.10 and 6.11 band "
            f"1-10/11-20/21-30/>=31, so there is no band to default to."
        )
    years = np.asarray(contract_year)
    if np.any(years < 1):
        raise PrescribedError(
            "contract year is 1 in the first year of the contract; §6.C.8.iii "
            "has no row below that and a 0 would read the first band as if "
            "it were a year of cover"
        )
    if category == "structured_settlement_standard":
        if rate_up_years is not None:
            raise PrescribedError(
                "Table 6.9 is the standard-lives table and has no rate-up "
                "dimension; a life with an age rate-up is substandard and "
                "belongs to Table 6.10 or 6.11 under "
                "category='structured_settlement_substandard'"
            )
        bands, table = FX_STANDARD_CONTRACT_YEARS, _FX_SS_STANDARD
    else:
        if rate_up_years is None:
            raise PrescribedError(
                "§6.C.8.iii: \"The factors for Substandard lives differ by "
                "the extent of the age rate-up\" — Table 6.10 covers 1 to 20 "
                "years and Table 6.11 covers 21 or more, so rate_up_years is "
                "required and the two tables disagree at every age"
            )
        if np.ndim(rate_up_years) != 0:
            raise PrescribedError(
                "rate_up_years selects between two different tables and is "
                "therefore scalar; call once per rate-up band rather than "
                "letting one lookup straddle Tables 6.10 and 6.11"
            )
        if rate_up_years < 1:
            raise PrescribedError(
                f"an age rate-up of {rate_up_years} is not substandard; "
                f"Table 6.10 starts at a rate-up of 1 year and a life with "
                f"none is a standard life under Table 6.9"
            )
        bands = FX_SUBSTANDARD_CONTRACT_YEARS
        table = (_FX_SS_SUBSTANDARD_1_20 if rate_up_years < FX_RATE_UP_SPLIT
                 else _FX_SS_SUBSTANDARD_21_PLUS)
    band = np.searchsorted(np.asarray(bands), years, side="right") - 1
    # Columns run band-major, female then male, as the header rows print
    # them: (band 1 F, band 1 M, band 2 F, band 2 M, …).
    return (np.array(table, dtype=np.float64),
            1 + 2 * band + (0 if sex == "F" else 1))


def fx_factor(age, sex: str, *, category: str = "accumulation",
              guaranteed_living_benefit: bool = False,
              contract_year=None, rate_up_years=None) -> np.ndarray:
    r"""§6.C.8's *F*\ :sub:`x`, the adjustment to the prescribed base table.

    §6.C.8: the factors "represent adjustments to the 2012 IAM Basic
    Mortality Table brought up to the current period using Projection Scale
    G2 … Such adjustments reflect emerging experience, including the impact
    of how historical mortality improvement has differed from the G2 scale."

    Which base table is adjusted depends on the category, and it is not the
    2012 IAM Basic table for all of them — see :func:`mortality_basis`.

    Tables 6.7 and 6.8 (``accumulation``, ``payout_annuity``) are quoted by
    attained age and sex, floor at age 50 and cap at 105. Tables 6.9 to 6.11
    (the two structured-settlement categories) are quoted by attained age,
    **contract year** and sex, floor at age **2**, and cap at 105 alike. The
    caps are the tables' own statements rather than extrapolation.

    ``contract_year`` is required for a structured settlement and refused for
    the other two, which have no such axis; ``rate_up_years`` is required for
    a substandard structured settlement, because §6.C.8.iii splits those
    between Table 6.10 (rate-ups of 1 to 20 years) and Table 6.11 (21 or
    more), and refused otherwise.

    A category whose table is not carried is **refused** rather than served
    from one that is: §6.C.8 gives a different set per category, and a factor
    from the wrong one is a plausible number nothing downstream would
    question.
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
            f"§6.C.8's tables are quoted by sex; {sex!r} is not 'M' or 'F'"
        )
    if category in _STRUCTURED:
        if guaranteed_living_benefit:
            raise PrescribedError(
                "§6.C.8.iii's structured settlement tables are not split by "
                "guaranteed living benefit; a structured settlement is a "
                "stream of payments under a claim settlement and has no "
                "rider, and answering the split from Table 6.7 would be a "
                "factor from the wrong section"
            )
        table, column = _structured_columns(
            category, sex, contract_year, rate_up_years)
        min_age = FX_STRUCTURED_MIN_AGE
    else:
        if contract_year is not None:
            raise PrescribedError(
                f"Tables 6.7 and 6.8 are quoted by attained age alone; "
                f"{category!r} has no contract-year band, and accepting one "
                f"would let a caller believe a banding had been applied"
            )
        if rate_up_years is not None:
            raise PrescribedError(
                f"an age rate-up is a structured settlement's underwriting "
                f"and {category!r} has no rate-up dimension; §6.C.8 states it "
                f"only at .iii"
            )
        if category == "payout_annuity":
            if guaranteed_living_benefit:
                raise PrescribedError(
                    "Table 6.8 is not split by guaranteed living benefit; "
                    "§6.C.8 gives one pair of columns for the Payout Annuity "
                    "Reserving Category, and asking for a split it does not "
                    "have would be answered from the accumulation table"
                )
            table = np.array(_FX_PAYOUT, dtype=np.float64)
            column = 1 if sex == "F" else 2
        else:
            column = {("F", False): 1, ("M", False): 2,
                      ("F", True): 3,
                      ("M", True): 4}[(sex, guaranteed_living_benefit)]
            table = np.array(_FX_ACCUMULATION, dtype=np.float64)
        min_age = FX_MIN_AGE
    ages = np.clip(np.asarray(age), min_age, FX_MAX_AGE)
    # Age and column are broadcast against each other *before* the lookup:
    # a scalar age with a vector of contract years is the natural projection
    # and would otherwise silently take the first column.
    ages, column = np.broadcast_arrays(ages, np.asarray(column))
    # Every column is graded and the wanted one selected afterwards, so that
    # an array of contract years picks a different column per element
    # without a Python loop.
    graded = np.stack(
        [np.interp(ages, table[:, 0], table[:, c])
         for c in range(1, table.shape[1])], axis=-1)
    picked = np.take_along_axis(graded, (column - 1)[..., None],
                                axis=-1)[..., 0]
    return np.where(ages >= FX_MAX_AGE, 1.0, picked)


def mortality_basis(category: str = "accumulation") -> MortalityBasis:
    """Which base table §6.C.8 projects for ``category``, and from when.

    A mechanism rather than a sentence, because the difference is invisible
    downstream. §6.C.8.i and .ii project the **2012 IAM Basic Mortality
    Table** from 2012; §6.C.8.iii projects the **1983 IAM Table 'a'** from
    **2011**. A caller who reached for the 2012 table and the 2012 base year
    for a structured settlement would be using the wrong rates *and* one
    improvement year too few, and ``q (1 − G2)^n × F`` would return a
    perfectly ordinary number.

    §6.C.8 also covers group annuities, international business and the
    Longevity Reinsurance Reserving Category, which take the 1994 GAM Table
    with Projection Scale AA and have **no** *F*\\ :sub:`x` at all — a
    different shape, not a missing table, so they are refused here.
    """
    try:
        return FX_MORTALITY_BASIS[category]
    except KeyError:
        raise PrescribedError(
            f"§6.C.8 states no F_x base table for {category!r}; it gives one "
            f"for {list(FX_MORTALITY_BASIS)}. Group annuities, international "
            f"business and the Longevity Reinsurance Reserving Category take "
            f"the 1994 GAM Table with Projection Scale AA and carry no F_x."
        ) from None


def projection_offset(valuation_year: int, *,
                      category: str = "accumulation") -> int:
    """The ``n`` in §6.C.8's formula, counted from the category's own base.

    The offset is where the two base years bite, and it is the one place the
    module can catch the confusion: ``projection_offset(2026)`` is 14 and
    ``projection_offset(2026, category="structured_settlement_standard")``
    is **15**, because §6.C.8.iii counts from 2011.
    """
    basis = mortality_basis(category)
    if valuation_year < basis.base_year:
        raise PrescribedError(
            f"{valuation_year} is before {basis.base_year}, the base year of "
            f"{basis.table} for {category!r}; §6.C.8 projects forward from it "
            f"and a negative n would run the improvement backwards"
        )
    return int(valuation_year) - basis.base_year


def prescribed_mortality_rate(q_base, g2, fx, n: int):
    """§6.C.8: ``q_x^(base+n) = q_x^base (1 − G2_x)^n × F_x``.

    One formula over two bases. §6.C.8.i and .ii write it
    ``q_x^(2012+n) = q_x^2012 …`` over the 2012 IAM Basic Mortality Table;
    §6.C.8.iii writes it ``q_x^(2011+n) = q_x^2011 …`` over the 1983 IAM
    Table 'a'. The arithmetic is identical and the inputs are not, which is
    why :func:`mortality_basis` exists and why ``n`` should come from
    :func:`projection_offset` rather than from a subtraction written at the
    call site.

    The base table (VM-M §2.C or §1.M) and Projection Scale G2 (VM-M
    §1.J.1.c) are **arguments**, not data carried here. They belong to
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
            f"n is {n}: §6.C.8's formula projects forward from its base "
            f"year, not back"
        )
    q = np.asarray(q_base, dtype=np.float64)
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

#: Table 6.9, §6.C.8.iii: *F*\ :sub:`x` for structured settlement contracts
#: on **standard** lives. ``(attained age, then female and male for each of
#: contract years 1-5, 6-10 and >=11)`` — six value columns, band-major, in
#: the order the two header rows print them. Age "<=2" is the first row and
#: ">=105" is 100%; the cap row is the code's, as it is for Tables 6.7
#: and 6.8.
_FX_SS_STANDARD = (
    (2, 3.0000, 3.0000, 3.0000, 3.0000, 3.6500, 3.7500),
    (3, 3.0600, 3.0600, 3.0700, 3.0600, 3.7400, 3.8100),
    (4, 3.1200, 3.1200, 3.1400, 3.1200, 3.8300, 3.8700),
    (5, 3.1800, 3.1800, 3.2100, 3.1800, 3.9200, 3.9300),
    (6, 3.2400, 3.2400, 3.2800, 3.2400, 4.0100, 3.9900),
    (7, 3.3000, 3.3000, 3.3500, 3.3000, 4.1000, 4.0500),
    (8, 3.3500, 3.3000, 3.3900, 3.3000, 4.1500, 4.0500),
    (9, 3.4000, 3.3000, 3.4300, 3.3000, 4.2000, 4.0500),
    (10, 3.4500, 3.3000, 3.4700, 3.3000, 4.2500, 4.0500),
    (11, 3.5000, 3.3000, 3.5100, 3.3000, 4.3000, 4.0500),
    (12, 3.5500, 3.3000, 3.5500, 3.3000, 4.3500, 4.0500),
    (13, 3.5500, 3.3100, 3.5500, 3.3100, 4.3500, 4.0700),
    (14, 3.5500, 3.3200, 3.5500, 3.3200, 4.3500, 4.0900),
    (15, 3.5500, 3.3300, 3.5500, 3.3300, 4.3500, 4.1100),
    (16, 3.5500, 3.3400, 3.5500, 3.3400, 4.3500, 4.1300),
    (17, 3.5500, 3.3500, 3.5500, 3.3500, 4.3500, 4.1500),
    (18, 3.5400, 3.3500, 3.5400, 3.3500, 4.3400, 4.1400),
    (19, 3.5300, 3.3500, 3.5300, 3.3500, 4.3300, 4.1300),
    (20, 3.5200, 3.3500, 3.5200, 3.3500, 4.3200, 4.1200),
    (21, 3.5100, 3.3500, 3.5100, 3.3500, 4.3100, 4.1100),
    (22, 3.5000, 3.3500, 3.5000, 3.3500, 4.3000, 4.1000),
    (23, 3.5000, 3.3800, 3.5000, 3.3800, 4.2900, 4.1400),
    (24, 3.5000, 3.4100, 3.5000, 3.4100, 4.2800, 4.1800),
    (25, 3.5000, 3.4400, 3.5000, 3.4400, 4.2700, 4.2200),
    (26, 3.5000, 3.4700, 3.5000, 3.4700, 4.2600, 4.2600),
    (27, 3.5000, 3.5000, 3.5000, 3.5000, 4.2500, 4.3000),
    (28, 3.5500, 3.5800, 3.5500, 3.5800, 4.3200, 4.4000),
    (29, 3.6000, 3.6600, 3.6000, 3.6600, 4.3900, 4.5000),
    (30, 3.6500, 3.7400, 3.6500, 3.7400, 4.4600, 4.6000),
    (31, 3.7000, 3.8200, 3.7000, 3.8200, 4.5300, 4.7000),
    (32, 3.7500, 3.9000, 3.7500, 3.9000, 4.6000, 4.8000),
    (33, 3.7500, 3.9200, 3.7500, 3.9200, 4.6000, 4.8200),
    (34, 3.7500, 3.9400, 3.7500, 3.9400, 4.6000, 4.8400),
    (35, 3.7500, 3.9600, 3.7500, 3.9600, 4.6000, 4.8600),
    (36, 3.7500, 3.9800, 3.7500, 3.9800, 4.6000, 4.8800),
    (37, 3.7500, 4.0000, 3.7500, 4.0000, 4.6000, 4.9000),
    (38, 3.5900, 3.8700, 3.5900, 3.8700, 4.4400, 4.7800),
    (39, 3.4300, 3.7400, 3.4300, 3.7400, 4.2800, 4.6600),
    (40, 3.2700, 3.6100, 3.2700, 3.6100, 4.1200, 4.5400),
    (41, 3.1100, 3.4800, 3.1100, 3.4800, 3.9600, 4.4200),
    (42, 2.9500, 3.3500, 2.9500, 3.3500, 3.8000, 4.3000),
    (43, 2.7800, 3.1200, 2.7900, 3.1200, 3.6300, 4.0400),
    (44, 2.6100, 2.8900, 2.6300, 2.8900, 3.4600, 3.7800),
    (45, 2.4400, 2.6600, 2.4700, 2.6600, 3.2900, 3.5200),
    (46, 2.2700, 2.4300, 2.3100, 2.4300, 3.1200, 3.2600),
    (47, 2.1000, 2.2000, 2.1500, 2.2000, 2.9500, 3.0000),
    (48, 2.0600, 2.1300, 2.1000, 2.1300, 2.8800, 2.9100),
    (49, 2.0200, 2.0600, 2.0500, 2.0600, 2.8100, 2.8200),
    (50, 1.9800, 1.9900, 2.0000, 1.9900, 2.7400, 2.7300),
    (51, 1.9400, 1.9200, 1.9500, 1.9200, 2.6700, 2.6400),
    (52, 1.9000, 1.8500, 1.9000, 1.8500, 2.6000, 2.5500),
    (53, 1.8900, 1.8500, 1.8900, 1.8500, 2.5800, 2.5400),
    (54, 1.8800, 1.8500, 1.8800, 1.8500, 2.5600, 2.5300),
    (55, 1.8700, 1.8500, 1.8700, 1.8500, 2.5400, 2.5200),
    (56, 1.8600, 1.8500, 1.8600, 1.8500, 2.5200, 2.5100),
    (57, 1.8500, 1.8500, 1.8500, 1.8500, 2.5000, 2.5000),
    (58, 1.7900, 1.8000, 1.8200, 1.8300, 2.4500, 2.4600),
    (59, 1.7300, 1.7500, 1.7900, 1.8100, 2.4000, 2.4200),
    (60, 1.6700, 1.7000, 1.7600, 1.7900, 2.3500, 2.3800),
    (61, 1.6100, 1.6500, 1.7300, 1.7700, 2.3000, 2.3400),
    (62, 1.5500, 1.6000, 1.7000, 1.7500, 2.2500, 2.3000),
    (63, 1.4900, 1.5400, 1.6600, 1.7200, 2.1800, 2.2400),
    (64, 1.4300, 1.4800, 1.6200, 1.6900, 2.1100, 2.1800),
    (65, 1.3700, 1.4200, 1.5800, 1.6600, 2.0400, 2.1200),
    (66, 1.3100, 1.3600, 1.5400, 1.6300, 1.9700, 2.0600),
    (67, 1.2500, 1.3000, 1.5000, 1.6000, 1.9000, 2.0000),
    (68, 1.2300, 1.2800, 1.4900, 1.5800, 1.8400, 1.9300),
    (69, 1.2100, 1.2600, 1.4800, 1.5600, 1.7800, 1.8600),
    (70, 1.1900, 1.2400, 1.4700, 1.5400, 1.7200, 1.7900),
    (71, 1.1700, 1.2200, 1.4600, 1.5200, 1.6600, 1.7200),
    (72, 1.1500, 1.2000, 1.4500, 1.5000, 1.6000, 1.6500),
    (73, 1.1400, 1.1800, 1.4300, 1.4800, 1.5600, 1.6100),
    (74, 1.1300, 1.1600, 1.4100, 1.4600, 1.5200, 1.5700),
    (75, 1.1200, 1.1400, 1.3900, 1.4400, 1.4800, 1.5300),
    (76, 1.1100, 1.1200, 1.3700, 1.4200, 1.4400, 1.4900),
    (77, 1.1000, 1.1000, 1.3500, 1.4000, 1.4000, 1.4500),
    (78, 1.1000, 1.1000, 1.3300, 1.3700, 1.3700, 1.4100),
    (79, 1.1000, 1.1000, 1.3100, 1.3400, 1.3400, 1.3700),
    (80, 1.1000, 1.1000, 1.2900, 1.3100, 1.3100, 1.3300),
    (81, 1.1000, 1.1000, 1.2700, 1.2800, 1.2800, 1.2900),
    (82, 1.1000, 1.1000, 1.2500, 1.2500, 1.2500, 1.2500),
    (83, 1.1000, 1.1000, 1.2300, 1.2300, 1.2300, 1.2300),
    (84, 1.1000, 1.1000, 1.2100, 1.2100, 1.2100, 1.2100),
    (85, 1.1000, 1.1000, 1.1900, 1.1900, 1.1900, 1.1900),
    (86, 1.1000, 1.1000, 1.1700, 1.1700, 1.1700, 1.1700),
    (87, 1.1000, 1.1000, 1.1500, 1.1500, 1.1500, 1.1500),
    (88, 1.1000, 1.1000, 1.1400, 1.1400, 1.1400, 1.1400),
    (89, 1.1000, 1.1000, 1.1300, 1.1300, 1.1300, 1.1300),
    (90, 1.1000, 1.1000, 1.1200, 1.1200, 1.1200, 1.1200),
    (91, 1.1000, 1.1000, 1.1100, 1.1100, 1.1100, 1.1100),
    (92, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (93, 1.0900, 1.0900, 1.0900, 1.0900, 1.0900, 1.0900),
    (94, 1.0800, 1.0800, 1.0800, 1.0800, 1.0800, 1.0800),
    (95, 1.0700, 1.0700, 1.0700, 1.0700, 1.0700, 1.0700),
    (96, 1.0600, 1.0600, 1.0600, 1.0600, 1.0600, 1.0600),
    (97, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (98, 1.0400, 1.0400, 1.0400, 1.0400, 1.0400, 1.0400),
    (99, 1.0300, 1.0300, 1.0300, 1.0300, 1.0300, 1.0300),
    (100, 1.0200, 1.0200, 1.0200, 1.0200, 1.0200, 1.0200),
    (101, 1.0100, 1.0100, 1.0100, 1.0100, 1.0100, 1.0100),
    (102, 1.0000, 1.0000, 1.0000, 1.0000, 1.0000, 1.0000),
    (103, 1.0000, 1.0000, 1.0000, 1.0000, 1.0000, 1.0000),
    (104, 1.0000, 1.0000, 1.0000, 1.0000, 1.0000, 1.0000),
)

#: Table 6.10, §6.C.8.iii: *F*\ :sub:`x` for structured settlements on
#: **substandard** lives with age rate-ups of 1 to 20 years. Eight value
#: columns, because the banding is *not* Table 6.9's: contract years 1-10,
#: 11-20, 21-30 and >=31, female and male within each.
_FX_SS_SUBSTANDARD_1_20 = (
    (2, 0.5500, 0.5500, 0.5500, 0.5500, 0.5500, 0.5500, 0.5500, 0.5500),
    (3, 0.5700, 0.5700, 0.5700, 0.5700, 0.5700, 0.5700, 0.5700, 0.5700),
    (4, 0.5900, 0.5900, 0.5900, 0.5900, 0.5900, 0.5900, 0.5900, 0.5900),
    (5, 0.6100, 0.6100, 0.6100, 0.6100, 0.6100, 0.6100, 0.6100, 0.6100),
    (6, 0.6300, 0.6300, 0.6300, 0.6300, 0.6300, 0.6300, 0.6300, 0.6300),
    (7, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (8, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (9, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (10, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (11, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (12, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (13, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (14, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (15, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (16, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (17, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (18, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (19, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (20, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (21, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (22, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (23, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (24, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (25, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (26, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (27, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500, 0.6500),
    (28, 0.6600, 0.6700, 0.6600, 0.6700, 0.6600, 0.6700, 0.6600, 0.6700),
    (29, 0.6700, 0.6900, 0.6700, 0.6900, 0.6700, 0.6900, 0.6700, 0.6900),
    (30, 0.6800, 0.7100, 0.6800, 0.7100, 0.6800, 0.7100, 0.6800, 0.7100),
    (31, 0.6900, 0.7300, 0.6900, 0.7300, 0.6900, 0.7300, 0.6900, 0.7300),
    (32, 0.7000, 0.7500, 0.7000, 0.7500, 0.7000, 0.7500, 0.7000, 0.7500),
    (33, 0.7100, 0.7500, 0.7100, 0.7600, 0.7200, 0.7700, 0.7200, 0.7700),
    (34, 0.7200, 0.7500, 0.7200, 0.7700, 0.7400, 0.7900, 0.7400, 0.7900),
    (35, 0.7300, 0.7500, 0.7300, 0.7800, 0.7600, 0.8100, 0.7600, 0.8100),
    (36, 0.7400, 0.7500, 0.7400, 0.7900, 0.7800, 0.8300, 0.7800, 0.8300),
    (37, 0.7500, 0.7500, 0.7500, 0.8000, 0.8000, 0.8500, 0.8000, 0.8500),
    (38, 0.7500, 0.7700, 0.8100, 0.8800, 0.9000, 0.9800, 0.9300, 1.0100),
    (39, 0.7500, 0.7900, 0.8700, 0.9600, 1.0000, 1.1100, 1.0600, 1.1700),
    (40, 0.7500, 0.8100, 0.9300, 1.0400, 1.1000, 1.2400, 1.1900, 1.3300),
    (41, 0.7500, 0.8300, 0.9900, 1.1200, 1.2000, 1.3700, 1.3200, 1.4900),
    (42, 0.7500, 0.8500, 1.0500, 1.2000, 1.3000, 1.5000, 1.4500, 1.6500),
    (43, 0.7500, 0.8400, 1.0700, 1.1900, 1.3400, 1.5000, 1.4900, 1.6500),
    (44, 0.7500, 0.8300, 1.0900, 1.1800, 1.3800, 1.5000, 1.5300, 1.6500),
    (45, 0.7500, 0.8200, 1.1100, 1.1700, 1.4200, 1.5000, 1.5700, 1.6500),
    (46, 0.7500, 0.8100, 1.1300, 1.1600, 1.4600, 1.5000, 1.6100, 1.6500),
    (47, 0.7500, 0.8000, 1.1500, 1.1500, 1.5000, 1.5000, 1.6500, 1.6500),
    (48, 0.7600, 0.8000, 1.1600, 1.1500, 1.5000, 1.5000, 1.6600, 1.6500),
    (49, 0.7700, 0.8000, 1.1700, 1.1500, 1.5000, 1.5000, 1.6700, 1.6500),
    (50, 0.7800, 0.8000, 1.1800, 1.1500, 1.5000, 1.5000, 1.6800, 1.6500),
    (51, 0.7900, 0.8000, 1.1900, 1.1500, 1.5000, 1.5000, 1.6900, 1.6500),
    (52, 0.8000, 0.8000, 1.2000, 1.1500, 1.5000, 1.5000, 1.7000, 1.6500),
    (53, 0.8200, 0.8200, 1.2300, 1.1900, 1.5500, 1.5400, 1.7400, 1.7000),
    (54, 0.8400, 0.8400, 1.2600, 1.2300, 1.6000, 1.5800, 1.7800, 1.7500),
    (55, 0.8600, 0.8600, 1.2900, 1.2700, 1.6500, 1.6200, 1.8200, 1.8000),
    (56, 0.8800, 0.8800, 1.3200, 1.3100, 1.7000, 1.6600, 1.8600, 1.8500),
    (57, 0.9000, 0.9000, 1.3500, 1.3500, 1.7500, 1.7000, 1.9000, 1.9000),
    (58, 0.9000, 0.9100, 1.3500, 1.3600, 1.7500, 1.7200, 1.9100, 1.9200),
    (59, 0.9000, 0.9200, 1.3500, 1.3700, 1.7500, 1.7400, 1.9200, 1.9400),
    (60, 0.9000, 0.9300, 1.3500, 1.3800, 1.7500, 1.7600, 1.9300, 1.9600),
    (61, 0.9000, 0.9400, 1.3500, 1.3900, 1.7500, 1.7800, 1.9400, 1.9800),
    (62, 0.9000, 0.9500, 1.3500, 1.4000, 1.7500, 1.8000, 1.9500, 2.0000),
    (63, 0.8900, 0.9400, 1.3300, 1.3800, 1.7200, 1.7800, 1.9200, 1.9800),
    (64, 0.8800, 0.9300, 1.3100, 1.3600, 1.6900, 1.7600, 1.8900, 1.9600),
    (65, 0.8700, 0.9200, 1.2900, 1.3400, 1.6600, 1.7400, 1.8600, 1.9400),
    (66, 0.8600, 0.9100, 1.2700, 1.3200, 1.6300, 1.7200, 1.8300, 1.9200),
    (67, 0.8500, 0.9000, 1.2500, 1.3000, 1.6000, 1.7000, 1.8000, 1.9000),
    (68, 0.8400, 0.8900, 1.2400, 1.2900, 1.5900, 1.6800, 1.7800, 1.8800),
    (69, 0.8300, 0.8800, 1.2300, 1.2800, 1.5800, 1.6600, 1.7600, 1.8600),
    (70, 0.8200, 0.8700, 1.2200, 1.2700, 1.5700, 1.6400, 1.7400, 1.8400),
    (71, 0.8100, 0.8600, 1.2100, 1.2600, 1.5600, 1.6200, 1.7200, 1.8200),
    (72, 0.8000, 0.8500, 1.2000, 1.2500, 1.5500, 1.6000, 1.7000, 1.8000),
    (73, 0.8000, 0.8500, 1.1900, 1.2400, 1.5300, 1.5800, 1.6800, 1.7700),
    (74, 0.8000, 0.8500, 1.1800, 1.2300, 1.5100, 1.5600, 1.6600, 1.7400),
    (75, 0.8000, 0.8500, 1.1700, 1.2200, 1.4900, 1.5400, 1.6400, 1.7100),
    (76, 0.8000, 0.8500, 1.1600, 1.2100, 1.4700, 1.5200, 1.6200, 1.6800),
    (77, 0.8000, 0.8500, 1.1500, 1.2000, 1.4500, 1.5000, 1.6000, 1.6500),
    (78, 0.8400, 0.8800, 1.1400, 1.1800, 1.4000, 1.4400, 1.5300, 1.5700),
    (79, 0.8800, 0.9100, 1.1300, 1.1600, 1.3500, 1.3800, 1.4600, 1.4900),
    (80, 0.9200, 0.9400, 1.1200, 1.1400, 1.3000, 1.3200, 1.3900, 1.4100),
    (81, 0.9600, 0.9700, 1.1100, 1.1200, 1.2500, 1.2600, 1.3200, 1.3300),
    (82, 1.0000, 1.0000, 1.1000, 1.1000, 1.2000, 1.2000, 1.2500, 1.2500),
    (83, 1.0200, 1.0200, 1.1000, 1.1000, 1.1800, 1.1800, 1.2200, 1.2200),
    (84, 1.0400, 1.0400, 1.1000, 1.1000, 1.1600, 1.1600, 1.1900, 1.1900),
    (85, 1.0600, 1.0600, 1.1000, 1.1000, 1.1400, 1.1400, 1.1600, 1.1600),
    (86, 1.0800, 1.0800, 1.1000, 1.1000, 1.1200, 1.1200, 1.1300, 1.1300),
    (87, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (88, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (89, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (90, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (91, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (92, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000, 1.1000),
    (93, 1.0900, 1.0900, 1.0900, 1.0900, 1.0900, 1.0900, 1.0900, 1.0900),
    (94, 1.0800, 1.0800, 1.0800, 1.0800, 1.0800, 1.0800, 1.0800, 1.0800),
    (95, 1.0700, 1.0700, 1.0700, 1.0700, 1.0700, 1.0700, 1.0700, 1.0700),
    (96, 1.0600, 1.0600, 1.0600, 1.0600, 1.0600, 1.0600, 1.0600, 1.0600),
    (97, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (98, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (99, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (100, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (101, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (102, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500, 1.0500),
    (103, 1.0330, 1.0330, 1.0330, 1.0330, 1.0330, 1.0330, 1.0330, 1.0330),
    (104, 1.0170, 1.0170, 1.0170, 1.0170, 1.0170, 1.0170, 1.0170, 1.0170),
)

#: Table 6.11, §6.C.8.iii: the same eight columns as Table 6.10, for age
#: rate-ups of 21 years or more. A separate table rather than a shift of
#: 6.10 — the two disagree at nearly every cell.
_FX_SS_SUBSTANDARD_21_PLUS = (
    (2, 0.5500, 0.5500, 0.5500, 0.5500, 0.7000, 0.7500, 0.7000, 0.7000),
    (3, 0.5700, 0.5700, 0.5700, 0.5700, 0.7200, 0.7600, 0.7200, 0.7200),
    (4, 0.5900, 0.5900, 0.5900, 0.5900, 0.7400, 0.7700, 0.7400, 0.7400),
    (5, 0.6100, 0.6100, 0.6100, 0.6100, 0.7600, 0.7800, 0.7600, 0.7600),
    (6, 0.6300, 0.6300, 0.6300, 0.6300, 0.7800, 0.7900, 0.7800, 0.7800),
    (7, 0.6500, 0.6500, 0.6500, 0.6500, 0.8000, 0.8000, 0.8000, 0.8000),
    (8, 0.6500, 0.6500, 0.6500, 0.6500, 0.8100, 0.8000, 0.8100, 0.8000),
    (9, 0.6500, 0.6500, 0.6500, 0.6500, 0.8200, 0.8000, 0.8200, 0.8000),
    (10, 0.6500, 0.6500, 0.6500, 0.6500, 0.8300, 0.8000, 0.8300, 0.8000),
    (11, 0.6500, 0.6500, 0.6500, 0.6500, 0.8400, 0.8000, 0.8400, 0.8000),
    (12, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (13, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (14, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (15, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (16, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (17, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (18, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (19, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (20, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (21, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (22, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8000, 0.8500, 0.8000),
    (23, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8100, 0.8500, 0.8100),
    (24, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8200, 0.8500, 0.8200),
    (25, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8300, 0.8500, 0.8300),
    (26, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8400, 0.8500, 0.8400),
    (27, 0.6500, 0.6500, 0.6500, 0.6500, 0.8500, 0.8500, 0.8500, 0.8500),
    (28, 0.6600, 0.6700, 0.6600, 0.6700, 0.8600, 0.8700, 0.8600, 0.8700),
    (29, 0.6700, 0.6900, 0.6700, 0.6900, 0.8700, 0.8900, 0.8700, 0.8900),
    (30, 0.6800, 0.7100, 0.6800, 0.7100, 0.8800, 0.9100, 0.8800, 0.9100),
    (31, 0.6900, 0.7300, 0.6900, 0.7300, 0.8900, 0.9300, 0.8900, 0.9300),
    (32, 0.7000, 0.7500, 0.7000, 0.7500, 0.9000, 0.9500, 0.9000, 0.9500),
    (33, 0.7100, 0.7600, 0.7100, 0.7600, 0.9100, 0.9600, 0.9200, 0.9700),
    (34, 0.7200, 0.7700, 0.7200, 0.7700, 0.9200, 0.9700, 0.9400, 0.9900),
    (35, 0.7300, 0.7800, 0.7300, 0.7800, 0.9300, 0.9800, 0.9600, 1.0100),
    (36, 0.7400, 0.7900, 0.7400, 0.7900, 0.9400, 0.9900, 0.9800, 1.0300),
    (37, 0.7500, 0.8000, 0.7500, 0.8000, 0.9500, 1.0000, 1.0000, 1.0500),
    (38, 0.7700, 0.8300, 0.7900, 0.8500, 0.9800, 1.0500, 1.0700, 1.1500),
    (39, 0.7900, 0.8600, 0.8300, 0.9000, 1.0100, 1.1000, 1.1400, 1.2500),
    (40, 0.8100, 0.8900, 0.8700, 0.9500, 1.0400, 1.1500, 1.2100, 1.3500),
    (41, 0.8300, 0.9200, 0.9100, 1.0000, 1.0700, 1.2000, 1.2800, 1.4500),
    (42, 0.8500, 0.9500, 0.9500, 1.0500, 1.1000, 1.2500, 1.3500, 1.5500),
    (43, 0.8500, 0.9400, 0.9600, 1.0400, 1.1100, 1.2300, 1.3700, 1.5400),
    (44, 0.8500, 0.9300, 0.9700, 1.0300, 1.1200, 1.2100, 1.3900, 1.5300),
    (45, 0.8500, 0.9200, 0.9800, 1.0200, 1.1300, 1.1900, 1.4100, 1.5200),
    (46, 0.8500, 0.9100, 0.9900, 1.0100, 1.1400, 1.1700, 1.4300, 1.5100),
    (47, 0.8500, 0.9000, 1.0000, 1.0000, 1.1500, 1.1500, 1.4500, 1.5000),
    (48, 0.8600, 0.9000, 1.0000, 1.0000, 1.1600, 1.1500, 1.4600, 1.5000),
    (49, 0.8700, 0.9000, 1.0000, 1.0000, 1.1700, 1.1500, 1.4700, 1.5000),
    (50, 0.8800, 0.9000, 1.0000, 1.0000, 1.1800, 1.1500, 1.4800, 1.5000),
    (51, 0.8900, 0.9000, 1.0000, 1.0000, 1.1900, 1.1500, 1.4900, 1.5000),
    (52, 0.9000, 0.9000, 1.0000, 1.0000, 1.2000, 1.1500, 1.5000, 1.5000),
    (53, 0.9200, 0.9200, 1.0300, 1.0300, 1.2300, 1.1900, 1.5500, 1.5400),
    (54, 0.9400, 0.9400, 1.0600, 1.0600, 1.2600, 1.2300, 1.6000, 1.5800),
    (55, 0.9600, 0.9600, 1.0900, 1.0900, 1.2900, 1.2700, 1.6500, 1.6200),
    (56, 0.9800, 0.9800, 1.1200, 1.1200, 1.3200, 1.3100, 1.7000, 1.6600),
    (57, 1.0000, 1.0000, 1.1500, 1.1500, 1.3500, 1.3500, 1.7500, 1.7000),
    (58, 1.0100, 1.0100, 1.1500, 1.1600, 1.3500, 1.3600, 1.7500, 1.7200),
    (59, 1.0200, 1.0200, 1.1500, 1.1700, 1.3500, 1.3700, 1.7500, 1.7400),
    (60, 1.0300, 1.0300, 1.1500, 1.1800, 1.3500, 1.3800, 1.7500, 1.7600),
    (61, 1.0400, 1.0400, 1.1500, 1.1900, 1.3500, 1.3900, 1.7500, 1.7800),
    (62, 1.0500, 1.0500, 1.1500, 1.2000, 1.3500, 1.4000, 1.7500, 1.8000),
    (63, 1.0300, 1.0400, 1.1400, 1.1800, 1.3300, 1.3800, 1.7200, 1.7800),
    (64, 1.0100, 1.0300, 1.1300, 1.1600, 1.3100, 1.3600, 1.6900, 1.7600),
    (65, 0.9900, 1.0200, 1.1200, 1.1400, 1.2900, 1.3400, 1.6600, 1.7400),
    (66, 0.9700, 1.0100, 1.1100, 1.1200, 1.2700, 1.3200, 1.6300, 1.7200),
    (67, 0.9500, 1.0000, 1.1000, 1.1000, 1.2500, 1.3000, 1.6000, 1.7000),
    (68, 0.9400, 0.9900, 1.0900, 1.0900, 1.2400, 1.2900, 1.5900, 1.6800),
    (69, 0.9300, 0.9800, 1.0800, 1.0800, 1.2300, 1.2800, 1.5800, 1.6600),
    (70, 0.9200, 0.9700, 1.0700, 1.0700, 1.2200, 1.2700, 1.5700, 1.6400),
    (71, 0.9100, 0.9600, 1.0600, 1.0600, 1.2100, 1.2600, 1.5600, 1.6200),
    (72, 0.9000, 0.9500, 1.0500, 1.0500, 1.2000, 1.2500, 1.5500, 1.6000),
    (73, 0.9000, 0.9400, 1.0400, 1.0400, 1.1900, 1.2400, 1.5300, 1.5800),
    (74, 0.9000, 0.9300, 1.0300, 1.0300, 1.1800, 1.2300, 1.5100, 1.5600),
    (75, 0.9000, 0.9200, 1.0200, 1.0200, 1.1700, 1.2200, 1.4900, 1.5400),
    (76, 0.9000, 0.9100, 1.0100, 1.0100, 1.1600, 1.2100, 1.4700, 1.5200),
    (77, 0.9000, 0.9000, 1.0000, 1.0000, 1.1500, 1.2000, 1.4500, 1.5000),
    (78, 0.9000, 0.9000, 0.9900, 0.9900, 1.1200, 1.1600, 1.3800, 1.4200),
    (79, 0.9000, 0.9000, 0.9800, 0.9800, 1.0900, 1.1200, 1.3100, 1.3400),
    (80, 0.9000, 0.9000, 0.9700, 0.9700, 1.0600, 1.0800, 1.2400, 1.2600),
    (81, 0.9000, 0.9000, 0.9600, 0.9600, 1.0300, 1.0400, 1.1700, 1.1800),
    (82, 0.9000, 0.9000, 0.9500, 0.9500, 1.0000, 1.0000, 1.1000, 1.1000),
    (83, 0.9100, 0.9100, 0.9500, 0.9500, 0.9900, 0.9900, 1.0700, 1.0700),
    (84, 0.9200, 0.9200, 0.9500, 0.9500, 0.9800, 0.9800, 1.0400, 1.0400),
    (85, 0.9300, 0.9300, 0.9500, 0.9500, 0.9700, 0.9700, 1.0100, 1.0100),
    (86, 0.9400, 0.9400, 0.9500, 0.9500, 0.9600, 0.9600, 0.9800, 0.9800),
    (87, 0.9500, 0.9500, 0.9500, 0.9500, 0.9500, 0.9500, 0.9500, 0.9500),
    (88, 0.9400, 0.9400, 0.9400, 0.9400, 0.9400, 0.9400, 0.9400, 0.9400),
    (89, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300, 0.9300),
    (90, 0.9200, 0.9200, 0.9200, 0.9200, 0.9200, 0.9200, 0.9200, 0.9200),
    (91, 0.9100, 0.9100, 0.9100, 0.9100, 0.9100, 0.9100, 0.9100, 0.9100),
    (92, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (93, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (94, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (95, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (96, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (97, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (98, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (99, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (100, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (101, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (102, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000, 0.9000),
    (103, 0.9330, 0.9330, 0.9330, 0.9330, 0.9330, 0.9330, 0.9330, 0.9330),
    (104, 0.9670, 0.9670, 0.9670, 0.9670, 0.9670, 0.9670, 0.9670, 0.9670),
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


#: §6.C.5, Tables 6.4 and 6.6: base lapse rates by years before or after
#: surrender-charge expiry, crossed with attained-age band. Row order is the
#: text's, running from long after expiry to long before it.
SURRENDER_CHARGE_ROWS = ("5+ after", "4 after", "3 after", "2 after",
                         "1 after", "upon", "1 to", "2 to", "3 to", "4 to",
                         "5+ to")

#: The attained-age bands Tables 6.4 and 6.6 are quoted over, as lower
#: bounds. "Before 60", "60 to 69", "70 to 79", "80 and above".
LAPSE_AGE_BANDS = (0, 60, 70, 80)

_BASE_LAPSE = {
    # Table 6.4: indexed annuities with no guaranteed living benefits.
    "indexed": ((0.065, 0.070, 0.060, 0.050), (0.080, 0.085, 0.065, 0.050),
                (0.085, 0.095, 0.070, 0.055), (0.110, 0.120, 0.090, 0.070),
                (0.150, 0.175, 0.135, 0.090), (0.335, 0.415, 0.370, 0.235),
                (0.045, 0.035, 0.040, 0.040), (0.040, 0.035, 0.030, 0.030),
                (0.025, 0.020, 0.020, 0.020), (0.030, 0.025, 0.025, 0.025),
                (0.020, 0.025, 0.020, 0.015)),
    # Table 6.6: indexed and fixed annuities *with* guaranteed living
    # benefits. Flat across the after-expiry rows, which the with-benefit
    # contract holder's behaviour is: they are not leaving.
    "with_glb": ((0.115, 0.065, 0.045, 0.040), (0.115, 0.065, 0.045, 0.040),
                 (0.115, 0.065, 0.045, 0.040), (0.115, 0.065, 0.045, 0.040),
                 (0.115, 0.065, 0.045, 0.040), (0.185, 0.140, 0.110, 0.085),
                 (0.070, 0.045, 0.045, 0.035), (0.030, 0.025, 0.020, 0.025),
                 (0.025, 0.015, 0.020, 0.025), (0.020, 0.015, 0.015, 0.020),
                 (0.020, 0.015, 0.015, 0.015)),
}

#: Which of §6.C.5's three lapse tables are carried. Table 6.5 — fixed
#: annuities with no guaranteed living benefits — is not; see
#: :func:`base_lapse_rate` and RFC-067 for the reason, which is specific.
LAPSE_TABLES_CARRIED = tuple(_BASE_LAPSE)


def base_lapse_rate(years_from_expiry, age, *, table: str = "indexed"):
    """§6.C.5, Tables 6.4 and 6.6: the prescribed base lapse rate.

    ``years_from_expiry`` is signed the way the text reads: **positive after**
    the surrender charge expires, zero in the year it expires ("Upon
    expiry"), negative before. Five or more in either direction takes the end
    row, as the table states.

    The shock at expiry is the whole shape of the table and the reason it is
    two-dimensional. An indexed annuity written to a 60-to-69-year-old lapses
    at **3.5%** the year before its surrender charge expires and **41.5%** the
    year it does — a factor of twelve across one contract anniversary. A
    single-rate lapse assumption cannot express that, and a model that
    smoothed it would put the cash flow in the wrong year rather than merely
    get the level wrong.

    Table 6.6, for contracts **with** a guaranteed living benefit, is flat
    across every after-expiry row: the contract holder who bought a benefit
    that pays while they live is not leaving once the charge is gone, and the
    expiry spike is less than half the size.
    """
    if table not in _BASE_LAPSE:
        raise PrescribedError(
            f"§6.C.5 has no carried lapse table {table!r}; this module "
            f"carries {list(LAPSE_TABLES_CARRIED)}. Table 6.5, fixed "
            f"annuities without guaranteed living benefits, is keyed by the "
            f"interest guarantee period rather than by attained age and is "
            f"not transcribed — see RFC-067."
        )
    rates = np.array(_BASE_LAPSE[table], dtype=np.float64)
    offsets = np.asarray(years_from_expiry)
    if np.any(np.asarray(age) < 0):
        raise PrescribedError("attained age is not negative")
    # Row 5 is "upon expiry"; positive offsets walk up, negative walk down.
    row = np.clip(5 - np.clip(offsets, -5, 5), 0, len(SURRENDER_CHARGE_ROWS) - 1)
    band = np.clip(np.searchsorted(np.asarray(LAPSE_AGE_BANDS),
                                   np.asarray(age), side="right") - 1,
                   0, len(LAPSE_AGE_BANDS) - 1)
    out = rates[row, band]
    return out if (np.ndim(years_from_expiry) or np.ndim(age)) else float(out)
