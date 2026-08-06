# RFC-071: The axis that is not the same axis twice, and the table underneath it

Status: **implemented** — `engine/report/vm22_prescribed.py`,
`tests/test_vm22_prescribed.py`,
`docs/sources/vm22-section-6-prescribed-assumptions.md`,
`docs/sources/scripts/extract_fx_structured.py`

## Summary

The last uncarried §6.C work. RFC-067 carried seven of the eleven prescribed
tables and left four, and this one discharges the sentence it left behind:

> The three structured-settlement sets cross a contract-year band with sex,
> and a table whose second dimension is read wrongly is a plausible number in
> *every* cell rather than an obviously missing one. They need a read of
> their own.

Tables **6.9, 6.10 and 6.11** are now carried — 312 rows read and 309
transcribed, the cap row being the code's as it is for Tables 6.7 and 6.8,
and all 2,266 printed cells reproduced through `fx_factor`. §6.C is at ten of
eleven,
and the one remaining is Table 6.5, whose Guidance Note contradicts its own
grid and which is waiting on an APF rather than on effort.

```python
fx_factor(62, "F", category="structured_settlement_standard",
          contract_year=11)                                    # → 2.25
fx_factor(62, "F", category="structured_settlement_substandard",
          contract_year=11, rate_up_years=25)                  # → 1.15
projection_offset(2026, category="structured_settlement_standard")   # → 15
```

## The second dimension is banded differently in the two tables

This is the whole hazard and it is worth being precise about why.

| table | lives | contract-year bands | value columns |
|---|---|---|---|
| 6.9 | standard | 1–5 / 6–10 / **≥11** | 6 |
| 6.10 | substandard, rate-ups 1–20 yrs | 1–10 / **11–20** / 21–30 / ≥31 | 8 |
| 6.11 | substandard, rate-ups ≥21 yrs | 1–10 / **11–20** / 21–30 / ≥31 | 8 |

Three bands against four. The tempting reading is that they nearly agree —
they share two boundaries, 1 and 11 — and the shared 11 is the trap rather
than the reassurance. Contract year 11 opens Table 6.9's **third** band and
the substandard tables' **second**. A band index computed against the wrong
list is therefore in range, lands a column or two off, and reads a real cell
of a real table. For a female aged 62 in contract year 11 that is 170%
instead of 225% — a 24% understatement of prescribed mortality, arriving as
an entirely ordinary number.

The test computes the wrong reading on purpose rather than describing it, in
the idiom `vm22.floor_outside_reserve` and `takaful.surplus_if_qard_ignored`
set: the band index is recomputed against the other list, the cell it reaches
is read out of the raw table, and the ratio is asserted. A description of a
gap is a claim; a computed gap is a number that changes when the data does.

So `contract_year` is a **required** argument for these two categories and a
**refused** one for the other two. Both directions matter equally. Omitting
it would have to default to a band and there is none to default to; accepting
it for Table 6.7 would let a caller believe a banding had been applied to a
table that has no such axis. Either returns a number.

`rate_up_years` selects between 6.10 and 6.11, and the caller supplies the
rate-up rather than the table number. The rate-up is a fact about the
contract; which table it lands in is a fact about the text, and naming the
table at the call site would move that boundary somewhere it would be
restated and eventually drift. A rate-up of zero is refused rather than
rounded into Table 6.10's first row: a life with no age rate-up is a standard
life and belongs to Table 6.9. So is a *vector* of rate-ups, because one
lookup cannot straddle two different tables.

## The age floor is 2, and `FX_MIN_AGE` could not be reused

Tables 6.7 and 6.8 floor at attained age 50. These floor at **2**, because
structured settlements are written on claimants and a claimant can be an
injured child. Clamping a five-year-old to the age-50 row returns 198% where
Table 6.9 says 318% — plausible, and in the direction that understates the
reserve.

