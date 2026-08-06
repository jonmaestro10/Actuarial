# The cliff at seven per cent

**Claim.** Solvency II Article 200's *lower* band boundary moves the capital
requirement by **14 percentage points of total loss-given-default** — a
66.7% jump — for an arbitrarily small change in the book. The *upper*
boundary, by contrast, is continuous by construction.

**Demonstrate it:** `python scripts/findings/counterparty_band_cliff.py`
**Recorded in:** [`docs/rfc-028-counterparty.md`](../rfc-028-counterparty.md)
**Source text:** Commission Delegated Regulation (EU) 2015/35, Article 200,
consolidated `02015R0035 — EN — 30.07.2020 — 007.001`

## What the article says

Article 200 turns the standard deviation of the loss distribution into
capital in three bands:

```
σ ≤  7% · ΣLGD   →   SCR_def,1 = 3σ
σ ≤ 20% · ΣLGD   →   SCR_def,1 = 5σ
σ >  20% · ΣLGD  →   SCR_def,1 = ΣLGD
```

The upper boundary meets exactly: `5 × 20% = 100%`, which is what the third
band gives. Somebody chose 5 and 20% so that they would join.

Nobody did the same at the bottom. `3 × 7% = 21%` against `5 × 7% = 35%`.

## Walked across on a real book

Equal credit quality step 4 counterparties sharing 1,000 of
loss-given-default. Adding counterparties *diversifies*, so σ falls and the
book drifts down across the 7% line:

| counterparties | σ / ΣLGD | capital |
|---|---|---|
| 37 | 7.0009% | **350.05** |
| 38 | 6.9973% | **209.92** |

One counterparty out of thirty-seven, a change of 0.004 percentage points in
σ, and the requirement falls by 40%.

## Why it matters

A firm sitting near the boundary can move its capital requirement by a third
through a change with no economic content — adding a small counterparty,
or reclassifying one. The discontinuity is in the regulation, not in the
engine, and the engine reproduces it faithfully; what the engine adds is the
ability to *see* it before the reporting date.

RFC-026 named a 10 basis point discontinuity in Article 176(3)'s spread
table as a defect worth reporting. This one is 140 times larger, and unlike
that one it is load-bearing rather than a typographical residue.
