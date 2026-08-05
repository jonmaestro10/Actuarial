# RFC-020: Embedded value and asset-liability management

Status: **implemented** — `engine/report/embedded_value.py`

## Summary

PLAN.md §5.3's last line: "**Embedded value / ALM** overlays (asset models,
liability-driven runs)". It is also where four earlier RFCs converge, each
having stopped at the same edge — the asset side:

- RFC-014's Solvency II has no market risk modules, because they need assets.
- RFC-016's principle-based reserve takes starting assets and earned rates
  as *inputs*.
- RFC-019's with-profits estate is assets less asset shares, and only had
  the second half.
- RFC-011's fixed-indexed annuity notes that the cap is set by the cost of
  the call spread backing it, and prices no spread.

## The finding: a guarantee whose time value exceeds the whole business

Embedded value is where the option this engine keeps measuring finally gets
a line in a report:

    TVOG = deterministic PVFP − mean stochastic PVFP

Measured on a real projected block rather than an illustration — a
universal-life account with a **1% minimum crediting rate**, at 6%
volatility:

| | |
|---|---|
| deterministic PVFP | **+7.78m** |
| mean stochastic PVFP | **−0.82m** |
| time value of the guarantee | **8.60m** — 110% of the deterministic value |

A traditional embedded value reports a **positive** value of in-force; a
market-consistent one on the identical block reports a **negative** one. Not
a difference of degree.

And it is not a mis-calibration. RFC-010 established that an annual
crediting floor is a *strip* of one-year options rather than one long one,
worth +323 bp a year at 10% volatility; the account it applies to here is
about thirty times the annual charge income, so a hundred basis points on
the account is comparable to the entire margin. The two results are the same
result, seen from the two ends of the platform.

The time value rises with volatility and with the floor, and the
deterministic value **does not move at all** when volatility changes — which
is the whole reason the line has to exist.

## The finding: matching duration does not immunise

Stated the other way round so often that it is worth demonstrating. Two
asset portfolios, both matching the liability's value and duration to
machine precision:

| portfolio | duration gap | convexity gap | surplus at −300bp | at +300bp |
|---|---|---|---|---|
| wide barbell (2y / 25y) | 1.8e-15 | **+75.4** | **+702** | **+313** |
| narrow (9y / 10y) | 0.0 | **−29.7** | **−249** | **−134** |

Same duration match, **opposite outcomes**. A portfolio holding more
convexity than its liabilities gains on a move in either direction; one
holding less loses in either direction. Duration matching pins the first
derivative and leaves the second free, and "immunised" is routinely used as
though it did more.

The error is second order, and asserting *that* took two attempts. A fixed
ratio across a 50-to-200bp jump is wrong by 18%, because at any shift big
enough to matter the third derivative is in it too. Asserted as a
convergence instead: halving the shift divides the error by 3.12 at 400bp
and by **3.97** at 12.5bp, approaching the 4 the quadratic term alone would
give.

`duration_gap` is value-weighted, because the unweighted difference is the
wrong number whenever the two sides are worth different amounts — which
they always are. A longer but smaller asset portfolio does not hedge a
shorter but larger liability.

## The analysis of change must reconcile, so the residual is solved

An embedded value report is judged on its movement analysis: opening, plus
unwind, plus new business, plus experience, plus assumption changes, less
distributions, equals closing. If those do not add up, nobody believes any
of them.

`analysis_of_change` computes the last component as **whatever is left** and
reports it as `unexplained`. A residual that is not tiny means a movement
has been left out — which is information the report should carry, not
absorb into the nearest line. An unrecognised movement name raises rather
than being silently dropped.

## Frictional cost is the spread, not the return

Locked-up capital still earns; what it costs shareholders is the **spread**
between what they require and what the assets yield. Charging the whole
return double counts, because the investment income on required capital is
already inside the projected profits. A frequent enough error to be worth
a named function and a test.

## What is taken rather than derived

`frictional_cost` and `non_hedgeable_cost` are inputs, for the reason
RFC-012 gives about risk adjustments: the standards say what they are and
not how to compute them, and a library shipping one method would be wrong
for every entity that chose another.

## Not in scope

- **A real asset model.** Cashflows are supplied; there is no projection of
  reinvestment, defaults, or trading. That is the piece the four RFCs above
  actually want, and it is larger than this one.
- **Non-parallel shifts.** Everything here moves the whole curve together.
  Key-rate durations are the same machinery on a different perturbation.
- **New business value** as its own calculation. It appears in the bridge as
  a movement; producing it is a projection of a cohort that does not exist
  yet.
- **Look-through to the with-profits estate**, which RFC-019 leaves open in
  the other direction.
- **Required capital as a projection.** It is supplied per date here;
  deriving it is RFC-014's SCR at every future date, which is the same
  circularity RFC-014's risk margin already documents.
