# RFC-017: IFRS 17, the premium allocation approach

Status: **implemented** — `engine/report/paa.py`

## Summary

The last of the three measurement models PLAN.md §5.3 names, and the only
one that is a **simplification** rather than a measurement in its own right.
The liability for remaining coverage is an unearned premium balance, and
there is no contractual service margin at all.

That absence is the whole thing. Under RFC-012's general model the CSM is
where a group's unearned profit lives and where every change in estimate
lands; under the PAA there is no such balance, so profit emerges purely as
premium is earned and nothing defers anything. RFC-012's coverage-unit
finding — that the choice of driver moves a quarter of the margin — has no
counterpart here, because there is no driver to choose.

With this, IFRS 17's GMM, VFA and PAA are all in place.

## The finding: the divergence is the time value of money, and nothing else

At a **zero discount rate the PAA is not a simplification — it is the
general model, exactly**, at every coverage period tested (agreement to
1e-12 at 1, 5, 20 and 30 periods).

Turn the rate up and the two part company, monotonically in both term and
rate:

| coverage periods | PAA vs GMM, at 4% | with the liability accreted (§56) |
|---|---|---|
| 1 | **0.000%** | 0.000% |
| 5 | 3.36% | 0.99% |
| 8 | 5.80% | 1.88% |
| 12 | 9.21% | 3.35% |
| 20 | 17.35% | 7.64% |
| 30 | 30.32% | 15.96% |

(Gap measured as the largest difference in the liability at any date, over
the group's total premium.)

This is exactly why §53(a) exempts contracts of a year or less — no material
financing component — and why §56 requires accretion where there *is* one.
And the accretion is not cosmetic: at a 5% materiality threshold this group
qualifies out to **seven** periods undiscounted and out to **fifteen** with
the liability accreted. §56 roughly doubles the eligible term.

## The eligibility test that requires the work it exempts you from

§53's second limb permits the PAA where the entity *reasonably expects* it
would not differ materially from the general model — which can only be
established by measuring the general model, the very thing the
simplification exists to avoid.

`eligibility` takes that seriously rather than papering over it. The
one-year limb is answered from the coverage period alone and no general
model is run; anything longer is measured **both ways** and compared,
because an expectation formed any other way is not one this module can
record. `Eligibility.ground` reports which limb was relied on, so a reader
can tell an answered question from an assumed one.

"Material" is a number the standard never gives. The default here is 5%, it
is overridable, and `explain()` states it wherever it is used.

## Four errors, all caught by measurement

**The comparison scale.** The first version divided the gap by the general
model's own liability. A level-premium group shows why that is unusable: the
GMM liability is nil at issue by construction, peaks at **0.8% of the
premium**, and is nil again at run-off. The two models agree to within 0.8%
of the group's size and the old scale called that a **100%** difference — and
120% to 200% on the single-premium shape, and infinity over one period. The
denominator is now the premium, which is stable and non-zero for any group
that can be tested at all.

**The onerous test's sign.** §57 keeps the test alive under the PAA, and a
group is onerous when the cost of fulfilling the remaining coverage
*exceeds* the unearned premium held against it. The first version added the
unearned premium where it must subtract it — and since a profitable group's
fulfilment cashflows are negative, adding a positive balance flipped the
comparison and manufactured a loss component on a group with a 30% margin.

**The level revenue, twice.** Two things drain the same balance — the
revenue earned and the acquisition cashflows amortised — and dividing the
premium by the number of periods knows about only one of them. Undiscounted,
the liability closed at *minus the acquisition cost*; discounted, it closed
133 short of zero on a five-period group. The level amount is now solved
against both drains, and against two accumulation vectors rather than one:
a cashflow joins the balance *before* the period's growth and carries it,
while the revenue is taken *after* and does not.

**The day-one loss.** `diff` of the loss component telescopes the opening
balance out of the income statement entirely — the same fault RFC-015's
day-one loss had, in the same shape, caught by the same reconciliation to
net cash. Total profit was 223 too high on a group whose whole loss was 223.

Every one of these reconciles now, across twelve combinations of shape and
discounting.

## §B126's gross-up

The part of revenue that recovers acquisition cashflows is reported gross —
in revenue and in expenses at the same amount — so it nets out of the
service result and cannot flatter it. That is the mechanism RFC-012 scopes
*out*, and it is present here because the PAA cannot avoid it: acquisition
costs drain the very balance the model is about.

## Not in scope

- **The liability for incurred claims.** §59(b) permits it undiscounted
  where claims settle within a year, and requires discounting and a risk
  adjustment otherwise. It is a separate balance from the LRC and a separate
  RFC.
- **Experience variance**, as in RFC-012 and RFC-015: expected against
  expected throughout.
- **Reinsurance contracts held under the PAA**, which have their own
  eligibility test and their own loss-recovery component.
- **Revenue on a basis other than the passage of time.** §B126 permits
  allocation on the expected timing of incurred claims where that differs
  significantly. It is a driver choice of exactly the shape RFC-012's
  coverage units are, and the same measurement would apply to it.
