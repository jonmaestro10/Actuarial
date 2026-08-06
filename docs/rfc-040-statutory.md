# RFC-040: The modified net premium, and the cap that bites

Status: **implemented** — `engine/report/statutory.py`,
`tests/test_statutory.py`

## Summary

Execution plan §5, item C2:

> `engine/report/statutory.py`: CRVM / net-premium formulaic reserves
> (build on `engine/library/reserves.py`), plus an asset-adequacy-testing
> runner joining the liability projection to the existing asset side
> (`engine/data/assets.py`, `engine/report/embedded_value.py` patterns).

Two halves, and the interesting thing about each is how little new
arithmetic it needed.

## The modified-reserve family is one function of one number

RFC-018 already had both ends of the family: nothing allowed (the net
premium reserve) and everything allowed (full preliminary term, whose
first-year reserve is exactly zero by construction). What sits between them
— and what US statute actually prescribes — is CRVM, which is full
preliminary term with the expense allowance **capped**.

Writing a modified method as a pair of net premiums `(alpha, beta)` and
solving "the modified premiums have the same value at issue as the level
one" collapses the whole family to one parameter:

```
beta = P + E / ä          alpha = beta − E
V_t  = A_t − beta · ä_t   (t ≥ 1),   V_0 = 0
```

`E = 0` is the net premium reserve. `E = E_fpt` — the allowance that makes
`alpha` the one-year term cost — is full preliminary term. CRVM is
`min(E_fpt, cap)`. So the module is one reserve function and three ways of
choosing its argument, and the tests pin the two extremes against the
already-tested RFC-018 code rather than reimplementing them: `E = 0` must
reproduce `prospective_reserve` and `E = E_fpt` must reproduce
`full_preliminary_term`, both to 1e-15.

The cap is computed rather than tabulated — the allowance a whole life
paying `CRVM_REFERENCE_PAYMENTS` premiums would earn at the same issue age,
derived by the same expression as every other allowance, so the comparison
is like with like and somebody can rederive it. `whole_life_term` is an
argument because a module that guessed at the end of somebody's mortality
table would be guessing at the cap.

## The finding: first-year strain is exactly the cap's bite

`V_1` is linear in `E` and vanishes at `E_fpt`, so it has a closed form:

```
V_1 = (E_fpt − E) · ä_{x+1:n−1} / ä_{x:n}
```

**The first year's statutory strain is exactly the amount the cap took,
scaled by the renewal share of the annuity** — and it is *exactly zero* for
every plan where the cap does not bind. `first_year_strain` computes it
from the allowance alone while `crvm_reserve` computes it from the full
prospective expression, and the test that they agree to 1e-12 is the
identity rather than a restatement of it.

The measured consequence is the sharp edge. On the mortality basis in the
tests, at issue age 40:

| plan | `E_fpt` | cap | binds | first-year reserve |
|---|---|---|---|---|
| 20-year term | 0.0046 | 0.0259 | no | **0** |
| 10-year endowment | 0.0945 | 0.0259 | yes | 0.0626 |
| 20-year endowment | 0.0389 | 0.0259 | yes | 0.0125 |
| 30-year endowment | 0.0236 | 0.0259 | no | **0** |

The reserve is continuous across that boundary but **not differentiable**:
the slope of first-year strain against policy term is exactly zero on the
long side and strictly positive on the short side. So a sensitivity
measured where the cap is inert predicts zero strain for a plan that has
plenty of it, and the suite demonstrates that with the numbers rather than
warning about it in a comment. It is the same trap as RFC-026's
counterparty band cliff and RFC-039's floor-eats-diversification, in a
third regime: a quantity that is correct everywhere, and a derivative that
is only correct locally.

`ExpenseAllowance.binds` is the one attribute that answers "which side of
the cap is this plan on", and it is the first thing to look at on a block.

## Asset adequacy is the same roll, reduced differently

Cash-flow testing asks whether the assets backing a block, run forward with
its liabilities, run out. That is the question VM-20 asks and — the point —
the same *object*: an accumulated deficiency, discounted along each path,
maximised over dates. RFC-016 already computes it, and the test that
matters asserts `asset_adequacy` returns exactly
`greatest_present_value_of_accumulated_deficiency`, bit for bit, rather
than a second implementation of the roll.

What actually differs between an asset adequacy opinion and a
principle-based reserve is **the reduction across scenarios**: a handful of
prescribed paths reduced by a maximum, against thousands reduced by a
conditional tail expectation. So `reduction` is an argument, both are
available, neither is a default that hides the other, and the result
records which was used — because "the additional reserve is 4.2m" means
two different things depending on the answer.

A CTE reduction with no level is refused: the maximum is the reduction that
needs no parameter, and a tail measure that picked its own level would be
answering a question nobody asked.

## What is not here

- **No prescribed scenario set.** The New York 7 and its successors are
  prescribed, dated, and belong with the other dated regulatory sets;
  `asset_adequacy` takes the paths it is given.
- **No asset projection.** `engine/data/assets.py` projects a portfolio and
  produces earned rates; this consumes that output rather than duplicating
  it. Joining the two into a one-call runner needs the prescribed
  scenarios above to be worth having.
- **No valuation mortality or interest tables.** CRVM is prescribed on
  prescribed tables at prescribed rates; the method here takes whichever
  basis it is handed, which is what makes it testable and what stops it
  claiming a compliance it cannot check.
- **No deficiency reserves, no XXX/AXXX.** Separate mechanics on the same
  chassis; the modified-premium machinery is the part they would share.

## Acceptance

`tests/test_statutory.py` — 21 tests. Both ends of the family reproduce
RFC-018's already-tested reserves; the modified premiums are what the
method says they are (`alpha` is the one-year term cost under full
preliminary term, `alpha == beta == P` when nothing is allowed); a larger
allowance lowers the first year and raises the rest.

The finding is pinned four ways: the closed form agrees with the full
prospective reserve across five terms; first-year strain is positive if and
only if the cap binds, checked across a grid of terms and both products;
CRVM is bit-identical to full preliminary term wherever the cap is inert;
and the kink is demonstrated by measuring the slope on both sides of the
crossover.

Asset adequacy reports no deficiency for a block whose assets last, the
deficiency as extra reserve for one that does not, the same numbers RFC-016
produces, a CTE never above the maximum, and an interior worst date. The
refusals — an unknown method, a term too short for a modified basis, an
unknown reduction, a CTE with no level, mismatched projections, no
scenarios — are asserted alongside.
