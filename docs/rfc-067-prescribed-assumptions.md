# RFC-067: The figures the text puts in brackets

Status: **implemented** — `engine/report/vm22_prescribed.py`,
`tests/test_vm22_prescribed.py`

## Summary

The half of the dated-set question that survived scrutiny. C1, C2 and VM-22's
remediation each left the same item on the record — the prescribed scenarios
*and* the prescribed assumption sets — and RFC-050 answered the first in the
negative: VM-20 Appendix 1.F prescribes shocks to a generator, so there is no
table to carry.

§6.C is the opposite answer. Eleven numeric tables and a closed-form
mortality basis, all dated, all exactly the shape
`engine/report/market_risk.py`'s `DELEGATED_2015`/`DELEGATED_2026` has.

```python
maintenance_expense("payout_annuity", valuation_year=2026, projection_year=5)
# → PrescribedExpense(amount=74.44, ..., provisional=True)
fx_factor(62, "M", guaranteed_living_benefit=True)   # → 0.78
```

## The square brackets are the NAIC's, and the pattern had no way to say so

RFC-050 flagged this as the gap a dated set over §6.C would hit, and it is
the design contribution here. §6.C.2 escalates the base expense by
`[1.025]^(valuation year – 2015)` and inflates it at `[2.5%]`. Those are the
**only** bracketed values in the section, and in NAIC drafting the brackets
mark a figure still under discussion.

Carrying them as ordinary floats would give them the same standing as the
$50/$100/$75 that are *not* bracketed — a claim the text does not make.

`Provisional` is a `float` subclass. The arithmetic is unchanged, which
matters: a value that behaved differently from the number it represents would
be worse than a comment. What it adds is that the figure's standing travels
with the figure rather than sitting beside it.

`PrescribedAssumptions.provisional_fields()` **derives** the list by
inspecting its own values. A hand-kept list of which figures are provisional
is a list that drifts from the figures, and the drift is silent in exactly
the direction that matters — a figure that gets settled stays flagged, or one
that gets bracketed does not. Anything computed from one says so:
`PrescribedExpense.provisional` is `True` for every expense under the 2026
text, because the escalation is unavoidable.

## Two escalations that look like one

§6.C.2.a: the base "multiplied by `[1.025]^(valuation year – 2015)` **in the
first projection year**, and increased by an assumed annual inflation rate of
`[2.5%]` **for subsequent projection years**".

Two different exponents — one from 2015 to the valuation, applied once, and
one over the projection, compounding — and collapsing them into a single
power is the natural simplification. It gives the wrong answer for every
valuation after 2015, which is all of them.

It is also *invisible* at the current calibration, because both bracketed
figures happen to be 2.5% and `1.025^11 × 1.025^5 = 1.025^16`. So the test
pins the **structure**, on a basis where the two rates differ, rather than
the arithmetic on a basis where they coincide. That is the same shape as
RFC-041's `buy_in`/`buy_out` refusal: a distinction with no numerical
consequence today is one nothing will catch when it acquires one.

§6.C.2 also reads "(a) plus (b) … **or** (c)", so a contract the company does
not administer takes $35 and no account-value component. Reading it as an
addition would charge a rider-only assumed contract for administration nobody
is performing.

## Table 6.7 is not monotone, and the trough is the finding

The obvious guess about a mortality-adjustment table is that it starts high —
annuitant selection biting hardest young — and grades to 100% at the oldest
ages. Three of the four columns start above 1 and all four end at 1, so the
guess survives the endpoints and fails in the middle.

Every column troughs in the early-to-mid sixties, and the male columns go
**below one**. A male at 62 takes **95%** of the 2012 IAM Basic rate without
a guaranteed living benefit and **78%** with one.

Below 1 means the prescribed basis expects these lives to die *more slowly*
than the base table — the conservative direction for a benefit that pays
while they are alive, so it is a deliberate feature of the calibration and
not a transcription error. The first version of the test asserted the guess,
failed against the real table, and the tempting fix would have been to sort
the data until it agreed. What is asserted now is the trough, by value and by
age.

The monotonicity that *does* hold is across the benefit columns rather than
across age: a contract holder who bought a benefit paying while they live is
expected to live longer, so the with-benefit factor is at or below the
without-benefit one at every age and for both sexes. That catches a
transcription slip which swapped two columns, where the age-shape would not.

## Seven of eleven, and a refusal rather than a fallback

**Carried**, all transcribed from the primary text and checked against it:
Table 6.1 (base maintenance expense), Table 6.7 (*F<sub>x</sub>*,
Accumulation), Table 6.8 (*F<sub>x</sub>*, Payout Annuity), and Tables 6.2
and 6.3 (partial withdrawals, qualified and non-qualified).

Tables 6.4 and 6.6 (base lapse rates) joined them.

**Not carried:** Table 6.5, and three *F<sub>x</sub>* sets for structured
settlements which cross a contract-year band with sex and need a read of
their own.

### Table 6.5 fails one of its own worked examples

