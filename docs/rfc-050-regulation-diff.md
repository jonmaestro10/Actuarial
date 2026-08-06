# RFC-050: What an amendment did, and the part no clause accounts for

Status: **implemented** — `engine/report/regdiff.py`, `tests/test_regdiff.py`

## Summary

Execution plan §8, item F2:

> Generalize the dated-sets pattern (market risk already carries 2015/35
> *and* 2026/269): `engine/report/regdiff.py` runs one block under two dated
> texts and reports per-module SCR deltas with drivers. The open answer to
> the vendors' quarterly-library-update moat: regulation changes become a
> diffable, testable artifact.

```python
diff = market_risk_diff(assets=..., liabilities=..., curve=..., spread=...)
diff.total_change          # +6.49
diff.sum_of_clauses        # +1.35  ← and that is the point
diff.interaction           # +5.14
diff.driver                # None: no clause moved it, the package did
```

Pulled forward from §8 at the user's direction, after VM-22's remediation
raised it for the third time. The answer it gives to *that* question is in
the last section and is not the one the question expected.

## The clauses do not add up, and normalising them would be the bug

RFC-026 had already measured it: 2026/269's clauses thrown one at a time
move a block by **+1.35**, and thrown together by **+6.49**. The
decomposition misses three quarters of the movement.

The tempting fix is to scale the clauses so they sum to the total. It is the
wrong one, and specifically wrong: it would hand the interaction to whichever
clause happened to be largest, and the reader would come away believing that
clause caused a movement it barely contributed to. Market risk aggregates
through a correlation matrix and a max over two interest scenarios; neither
is linear, and no reweighting makes them so.

So `interaction` is a first-class number, `reconciles()` asserts that clauses
plus interaction equal the total *exactly*, and `driver` **returns `None`**
where the interaction exceeds every clause. A report that cannot name a
driver honestly is more useful than one that names the largest row: "this
amendment has to be read as a package" is a real finding, and a `None` can
carry it where a number cannot.

## Forward and backward, because they disagree

There are two natural one-at-a-time decompositions:

- **forward** — `f(baseline + clause) − f(baseline)`: what the clause does to
  the world as it stands;
- **backward** — `f(amended) − f(amended − clause)`: what it contributes once
  the rest of the amendment has landed.

For an additive amendment they are equal. Both are reported per clause
because the gap between them *is* that clause's interaction with the others,
and the case worth surfacing is the clause that is inert alone and material
in company — invisible if you pick one column and call it the effect.

The control matters as much as the finding: a text diffed against
itself-plus-one-clause has nothing for that clause to interact with, and the
test asserts forward equals backward to zero there. Without it, the
disagreement on the real amendment could be an artefact of taking two
measurements rather than a fact about the text.

## What the module refuses

- **A regime compared with itself.** True, useless, and the shape of a caller
  who passed one variable twice.
- **Two differently-named texts with identical settings.** Reporting a diff of
  zero would say the amendment did nothing. What actually happened is that it
  does not live in the settings this module parameterises, which is a
  different statement and the one worth making.
- **Texts that do not describe the same settings.** A clause one text has and
  the other lacks is a disagreement about what the regime *is*, and there is
  no way to throw it one at a time.
- **A pinned calibration.** It is the thing being varied.

`run` is a callable taking a calibration and returning anything with `scr`
and `modules`, so nothing here knows what a calibration is — asserted with a
deliberately multiplicative toy regime rather than assumed from the
signature. What it *does* need is that the two texts expose their divergences
as named settings. A regime that bakes its parameters in can be diffed for
its total and not for its drivers, and the module says so rather than
inventing an attribution.

## One thing the diff itself found

`interest_correlation` — Article 164(3)'s parameter A against equity and
property — is **0.5 under both texts**. 2026/269 splits only the *spread*
cell out, as B at 0.25. "The amendment changed Article 164(3)" is true and
reads as though the whole row moved; the diff says which cell did. That is
the smallest possible illustration of why the report exists.

## The question this was pulled forward to answer, and the answer

VM-22's remediation left one open question three times over: the 16
prescribed economic scenarios of VM-20 Appendix 1.F and the prescribed
assumption sets are not carried, and they look like "dated regulatory data",
which is this item's business.

**Appendix 1.F was then read, and they are not dated data.** It does not
give sixteen scenarios; it gives sixteen *descriptions of shocks to a
prescribed generator*:

> "Starting with the yield curve on the valuation date, the scenarios are
> created using the **prescribed economic scenario generator** and the
> interest rate shocks and equity price returns detailed below."

Scenario 1 is "shocks to the CIR3 selected to maintain the cumulative shock
at the 90% level (1.282 standard errors)"; scenario 9 is "all shocks are
zero"; scenario 12 is a path calibrated to reach "approximately one standard
deviation down" of *that generator's* distribution at year 20. Every one is a
function of the valuation-date yield curve and of the generator's own state
variables and standard errors.

So there is no table to carry. Carrying these means implementing the
prescribed generator, which is a different and much larger item than F2 —
and it is the thing VM-22's section-check already marked out of scope at §8,
correctly and for a reason it had not yet articulated. `docs/sources/`
records this so the question is not asked a fourth time.

What VM-22 *can* use from this module is the shape rather than the data: its
`VM22Basis` is already dated, and when the 2027 Valuation Manual amends it,
the diff is this.

## Acceptance

`tests/test_regdiff.py` — 15 tests. The clause list is discovered from the
calibrations rather than written down, so a sixth clause added to the module
appears in every diff untouched and a clause silently dropped fails. The
residual is asserted to be a substantial share of the movement, not a
rounding — a test that only checked `reconciles()` would pass against a
module that normalised the clauses. Reversing the pair negates the movement.
Per-module deltas are asserted against `market_risk` directly, including the
two sub-modules that did **not** move, because "did this amendment touch our
property book" is a question a report of changed rows cannot answer.
