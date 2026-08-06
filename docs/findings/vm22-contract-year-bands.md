# The axis that is not the same axis twice

**Claim.** VM-22 §6.C.8.iii's three structured-settlement *F<sub>x</sub>*
tables band their second axis two *different* ways, and the boundary they
share is the trap rather than the reassurance.

**Demonstrate it:** `python scripts/findings/vm22_contract_year_bands.py`
**Recorded in:** [`docs/rfc-071-structured-settlement-factors.md`](../rfc-071-structured-settlement-factors.md)
**Source text:** NAIC *Valuation Manual*, 1 January 2026 edition, VM-22
§6.C.8.iii, Tables 6.9 to 6.11

## The two bandings

| table | lives | contract-year bands |
|---|---|---|
| 6.9 | standard | 1–5 / 6–10 / **≥11** |
| 6.10 | substandard, rate-ups 1–20 yrs | 1–10 / **11–20** / 21–30 / ≥31 |
| 6.11 | substandard, rate-ups ≥21 yrs | 1–10 / **11–20** / 21–30 / ≥31 |

Three bands against four. They share two boundaries, 1 and **11** — and that
shared 11 is the hazard, because it opens Table 6.9's *third* band and the
substandard tables' *second*.

## The consequence, computed

A band index built against the wrong list is **in range**. It does not raise,
it does not return a sentinel; it reads a real cell of a real table.

For a female aged 62 in contract year 11:

| reading | factor |
|---|---|
| correct (Table 6.9, third band) | **225%** |
| the substandard banding applied to Table 6.9 | **170%** |

A 24% understatement of prescribed mortality, arriving as a perfectly
ordinary number.

## Why it is delicate rather than merely fiddly

A table whose second dimension is read wrongly gives a plausible number in
*every* cell, rather than an obviously missing one in a few. There is no
row that looks empty, no factor that looks absurd, and nothing downstream
that would question it.

The engine's answer is to make the axis a required argument: `contract_year`
must be supplied for these categories and is **refused** for Tables 6.7 and
6.8, which have no such axis. Both directions matter — accepting a contract
year for a table that does not band by it would let a caller believe a
banding had been applied.
