# RFC-054: Two error bars that are not the same error bar

Status: **partially implemented** — reserve variability only.
`engine/report/incurred_claims.py`, `tests/test_gi_reserving.py`.
The premium-liability template (C5's second half) is **not built**; see the
last section.

## Summary

Execution plan §10, item C5, first half:

> **Reserve variability:** extend `incurred_claims.py` with Mack standard
> errors and an over-dispersed-Poisson bootstrap — golden-tested against the
> published Taylor–Ashe triangle results that every P&C text reproduces, so
> the reserve *ranges* (not just the point estimates) are machine-checked.
> Bootstrap RNG follows the engine's pinned-stream discipline, so the range
> itself is reproducible — which no reserving tool asserts.

```python
mack = mack_standard_error(ChainLadder(triangle))
mack.coefficient_of_variation      # → (0, .80, .26, .19, …) — Mack's Table 3
boot = odp_bootstrap(triangle, n_samples=4000, seed=7, process_variance=True)
boot.percentile([75, 95])
```

Every figure in Mack's Table 3 reproduces exactly — `(80, 26, 19, 27, 29, 26,
22, 23, 29)` by accident year and **13%** in total — and the bootstrap's
over-dispersion parameter comes out at **φ = 52,601**, the value the
England–Verrall literature quotes for this triangle.

The check ran the right way round. `tests/test_published_sources.py` carried
those targets *before* anything could compute them, with a test asserting
`mack_standard_error` did not exist and pointing at this item. There was no
implementation to tune the transcription toward, which is the difference
between a published check and a regression test of one's own output. That
test now asserts the thing it was holding a place for.

## Estimation error and prediction error are different numbers

This is most of what the module exists to keep straight, because the two are
close enough on this triangle to be mistaken for agreement.

| | what it is | Taylor–Ashe |
|---|---|---|
| Mack | prediction error, closed form | **13%** |
| `odp_bootstrap(...)` | estimation error | **15%** |
| `odp_bootstrap(..., process_variance=True)` | prediction error | **16%** |

Resampling residuals measures only how much the *fitted* reserve moves — the
estimation error. Mack's is a prediction error, carrying the process variance
of the future payments as well. Quoting the bootstrap's 15% against Mack's
13% would be comparing two different quantities and finding them reassuringly
close.

So `process_variance` defaults to **`False`**, and the docstring says which
number each setting returns. A process step added silently would let an
estimation error be quoted against a published prediction error and pass
inspection. With it on, a gamma draw of mean `R*` and variance `φR*` — the
over-dispersed Poisson's continuous analogue, which is what lets a
non-integer claim amount have a Poisson-like variance — takes it to 16%, and
the decomposition is asserted to hold: `15.4 ⊕ 5.3 = 16.3`. That check is
what distinguishes a real process step from extra noise of about the right
size.

**The two are then still 13% and 16%, and nothing here reconciles them.**
They are two models, not two estimates of one number. A reserving tool that
quietly reported the smaller — or their average — would be making a modelling
choice on the user's behalf at exactly the point where the user's judgement is
the thing wanted. The test asserts they disagree, and by how much.

## The total is not the periods added in quadrature

Every accident period is developed with the *same* estimated factors, so
their reserves are positively correlated and the total's error exceeds the
root sum of squares — by **20%** on this triangle. Adding in quadrature is
what anyone would do by default.

`MackResult.quadrature_total` computes the wrong figure on purpose, so the
correlation term is reportable rather than a thing the reader has to already
know. Same posture as `engine/report/vm22.floor_outside_reserve`, which
computes the natural misreading of §4.B.1 so the gap can be shown.

## What is refused

- **Simple-average factors.** The derivation is conditional on the
  volume-weighted estimator: `σ²` is estimated *around* `f_j`, and different
  factors are a different model. Computing the formulae anyway returns a
  plausible number for a model nobody fitted.
- **A tail factor.** Mack's formulae stop where the triangle does. A tail's
  uncertainty is not in the data, and extending through it would attach an
  error bar to an assumption.
- **A triangle too small for the σ² extrapolation**, which needs three
  earlier development periods.
- **A bootstrap of one sample**, and a triangle with no degrees of freedom
  for φ.

The last development period's `σ²` cannot be estimated — one observation, no
degrees of freedom — so Mack §3 extrapolates it as
`min(σ⁴_{n-3}/σ²_{n-4}, σ²_{n-4}, σ²_{n-3})`, and that is used rather than
zero. Zero is the tempting alternative and would declare the last factor known
exactly at the one development period where the data says least — dragging the
oldest accident years' errors down, which is precisely where the reserve is.

A resampled triangle that goes negative in a cell is **dropped**, not clipped.
Clipping would bias the range toward the point estimate, which is the
direction that makes a reserve range look better than it is.

## The reproducibility claim

`seed` is an argument with a default, not a hidden global, and the test
asserts the simulations match **element for element** across two runs — not
that their standard deviations agree. Two different streams can agree on a
summary statistic and disagree on every draw, and it is the draws that a
published reserve range has to be recomputable from.

## What is not built

**C5's second half.** `engine/library/general_insurance.py` — earned and
unearned premium, earning patterns, expected loss and cat-load cashflows,
pairing with the PAA overlay in `engine/report/paa.py` — is not started. This
RFC's status says *partially implemented* rather than implying otherwise, and
the execution plan's C5 entry records which half landed.

**No published ODP figure is asserted as a golden.** φ is, because it is a
property of the fit and is quoted consistently in the literature. The
bootstrap's own standard error is asserted to a tolerance and against its own
decomposition, because published ODP results vary with the simulation count,
the bias adjustment and the residual definition — pinning a number to three
figures against a paper that used a different sampler would be asserting
somebody else's random seed.

## Acceptance

`tests/test_gi_reserving.py` — 14 tests, plus the rewritten published-source
test. Mack's Table 3 by accident year and in total; the σ² extrapolation
identity; the quadrature gap; the seed reproducing the draws; φ at 52,601
and independent of the seed; the estimation/process/prediction decomposition;
the bootstrap centring on the chain-ladder reserve, which is where a
wrong-residuals mistake shows up — in the *mean*, not the spread, leaving a
plausible range around the wrong centre.
