# VM-22 §6.C — the prescribed assumption sets, which *are* dated data

**Source.** NAIC *Valuation Manual*, 1 January 2026 edition, chapter VM-22,
Section 6 ("Requirements for the Standard Projection Amount"), subsection C.
Produced by the **National Association of Insurance Commissioners**.

<https://content.naic.org/sites/default/files/pbr_data_valuation_manual_current_edition.pdf>

Retrieved and machine-read (PyMuPDF) at pages 22-25 to 22-53 of the chapter
— Section 6 runs to 22-53, and Tables 6.10 and 6.11 begin at 22-44 and
22-47 and continue past the 22-45 this line used to claim.
Verified: the section exists, was read, and contains what follows.

## Why this file exists

The open question raised after C1, C2 and VM-22's remediation had **two**
halves: the 16 prescribed economic scenarios, and the **prescribed
assumption sets**. RFC-050 answered the first — VM-20 Appendix 1.F prescribes
shocks to a generator, not scenario data, so there is no table to carry (see
[`vm20-appendix-1f-scenarios.md`](vm20-appendix-1f-scenarios.md)) — and then
recorded the whole question as closed.

**That was wrong.** The second half is a different answer. §6.C prescribes
**eleven numeric tables** and a closed-form mortality formula, all of them
dated, all of them exactly the shape `engine/report/market_risk.py`'s
`DELEGATED_2015`/`DELEGATED_2026` pattern exists for. They are carryable, and
the reason they are not carried is priority rather than possibility.

## What §6.C prescribes

| table | what it holds |
|---|---|
| 6.1 | Base Maintenance Expense Assumptions, by contract type |
| 6.2 | Partial Withdrawals, Accumulation Reserving Category — qualified |
| 6.3 | Partial Withdrawals, Accumulation Reserving Category — non-qualified |
| 6.4 | Base Lapse Rates, indexed annuities with no guaranteed living benefits |
| 6.5 | Base Lapse Rates, fixed annuities with no guaranteed living benefits |
| 6.6 | Base Lapse Rates, indexed and fixed annuities **with** guaranteed living benefits |
| 6.7 | *F<sub>x</sub>* mortality factors, individual annuities, Accumulation category |
| 6.8 | *F<sub>x</sub>* mortality factors, individual annuities, Payout Annuity category |
| 6.9 | *F<sub>x</sub>* mortality factors, structured settlements, standard lives |
| 6.10, 6.11 | *F<sub>x</sub>* mortality factors, structured settlements, substandard lives |

plus prescribed treatment of full surrenders, annuitizations, index transfers
and future deposits, account value depletions, other voluntary contract
terminations, and crediting rates and investment spread.

Table 6.1 in full, as the smallest complete illustration:

> | Contract Type | Base Maintenance Expense Assumption |
> |---|---|
> | Individual contracts or certificates in a group contract in the Payout Annuity Reserving Category | $50 |
> | Fixed Indexed Annuities and other contracts in the Accumulation Reserving Category with guaranteed living benefits | $100 |
> | All other individual contracts or certificates in a group contract, including contracts in the Accumulation Reserving Category with no guaranteed living benefits | $75 |

with the amount "multiplied by [1.025]^(valuation year – 2015) in the first
projection year, and increased by an assumed annual inflation rate of [2.5%]
for subsequent projection years", plus "[s]even basis points of the projected
account value for each year in the projection".

And the mortality, §6.C.8, which is a formula over two other prescribed
artefacts rather than a table of rates:

> q<sub>x</sub><sup>2012+n</sup> = q<sub>x</sub><sup>2012</sup> (1 − G2<sub>x</sub>)<sup>n</sup> × F<sub>x</sub>

where q<sup>2012</sup> is the 2012 IAM Basic Mortality Table (VM-M §2.C), G2
is Projection Scale G2 (VM-M §1.J.1.c), and *F<sub>x</sub>* comes from Tables
6.7–6.11 — 150.0%/120.0% for female/male under 50 without guaranteed living
benefits, 125.0%/105.0% with them, grading by attained age.

