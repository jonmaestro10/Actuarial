# VM-20 Appendix 1.F — the 16 scenarios, and why they are not data

**Source.** NAIC *Valuation Manual*, 1 January 2026 edition, chapter VM-20,
Appendix 1 ("Additional Description of Economic Scenarios"), section F.
Produced by the **National Association of Insurance Commissioners**.

<https://content.naic.org/sites/default/files/pbr_data_valuation_manual_current_edition.pdf>

Retrieved and machine-read (PyMuPDF) at pages 20-88 to 20-90 of the chapter.
Verified: the section exists, was read in full, and says what follows.

## What it contains

The set of 16 scenarios VM-20's stochastic exclusion ratio test runs over,
and which **VM-22 §7.C borrows** — `engine/report/vm22.stochastic_exclusion_test`
takes the baseline `a` from scenario 9 and the adverse `b` as the maximum
over all 16.

## Why nothing here is asserted in a test

Three passes over VM-22 recorded these as *prescribed dated data* not yet
carried, and proposed carrying them with execution-plan item F2's
regulation-as-dated-sets work. Reading the section shows that framing is
wrong, and the correction is the reason this file exists.

Appendix 1.F does not give sixteen scenarios. It gives sixteen
**descriptions of shocks to a prescribed generator**:

> "Starting with the yield curve on the valuation date, the scenarios are
> created using the **prescribed economic scenario generator** and the
> interest rate shocks and equity price returns detailed below. All shocks
> to CIR 1 are zero for each of the 16 scenarios."

Each scenario is then specified in the generator's own vocabulary:

| # | name | how it is specified |
|---|---|---|
| 1 | Pop up, high equity | "shocks to the CIR3 … selected to maintain the cumulative shock at the 90% level (1.282 standard errors)"; CIR2 shocks scaled by √2−1 |
| 3 | Pop down, high equity | as 1, at the 10% level |
| 5, 7 | Up/down and down/up | direction held constant within each five-year period |
| 9 | Baseline | "All shocks are zero" |
| 10 | Inverted yield curves | CIR2 shocks with offsetting CIR3 shocks at ⅓ the level, "to keep the 20-year spot rate unchanged" |
| 11 | Volatile equity returns | equity shocks reversing every two years |
| 12 | Deterministic scenario for valuation | uniform monthly downward shocks for 20 years reaching "approximately … the one standard deviation down level (16%) **from the stochastic distribution of interest rates** at the end of year 20" |
| 13–16 | Delayed pop up/down | zero for ten years, then shocks 1.414× those of scenarios 1 and 3 |

Every one of them is a function of **the valuation-date yield curve** and of
**the generator's own state variables (CIR1, CIR2, CIR3) and standard
errors**. Scenario 12 is defined against a percentile of the generator's
stochastic distribution; scenario 10 is defined by a constraint on a spot
rate the generator produces.

**So there is no table to transcribe.** Carrying these means implementing
the prescribed economic scenario generator — a separate artefact of a
different size from F2 — and until that exists, a file of sixteen numeric
paths in this repository would be somebody's output rather than the
regulation's.

This is what `docs/sources/vm22-section-check.md` marked out of scope at §8
("scenario generation — prescribed dated data"). The judgement was right; the
reason recorded against it was not, and this file supplies the reason.

## What this changes

- **Nothing in `tests/test_published_sources.py` asserts against this file.**
  There is no number in the section to assert. That is a property of the
  text, not a retrieval failure — unlike
  [`solvency2-market-correlation.md`](solvency2-market-correlation.md),
  which had numbers and could not reach them.
- `engine/report/vm22.stochastic_exclusion_test` is unchanged and remains
  correct: it takes `baseline` and `adverse` as **inputs**, which is exactly
  the right shape for quantities a prescribed generator produces.
  `VM22_2026`'s `text` field already says the prescribed scenarios are not
  carried.
- The open question raised after C1, C2 and VM-22's remediation — "pull F2
  forward to carry the prescribed scenarios" — is **closed**: F2 is built
  (RFC-050) and it is not where these live.