`FX_STRUCTURED_MIN_AGE` is a separate constant, and the docstring on
`FX_MIN_AGE` now says out loud that it is Tables 6.7 and 6.8's alone. The cap
is genuinely shared: all five tables state ">=105" at 100%.

## §6.C.8.iii projects a different base table, from a different year

The finding that came from reading the prose rather than lifting the grid,
and the one that would have been easiest to miss because nothing downstream
can see it.

§6.C.8.i and .ii:

> the mortality rate for a contract holder age x in year **(2012 + n)** …
> where q<sub>x</sub> denotes mortality from the **2012 IAM Basic Mortality
> Table**, as defined in VM-M Section 2.C

§6.C.8.iii:

> the mortality rate for an annuitant age x in year **(2011 + n)** … where
> q<sub>x</sub> denotes mortality from the **1983 IAM Table 'a'**, as defined
> in VM-M **Section 1.M**

Same formula. Different base table, and a base year one earlier. An
implementation that reached for the 2012 IAM Basic table and a 2012 base year
out of habit would be wrong on both counts and `q (1 − G2)^n × F` would
return an ordinary number.

Neither table is carried here — they belong to VM-M, and this module
inventing them would be the error RFC-067 already refused. But *which* of
them a category calls for is §6.C.8's own statement, so it is carried as data
(`FX_MORTALITY_BASIS`, `mortality_basis()`) and the offset is derived from it
(`projection_offset()`) rather than left to a subtraction at the call site.
At a 2026 valuation that is 14 for an accumulation contract and **15** for a
structured settlement; the test asserts the gap is exactly one improvement
year, `1 / (1 − G2)`.

`mortality_basis` also refuses the categories §6.C.8 gives no *F*<sub>x</sub>
at all — group annuities, international business and the Longevity
Reinsurance Reserving Category take the 1994 GAM Table with Projection Scale
AA, which is a different shape rather than a missing table.

## The substandard factors are lower, and that is not a slip

Table 6.10 reads 55% where Table 6.9 reads 300%, and stays strictly below it
to attained age 86. A substandard life is impaired, so the expectation runs
the other way, and this is exactly the shape of finding that gets a correct
transcription "corrected" until it agrees with the intuition.

§6.C.8.iii says why:

> Substandard lives shall use the mortality formula and terms described above
> for Standard lives, with such mortality reflecting the inclusion of the
> "Constant Extra Death" (CED) methodology described in Actuarial Guideline
> IX-A. **The CED shall be applied prior to the application of multiplicative
> F<sub>x</sub> factor.**

The impairment is already in the rate before the factor touches it. The two
sets multiply different quantities and are not comparable — which is the
RFC-055 rule again (`mudarabah_share` against `operator_surplus_share`): two
numbers close enough to be mistaken for each other, kept apart and labelled.
Asserted, with the crossing at the top of the range asserted too: the two are
equal from 87 to 97 and Table 6.10 is *above* Table 6.9 from 98 to 104,
because 6.10 comes down to 100% from above and 6.9 has been flat there since
102.

**And Table 6.11 is not monotone across its contract-year bands.** Every
other column set rises with duration. Table 6.11's *male* columns fall from
the 21–30 band to the ≥31 band at attained ages 2, 3, 4, 5 and 6 — 75% → 70%
at age 2, converging to 79% → 78% at 6 — and nowhere else in any of the three
tables. From age 7 upward its male and female columns are identical
throughout, which is where the reversal stops. This is Table 6.7's
non-monotonicity a second time: a test written to the expected shape fails
against the real table, and the tempting fix is to sort the data until it
agrees.

## Two things the extraction did before it was trusted

**Calibrated against what is already carried.** The coordinate-based reader
(`page.find_tables()`) was run first over Tables 6.7 and 6.8, which RFC-067
carries, and reproduces `_FX_ACCUMULATION` and `_FX_PAYOUT` cell for cell,
55 rows each. That calibration is what earns the method the right to be
believed on a table nothing can check it against — and it is what previously
caught a running-text regex disagreeing with the carried Table 6.7 at five
ages while the coordinate reader matched exactly.