## Two things that would shape a dated set carrying this

**The square brackets are the NAIC's, not a transcription artefact.**
`[1.025]` and `[2.5%]` are the only bracketed values in the section. In NAIC
drafting that marks a figure still under discussion, so a dated set carrying
§6.C would have to mark those two **provisional** rather than presenting them
with the same standing as the $50/$100/$75 that are not bracketed. That is a
distinction the module's existing dated-set pattern does not yet express.

**§6 is disclosure-only for 2026.** §3.C: the additional standard projection
amount "is only required for disclosure purposes pursuant to VM-31" — so
none of this is a reserve floor at year-end 2026, and that is a real reason
to sequence it behind reserve arithmetic. It is *not* a reason to call the
question closed, and conflating the two is the error this file corrects.

## Seven of the eleven are now carried

`engine/report/vm22_prescribed.py` (RFC-067) carries **Tables 6.1, 6.2, 6.3,
6.4, 6.6, 6.7 and 6.8**, all transcribed from the primary text above and checked
against it, together with §6.C.2's expense rule and §6.C.8.i's mortality
formula. `tests/test_vm22_prescribed.py` asserts the values against the text
rather than against the module's own constants.

Both bracketed figures are carried as `Provisional`, which is the mechanism
this file's earlier note said the dated-set pattern lacked.

**The remaining four are still recorded and not carried**, and `fx_factor`
refuses a category whose table is absent rather than serving the one that is
present. Those six each carry a *second* dimension — an age band crossed with
a surrender-charge duration, or a contract-year band crossed with sex — and a
table whose second dimension is read wrongly is a plausible number in every
cell rather than an obviously missing one. The reason is transcription risk, not effort: each table needs
reading against the primary text before it is worth having, and a
mis-transcribed prescribed factor is worse than an absent one because it
looks authoritative.

One thing the transcription found. **Table 6.7 is not monotone.** Every
column troughs in the early-to-mid sixties and the male columns drop below
100% — a male at 62 takes 95% of the 2012 IAM Basic rate without a
guaranteed living benefit and 78% with one. Below 100% means the prescribed
basis expects those lives to die more slowly than the base table, which is
the conservative direction for a benefit that pays while they are alive. A
test written to the obvious shape — high at young ages, grading to 100% —
fails against the real table, and it did.

The standard projection amount itself is still unbuilt: §3.C makes it
disclosure-only for year-end 2026, which is why the assumptions land before
the calculation.


## Table 6.5 contradicts itself, and the reading is exonerated

The one absence with a specific, recheckable reason rather than a general
one. §6.C.5's Table 6.5 — fixed annuities with no guaranteed living benefits
— is keyed by the **interest guarantee period** rather than by attained age,
and its Guidance Note supplies three worked examples of contract-year lapse
sequences.

Under the straightforward reading (row by years from surrender-charge expiry,
column by where the contract sits in its IGP cycle):

| example | the note's sequence | computed |
|---|---|---|
| 1: 3-yr IGP + 3-yr SC, then 1-yr IGPs, no SC | 1, 1, 1, 75, 10, 7.5, 3 | **exact** |
| 2: 3-yr IGP + 3-yr SC, then the same again | 1, 1, 1, 75, 1, 1, 75 | **exact** |
| 3: 1-yr IGP + 3-yr SC, then 2-yr IGP, no SC | 2.5, 2.5, 2.5, 25, **1**, 65 | 2.5, 2.5, 2.5, 25, **2**, 65 |

This section previously said "either the reading is wrong in a way the first
two examples cannot discriminate, or the Guidance Note has an error". The
first disjunct is now closed, and the reason does not depend on the reading
at all.

**Example 3 is inconsistent with Table 6.5 on its own terms.** Take no
reading and simply ask which of the table's 21 printed cells can produce each
stated number:

- **25%** occurs at exactly one cell — *Upon expiry*, column A.
- **65%** occurs at exactly one cell — *2 yrs after expiry*, column C.

