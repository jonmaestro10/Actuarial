# RFC-014: Solvency II — technical provisions and the standard formula

Status: **implemented** — `engine/report/solvency2.py`

## Summary

PLAN.md §5.3's second framework: "**Solvency II** (BEL, risk margin, SCR
standard formula stresses)". A different kind of overlay from RFC-012's
IFRS 17, and the difference is architectural rather than accounting.

**IFRS 17 reads a projection. Solvency II re-runs one.**

The SCR is the fall in own funds under a prescribed shock, and a shock is a
change of *assumption* — 15% more mortality, half the lapses, a fifth of the
annuitants living longer. There is no formula for what that does to a
liability; the only way to find out is to project it again on the stressed
basis. So this module **drives** the engine rather than consuming its
output, which makes it the first thing here that needs the projection to be
cheap.

That is a design statement rather than a complaint: it is precisely why
PLAN.md §4 puts vectorization and scale-out ahead of product breadth. A
standard-formula life SCR is a dozen full projections of the whole book, and
a nested-stochastic one is far worse.

## The finding: the same shock, opposite signs, on two books

Which lapse stress bites is a property of the **product**, not of the
standard. On two books at the same insurer, both projected here:

| | lapse up (+50%) | lapse down (−50%) |
|---|---|---|
| term assurance | **releases capital** | costs capital |
| unit-linked savings | **costs capital** | releases capital |

A protection book loses money when policies *stay*, because more of them
survive to claim. A savings book loses money when they *go*, because the
charges that pay for the guarantee walk out with them. The standard does not
choose; it applies all three lapse shocks and takes the worst.

**The lapse module is a maximum, not a sum.** A book cannot simultaneously
lapse more and lapse less. On the protection book here, adding the three
would overstate the module by more than half.

Cutting across both: **mass discontinuance bites on either book**. Where the
best estimate is negative — a profitable book, an asset — losing 40% of it at
a stroke destroys future profit whichever way the rates were going to move.
On both books measured here it is the binding lapse shock.

## The finding: a plausible correlation matrix can produce no capital at all

`sqrt(v' C v)` is only a norm when `C` is positive semi-definite, and the
failure mode produces a **number** rather than an error.

```
[[ 1.0, -0.9, -0.9],
 [-0.9,  1.0, -0.9],
 [-0.9, -0.9,  1.0]]
```

Symmetric. Unit diagonal. Every entry inside [−1, 1]. Its smallest
eigenvalue is −0.8, and three modules of 100 each give `v' C v = −24,000` —
so the square root is undefined, and any implementation that floors it at
zero reports **no capital requirement at all** for a book with three
material risks.

`CorrelationMatrix` checks the eigenvalues on construction, along with
symmetry (otherwise the aggregate depends on the order the risks happen to
be listed in) and the unit diagonal (otherwise a module is silently
rescaled). A typo in a module name raises rather than dropping a whole risk
from the SCR.

Measured on the protection book with the Annex IV life underwriting matrix,
the diversification benefit is between 25% and 40% of the standalone total —
and the aggregate is above the largest module, which is what a valid matrix
guarantees and an invalid one does not.

## The stress is applied to the annual rate

`ScaledMortality` scales the **annual** `q`, and the sub-annual split then
divides the stressed year through `split_annual` — the same function the
unstressed basis uses. Scaling a periodic rate instead would stress the
split as well as the mortality.

The two coincide *exactly* at the first sub-period, where the UDD
denominator `1 - (k/m)q` is one — which is why a test there proves nothing,
and the first version of that test proved nothing. They separate through the
year and by more at heavier ages: at 85, the twelfth month of a 15% stress
differs by **66 basis points of itself** between the two orders.

Extracting `split_annual` out of `MortalityBasis.periodic_rate` was a pure
refactor: 50 output series across five templates are bitwise identical, and
the VPLA parity harness still reports bitwise on every rate.

## A mass lapse is an event, not a rate

Every other shock changes an assumption. A mass discontinuance changes how
many policies there are, so `Stress.apply_to_points` scales the starting
count and `Stress.apply` leaves the lapse rate alone. A stress carrying no
mass lapse returns the points unchanged — the *same objects*, so a caller
applies it unconditionally without copying a book it did not need to.

Applying a stress never mutates the basis it was derived from: a shallow
copy with the shocked fields replaced, so a stress cannot quietly change
something it was not asked to. Asserted directly.

## The risk margin's circularity

`CoC × Σ SCR(t) / (1 + r)^(t+1)` — Article 37. The circularity is real: the
SCR at each future date depends on the technical provisions then, which
include the risk margin then. Article 58's simplifications exist because of
it, and the one here is the common first — future SCRs run off in proportion
to a **driver**, scaled to today's SCR.

The driver is the choice, and it is the caller's. A run-off in proportion to
the best estimate and one in proportion to policy count are different
numbers for the same book — **48% apart** on the illustration here — and
neither is more correct in general. A module that picked one would be making
the entity's decision for it.

## Where the standard is judgement, the module takes the answer

The risk adjustment pattern from RFC-012, applied again. Cost of capital is
prescribed (6%) and is a constant. The run-off driver, the aggregation
matrix and the choice of which modules to measure are the entity's, and are
inputs. The arithmetic — the maximum over lapse shocks, the quadratic form,
the floor at zero — is done here and checked.

## Not in scope

- **Market risk**: interest up and down are expressible as a shift in the
  valuation rate and are supported, but equity, property, spread,
  concentration and currency need an asset model. That is the ALM overlay.
- **The loss-absorbing capacity of technical provisions and deferred tax**
  (the "adjustment"), which needs the with-profits and tax structure of a
  specific fund.
- **Counterparty default and operational risk**, neither of which is a
  projection.
- **Matching and volatility adjustments**, and the symmetric equity
  adjustment — all three are curve modifications rather than new machinery,
  and all three are politics as much as arithmetic.
- **The one-year view.** The SCR here is measured as the change in the
  liability under an instantaneous shock, which is the standard formula's
  own construction. A full one-year ahead re-projection with a fresh
  best-estimate at t=1 is the internal-model shape, and needs RFC-006's
  nested machinery.