**Every page asserted to be its own table.** Each of these tables spans four
PDF pages, and pages 269 and 272 each carry the *tail* of one and the *head*
of the next. So each page was required to repeat its own banner
("Structured Settlements – Substandard Lives, Rate-Ups ≥21 Years") and its
own band headers in order, and each table's ages were required to run 2 to
105 contiguously with exactly one "≤2" row and one "≥105". A second,
independent read by word x-position clustering — bounded at the next table's
heading on the two shared pages — agrees on all 312 rows. The first, naive
version of that cross-check *disagreed* on 25 rows, all of them on the two
shared pages, which is the failure the boundary rule exists to catch.

The reader is kept, as `docs/sources/scripts/extract_fx_structured.py` —
standalone with no repo imports, in the same idiom as the Table 6.5 scripts
beside it, and the thing to point at the 2027 edition. Both of its checks are
enforcement rather than reporting: it pins the calibration by **digest**
against the carried transcription, and exits non-zero without printing a
table if either that or the cross-check fails. A script that prints its
answer alongside a warning is a script whose warning gets skimmed.

## The refusal that had nothing left to refuse

`fx_factor` refuses a category §6.C.8 covers but this module has not
transcribed, and the test asserting it looped over
`set(FX_CATEGORIES) - set(FX_CATEGORIES_CARRIED)`. That difference is now
**empty**, and a loop over an empty set asserts nothing while continuing to
pass — the same trap as a parametrised test over an empty list, which this
repo already has a rule about.

The refusal is kept and the mechanism asserted directly instead: the carried
set is narrowed to one category for the duration of the test and the refusal
has to still fire, with the "category the section does not have at all" case
asserted alongside so the narrowing cannot collapse the two errors into one.
§6.C.8 grew three categories between the 2023 exposure draft and this
edition; the guard is for the fourth.

The dated set's provenance string needed no editing at all, because RFC-067
made it derived: moving three entries from `TABLES_NOT_CARRIED` to
`TABLES_CARRIED` re-derived "10 of 11 prescribed tables are carried" on its
own. One thing did need adding — with the absent count down to one, "the
other 1 (Table 6.5) **are** recorded" reads as a broken sentence, so the verb
is derived too. A generated sentence that reads wrong is one a reader stops
trusting, and the string travels into `__fingerprint__`.

## Acceptance

`tests/test_vm22_prescribed.py` — 42 tests, ten of them new. The spot values
are asserted from the primary text rather than from the module's constants,
one per table and per band, including the two oldest rows where 6.10 and 6.11
approach the cap from opposite sides (101.7% and 96.7% at 104).

The structural claims are each asserted where a transcription slip would show
up rather than only where it is convenient: the band boundaries at 5/6,
10/11, 20/21 and 30/31; the rate-up split at 20 against 21; the age floor at
2 against Tables 6.7 and 6.8's 50; the cap at 105; Table 6.11's five male
band reversals *and* the absence of any in its female columns, so the
reversal cannot be a banding bug in the lookup.

Broadcasting asserts **shape, dtype and value separately** — the rule
RFC-069 and RFC-070 earned, where three bugs produced equal numbers with an
unequal contract. The case that matters is a scalar age against a vector of
contract years, which is the natural projection and which a lookup
broadcasting in the wrong order would get right for the first five entries.

The refusals: a contract year omitted where the table has one, supplied where
it does not, or below 1; a rate-up omitted for a substandard life, supplied
for a standard one or for an individual annuity, at zero, or given as a
vector straddling two tables; a guaranteed living benefit asked of a
structured settlement; a valuation year before the category's own base year;
and a category with no *F*<sub>x</sub> asked of `mortality_basis`.
