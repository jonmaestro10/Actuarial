# NAIC VM-22 Field Test Specifications (6 March 2024)

**Source.** *VM-22 Field Test Specifications*, 6 March 2024, produced by
the **NAIC** Annuity Reserves and Capital Subcommittee (under the Life
Actuarial (A) Task Force). No individual author.

<https://content.naic.org/sites/default/files/inline-files/20240306%20VM22%20Field%20Test%20Specs.pdf>

**How the figures here were obtained.** PDF fetched and text extracted
mechanically (PyMuPDF); 11 pages.

## The figures

Prescribed for participants who did not wish to use their own margins:

| item | value |
|---|---|
| mortality margin | ±10 % on plus/minus segments |
| maintenance expenses | +5 % |
| lapses | ±10 % (direction depending on lapse-supportedness) |
| dynamic lapses | 150 %, capped at 100 % lapse |
| withdrawal shift | 5 % from no withdrawals to 10-year GLB withdrawals |
| index hedging error | 5 % |
| investment guardrail (fixed income) | 5 % Treasury, 15 % AA, 40 % AAA, 40 % BBB — unless a company-specific strategy gives a higher reserve |
| index-based hedging program error | max(company assumption, **1.5 %**), deducted from hedge payoffs relative to index credits |
| other hedging program error | 5 % of the difference between the "best efforts" and "adjusted" **CTE70** amounts, added to the CTE70 best-efforts run |

Required time-zero output metrics: scenario-level reserves, CARVM at
valuation (VM-22, AG 33, AG 35), C3P1 at the valuation date, the Standard
Projection Amount, and exclusion test results by scenario.

## What this supports here

Corroborating rather than asserted. It independently confirms **CTE 70**
and the two-CTE70 hedging blend that `engine/report/vm22.py`'s docstring
references from the manual. The margin figures are *inputs* a company
supplies, not outputs an engine produces, so there is nothing here for a
test to hold the engine to — they are recorded because they are the
concrete numbers a field-test-shaped exercise would need, and because
"CARVM at valuation" names the formulaic method
`engine/report/statutory.py` implements (RFC-040).