Those two pin contract year 4 to row offset 0 and year 6 to row offset +2,
bracketing year 5. The row axis is titled *"Years Before or After Surrender
Charge (SC) Expiration"*, so it advances one per contract year and can only
fall when a new surrender charge appears — and Example 3's contract renews
with **no** surrender charge. Year 5 is therefore forced to *1 yr after
expiry*, whose three values are **10.0%, 2.0%, 75.0%**.

**1.0% is not among them, and does not appear in any at-or-after-expiry row
of the table.** It occurs only in column B's three *before*-expiry rows. So
no re-reading of the column axis can rescue it either, and positing an
unstated second surrender charge cannot: a new charge lets the row *fall*,
and this would need it to *rise* by three in one year.

Column B at *1 yr after expiry* is **2.0%** — exactly what the reading
computes. The table forces the reading's answer.

**The enumeration.** 144 parameterised readings — six axes crossed
(expiry-row offset, which SC event governs, IGP-expiry convention,
expiring-versus-incoming IGP in a renewal year, the non-final-year column,
post-SC row behaviour) — against all three examples:

| readings | examples reproduced |
|---|---|
| 140 | none |
| 2 | Example 2 only |
| 1 | Example 1 only |
| **1** | **Examples 1 and 2** |
| 0 | anything including Example 3 |

Examples 1 and 2 **uniquely determine** the carried reading within that
space, which is a positive finding worth having. A further sweep over every
contract structure — surrender-charge lengths 1 to 8, with and without
renewal, all IGP length sequences — found **no** structure producing Example
3's sequence.

**Readings that do fit all three exist and are contrived.** Relaxing the row
axis so that each column may run on either the SC clock or the IGP clock
yields exactly two fits, both variants of "column B reads its row off the IGP
expiry". They are rejected on three grounds: the row header names one event
and glosses it "(SC)" so there can be no doubt; the extra clause is inert
everywhere except the single disputed cell, which is one free parameter
fitted to one observation; and it makes **three of the drafters' own printed
numbers unreachable** — column B's 2.0% at 1, 2 and 3+ years after expiry
can then never apply to any contract. The straightforward reading reaches all
21 cells, and uses one of exactly those three for the disputed year.

So the two hypotheses are: Example 3's year 5 should read **2%** — cost, one
typo — or an unstated axis switch plus three dead cells.

**Nothing published bears on it.** The table is textually frozen: the APF
2025-11 attachment of 27 May 2024, the 17 April 2025 draft (where it is
numbered 6.56) and the 2026 plenary-adopted amendments are identical in
headers, cells and all three examples, down to a doubled space in "in&nbsp;&nbsp;contract
years" that survives in all three — one source file propagated forward, never
re-derived. Example 3 has read 1% in every version ever exposed. Its
predecessor in the July 2023 SPA exposure draft was a different table
entirely (numbered 6.10, keyed by attained age, and **empty**, with no
Guidance Note), so the IGP columns and the three examples were substituted in
wholesale in one step. No erratum, amendment, comment letter or practitioner
paper works the examples. APF 2026-03, adopted 30 April 2026, reopened
§6.C.5 to fix units in the dynamic-lapse formula and left the Guidance Note
alone — and its authors are the VM-22 Subgroup's chair and vice-chair, which
reads as unnoticed rather than known-and-fixed.

**Status: unresolved, and the refusal stands.** `base_lapse_rate` refuses
the table by name with the dimension identified. The evidence now supports
the stronger statement that the reading is exonerated and Example 3 is
internally inconsistent, but which of the two the drafters intend is theirs
to say — the answerable form of the question is *"under your intended rule,
when does column B's printed 2.0% after-expiry block ever apply?"*

Two shape notes for whoever carries this table if it is resolved: it has
**seven rows, not eleven**, with "3 yrs or more" end rows rather than "5 yrs
or more", so `SURRENDER_CHARGE_ROWS` must not be reused; and Example 3's
contract is under-specified in the text — it never states that the contract
renews into further 1-year IGPs through years 2 and 3, which its own 2.5%
values require.
