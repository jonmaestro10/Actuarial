# VM-22 §6.C — the prescribed assumption sets, which *are* dated data

**Source.** NAIC *Valuation Manual*, 1 January 2026 edition, chapter VM-22,
Section 6 ("Requirements for the Standard Projection Amount"), subsection C.
Produced by the **National Association of Insurance Commissioners**.

<https://content.naic.org/sites/default/files/pbr_data_valuation_manual_current_edition.pdf>

Retrieved and machine-read (PyMuPDF) at pages 22-25 to 22-45 of the chapter.
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

## Two of the eleven are now carried

`engine/report/vm22_prescribed.py` (RFC-067) carries **Table 6.1** and
**Table 6.7**, both transcribed from the primary text above and checked
against it, together with §6.C.2's expense rule and §6.C.8.i's mortality
formula. `tests/test_vm22_prescribed.py` asserts the values against the text
rather than against the module's own constants.

Both bracketed figures are carried as `Provisional`, which is the mechanism
this file's earlier note said the dated-set pattern lacked.

**The other nine are still recorded and not carried**, and `fx_factor`
refuses a category whose table is absent rather than serving the one that is
present. The reason is transcription risk, not effort: each table needs
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
