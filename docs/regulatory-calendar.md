# Regulatory calendar

*Every dated regulation set this engine carries, when it takes effect, and
when somebody has to look at it again.*

This is the open answer to the thing incumbents sell as "quarterly vendor
library updates on a contractual cadence". The difference is not the cadence —
it is that each update lands as a **new dated set** beside the old one rather
than replacing it, plus an RFC-050 diff report saying what changed and what it
does to your numbers. A client's prior-period figures stay reproducible,
because the set that produced them is still here.

`tests/test_release.py` asserts that **every dated set in `engine/` appears in
this table**. A set added without a calendar row fails the build — otherwise
this file becomes a list of the sets somebody remembered.

## Carried sets

| Set | Regime | In force | Review by | Notes |
|---|---|---|---|---|
| `DELEGATED_2015` | Solvency II standard formula (market risk) | reporting dates before 30 Jan 2027 | 2027-01-30 | Commission Delegated Regulation (EU) 2015/35. Stays after the amendment takes effect: prior-period reporting is still done on it. |
| `DELEGATED_2026` | Solvency II standard formula (market risk) | from 30 Jan 2027 | 2027-06-30 | (EU) 2026/269. The interest-rate tables and the equity parameters move; RFC-050's diff report quantifies it against `DELEGATED_2015`. |
| `VM22_2026` | NAIC Valuation Manual VM-22 | 2026 edition | 2027-01-31 | The Valuation Manual is republished annually and the review date tracks that, not a change we expect. |
| `VM22_PRESCRIBED_2026` | VM-22 prescribed tables | 2026 edition | 2027-01-31 | The prescribed-table half of VM-22, versioned with it. One open item: Table 6.5 has a reading that no arithmetic reproduces and an APF is drafted — see `docs/sources/vm22-table-6-5-reading.md`. |

## How a new set lands

1. A new module-level constant, dated, **beside** the existing one. Never an
   edit to a set already in force: a client who reproduces last year's
   valuation must get last year's numbers.
2. A row here, with a review date.
3. An RFC-050 diff report against the set it supersedes, registered as an
   artifact so the comparison is content-addressed rather than a screenshot in
   an email.
4. A `CHANGELOG.md` entry carrying the **expected-change note** §3.5 requires,
   because a numeric result moving is the one change that cannot be reviewed
   from a diff of the code.

## What a review date means

That somebody reads the primary text again — not that anything changed. The
review dates above are set from the publication rhythm of each regime, and a
review that finds nothing is recorded as a review that found nothing.

Worth stating because this repository has been bitten by the alternative:
reading VM-22's actual text found three errors that 35 passing tests all
agreed with. Golden tests check an implementation; they cannot catch a
misreading of the method, because the misreading reproduces perfectly across
every implementation of it.