This is the sharpest version of "a table whose second dimension is read
wrongly is a plausible number in every cell", because the text supplies the
check itself.

6.5's second dimension is the **interest guarantee period**, not attained
age, and its Guidance Note gives three worked examples of contract-year lapse
sequences. Under the straightforward reading — row by years from
surrender-charge expiry, column by where the contract sits in its IGP cycle:

| example | expected | computed |
|---|---|---|
| 1 | 1, 1, 1, 75, 10, 7.5, 3 | **exact** |
| 2 | 1, 1, 1, 75, 1, 1, 75 | **exact** |
| 3 | 2.5, 2.5, 2.5, 25, **1**, 65 | 2.5, 2.5, 2.5, 25, **2**, 65 |

Two of three reproduce exactly; Example 3's contract year 5 comes out at 2.0%
against the text's 1.0%. Either the reading is wrong in a way Examples 1 and 2
cannot discriminate — they never exercise a renewal into a *longer* IGP with
no surrender charge — or the Guidance Note has an error.

Carrying the table on a reading that fails one of its own examples would be
the plausible-number-everywhere failure, so it is refused, and
`base_lapse_rate`'s message says which dimension the trouble is in. The
discrepancy is recorded in the test rather than in a comment, so it is
recheckable if the 2027 text restates the example.

### What Tables 6.4 and 6.6 turned out to be

The surrender-charge expiry spike is the whole reason the table has two
dimensions. An indexed annuity written to a 60-to-69-year-old lapses at
**3.5%** the year before its charge expires and **41.5%** the year it does — a
factor of twelve across one contract anniversary. A single-rate lapse
assumption cannot express that, and smoothing it would put the cash flow in
the wrong *year* rather than merely get the level wrong.

Table 6.6, for contracts *with* a guaranteed living benefit, is flat across
every after-expiry row and its spike is less than half the size. Someone who
bought a benefit that pays while they live is not leaving once the charge is
gone — the two tables encode different behaviour rather than one scaled.

### What Tables 6.2 and 6.3 turned out to be

Two tables rather than one with an adjustment, and the numbers say why. The
qualified rates grade 1.65% → 6.30% with attained age; the non-qualified ones
sit at **1.60% at every age** without a guaranteed living benefit. Required
minimum distributions drive withdrawals on qualified money and there is no
equivalent pressure on non-qualified. A module applying one table with a
factor would look reasonable and be wrong at every age above 65.

The bands are the text's own — "59 and under", "60 – 64" — so 59 and 60 take
different rates and **nothing interpolates between them**. Interpolating
would produce a rate the text does not contain at every age between the band
edges, which is the tempting smoothing and precisely what a prescribed table
exists to prevent.

Table 6.8 has the same early-sixties trough as 6.7 — 103% female and 95% male
at 62 — and is **not split by guaranteed living benefit**, so asking for a
split it does not have is refused rather than answered from the accumulation
table. That is the wrong-section failure one level down from the category
refusal.

`fx_factor` **refuses** a category whose table is absent rather than falling
back to the one that is present. A mortality factor from the wrong category
is a plausible number that nothing downstream would question — which is
precisely how this chapter produced eight errors before anyone read it. The
refusal distinguishes a category §6.C.8 does not have from one it has and
this module has not transcribed, because those are different problems for the
caller.

The reason the remaining four are absent is not transcription effort: each needs
reading against the primary text before it is worth having, and a
mis-transcribed prescribed factor is worse than an absent one because it
looks authoritative.

## What this does not build

**The additional standard projection amount itself.** §6 is the CTEPA method,
and §3.C makes it "only required for disclosure purposes pursuant to VM-31"
for year-end 2026 — not a reserve floor, which is why the assumptions land
before the calculation. `engine/report/vm22.py` is unchanged.

**VM-M's tables.** The mortality basis is a formula over the 2012 IAM Basic
Mortality Table (VM-M §2.C) and Projection Scale G2 (VM-M §1.J.1.c), and
neither is carried here, so `prescribed_mortality_rate` takes them as
arguments. That is the same shape `stochastic_exclusion_test` uses for the
prescribed scenarios, and for the same reason: a chapter's own data is not
this chapter's to invent. A test asserts those names stay absent, so carrying
them later is a decision rather than an accident.

## Acceptance

`tests/test_vm22_prescribed.py` — 31 tests. `Provisional` arithmetic is
asserted to be ordinary float arithmetic; the derived provisional list is
asserted to empty out for a basis whose figures are settled, with identical
values, so the standing is genuinely separate from the number. The two
escalations are pinned on a basis where the rates differ. Table 6.7's spot
values, its floor at ≤50 and its cap at ≥105 are asserted from the text
rather than from the module's own constants.

The refusals: an unknown contract type, a projection year before the
valuation, a category §6.C.8 does not cover, a category it covers whose table
is not transcribed, a sex the table is not quoted by, an improvement scale
outside `[0, 1)`, and a negative projection duration.
