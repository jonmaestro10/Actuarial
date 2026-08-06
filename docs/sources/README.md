# External sources, and what they check

*Published material with **numbers in it**, kept so the engine can be
checked against something nobody here wrote. Each file records the
provenance of one source, the figures extracted from it, and which part of
this repository those figures hold up.*

## Why extracts rather than PDFs

These files carry **the numerical content and the citation**, not copies of
the source documents. Three reasons, in order of how much they matter:

1. **A test needs numbers, not a PDF.** `tests/test_published_sources.py`
   reads the figures from these notes' companion data and asserts against
   them; a binary in the tree would still have to be transcribed.
2. **Redistribution.** Some of this is copyrighted (ASTIN Bulletin), and a
   repository is a distribution channel. Every source below is freely
   readable at the URL given.
3. **Diffability.** A number that changes because a source was revised
   should show up in a diff. A 4 MB PDF does not diff.

Where a document was fetched and machine-extracted, the note says so and
gives the page or section the figures came from.

## The log

| file | source | author / organisation | what it contains | what it checks | status |
|---|---|---|---|---|---|
| [`mack-1993-chain-ladder.md`](mack-1993-chain-ladder.md) | *Distribution-Free Calculation of the Standard Error of Chain Ladder Reserve Estimates*, ASTIN Bulletin 23(2), 213–225, 1993 | Thomas Mack; ASTIN / International Actuarial Association, hosted open-access by the Casualty Actuarial Society | The Taylor–Ashe (1983) 10×10 paid triangle; published age-to-age factors; reserves by accident year under six methods; standard errors as a % of each reserve | `engine/report/incurred_claims.py` — development factors and chain-ladder reserves | ✅ **checked, passes** |
| [`naic-vm22-2026.md`](naic-vm22-2026.md) | *Valuation Manual*, 1 January 2026 edition, chapter VM-22 | NAIC (National Association of Insurance Commissioners) | CTE level, the aggregate-reserve composition, where the cash-surrender-value floor is applied, the stochastic exclusion ratio test and its threshold, the status of the Standard Projection Amount | `engine/report/vm22.py` | ✅ **checked — found three errors, now fixed** (RFC-039) |
| [`naic-vm22-field-test-2024.md`](naic-vm22-field-test-2024.md) | *VM-22 Field Test Specifications*, 6 March 2024 | NAIC Annuity Reserves and Capital Subcommittee | Prescribed field-test margins, the investment guardrail, the hedging-error weighting behind the two-CTE70 blend, and the required output metrics | `engine/report/vm22.py` — corroborates CTE 70 and the hedge blend; supplies margin figures for future work | 🟡 corroborating, not yet a test |
| [`solvency2-market-correlation.md`](solvency2-market-correlation.md) | Commission Delegated Regulation (EU) 2015/35, Article 164(3) | European Commission / EIOPA | The market-risk correlation matrix and the direction-dependent parameter *A* | `engine/report/market_risk.py` | 🟡 **values recorded, primary text not retrievable in this session** — see the note in the file |

## Status vocabulary

- ✅ **checked** — the figures are asserted in `tests/test_published_sources.py`
  and the suite is green.
- 🟡 **corroborating** — the source supports a design choice but no
  assertion runs against it, either because the repo does not yet implement
  the thing measured or because the figures are inputs rather than outputs.
- 🟡 **not retrievable** — the numbers are recorded from a secondary route
  and the primary text could not be machine-read here. Treated as
  *unverified* until somebody opens the source.

## Wanted

Gaps where a published check would be worth having and none is recorded yet:

- **Mack standard errors.** The source above carries them; the repo has no
  Mack variability yet (execution plan C5). The targets are already written
  down, so C5 arrives with its acceptance test pre-specified.
- **IFRS 17 CSM roll-forward** against a published illustrative example, for
  `engine/report/ifrs17.py`.
- **Life-contingency factors** (annuities and assurances) against a standard
  illustrative life table, for `engine/library/reserves.py` — the closed
  forms are self-checked today but not against anybody else's arithmetic.
- **A CTE / TVaR worked example** with published values, for
  `engine/report/pbr.py`.
