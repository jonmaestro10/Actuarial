# VM-22 §6.C.5 Table 6.5 — the published record around the table

*A search of the published record for anything bearing on the Example 3
discrepancy recorded in
[`vm22-section-6-prescribed-assumptions.md`](vm22-section-6-prescribed-assumptions.md).
This note is about the **record around** Table 6.5 — errata, drafts, minutes,
commentary, and the route to the drafters. It deliberately does not re-derive
the table's own arithmetic.*

**Retrieved.** 6 August 2026. Every document below was fetched over HTTPS and
machine-read (PyMuPDF for PDF, `word/document.xml` for `.docx`,
`xl/sharedStrings.xml` for `.xlsx`) unless explicitly marked *unverified*.

---

## The headline

**Nothing published bears directly on the Example 3 discrepancy.** There is no
erratum, no amendment, no comment letter, no meeting minute and no consultancy
paper that reproduces, questions or corrects the Guidance Note's worked
examples. I looked and did not find one; this is a clean negative, not a
partial result.

What the record *does* establish is stronger than nothing, and it cuts in a
specific direction:

1. **Table 6.5 is textually frozen.** Four independent primary copies spanning
   July 2023 → April 2025 → the 2026 adopted text are byte-identical in
   headers, cells and all three worked examples. Example 3 has read `1%` at
   contract year 5 in every version ever exposed.
2. **Its second dimension was swapped wholesale, late, and arrived with the
   examples attached.** In the July 2023 exposure the fixed-annuity table was
   keyed by **attained age** and was **an empty placeholder**. The IGP columns
   and the three examples appeared together, already complete, in the next
   version I could find (May 2024), and have never been revised.
3. **No published derivation exists for Table 6.5's cells.** Its sibling Table
   6.4 has a full LIMRA-based development document. The IGP-keyed table has
   none.
4. **The drafters edited §6.C.5 in 2026 and did not touch the Guidance Note.**

Taken together: the discrepancy is very unlikely to be *known and already
fixed*. It reads as unnoticed.

---

## 1. Is there an erratum, correction or APF? — No.

### The authoritative place to check

NAIC publishes adopted-but-not-yet-effective Valuation Manual amendments in a
single standing PDF, linked from the PBR Data page
(<https://content.naic.org/pbr_data.htm>):

<https://content.naic.org/sites/default/files/inline-files/2027%20Valuation%20Manual%20LATF%20Amendments%20Not%20Yet%20Adopted%20by%20Executive%20Committee%20and%20Plenary.pdf>

**Primary, retrieved** (53 pp.). Its cover warns:

> ***WARNING: Not Yet Effective*** … These Valuation Manual amendments have not
> been finally adopted by the Life Insurance and Annuities (A) Committee and/or
> the Executive Committee and Plenary. … If adopted, these amendments will be
> effective 1/1/2027 and thereafter.

This is the definitive answer to "has an adopted-but-unpublished correction
changed Example 3?" — **it lists eight APFs, and none of them do.**

### The one APF that touches §6.C.5 — and what it actually changes

**APF 2026-03**, index entry (p. 2), quoted exactly:

> | 2026-03 | VM-22 Section 6.C.5 Full Surrenders | This amendment clarifies
> calculation mechanics in the VM-22 SPA dynamic lapse formula, specifically in
> the Market Factor and Rate Factor formulas. | 4/30/2026 | 52 |

The APF body (p. 52) identifies the authors and the reason:

> Elaine Lam, California Department of Insurance
> Ben Slutsker, Minnesota Department of Commerce
> Clarify calculation mechanics in the VM-22 SPA dynamic lapse formula,
> specifically in the Market Factor and Rate Factor formulas.
>
> January 1, 2026 Edition of the Valuation Manual – VM-22 Section 6.C.5 Full
> Surrenders

> In the current VM-22 SPA dynamic lapse formula, in order to get the intended
> result, the Market Factor accepted percentage inputs (for CR, MR, BF) as
> whole numbers rather than decimals. For example, 5% was expected to be input
> as 5, instead of 0.05. … **In the end, the Total Lapse result will be
> unchanged.**

The red-lined text (p. 53) confirms the scope — it is a units convention fix to
the *dynamic* lapse multiplier, sitting downstream of the base rate:

> 𝑇𝑜𝑡𝑎𝑙 Lapse = (𝐵𝑎𝑠𝑒 𝐿𝑎𝑝𝑠𝑒 x GMIR Factor + 𝑅𝑎𝑡𝑒 𝐹𝑎𝑐𝑡𝑜𝑟 x MVA Factor) × 𝐼𝑇𝑀 𝐹𝑎𝑐𝑡𝑜r
>
> 𝑀𝑎𝑟𝑘𝑒𝑡 𝐹𝑎𝑐𝑡𝑜𝑟 =  –1.25 x [100 x (𝐶𝑅 – 𝑀𝑅)]X ÷ 100      if CR ≥ MR
>
> X = 2.0 during Surrender Charge Period, 2.5 at Shock, and 2.5 thereafter

**It does not touch Table 6.5, its column headers, or the Guidance Note.**

This is the most probative single finding in the note. Two named regulators —
who are (see §5) the *chair and vice-chair of the VM-22 Subgroup* — opened up
§6.C.5 in March–April 2026 to fix a formula that produced right answers by an
unconventional input convention, and left the base lapse table's worked
examples untouched. If Example 3 were a known error, this was the obvious
vehicle and the obvious moment.

### Other amendment routes checked, all negative

| document | scope | Table 6.5? |
|---|---|---|
| **APF 2026-05** "Editorial Valuation Manual Edits" — Scott O'Neal and McKayla Doyle, NAIC; NAIC staff stamp "5/19/26 / SO" ([docx](https://content.naic.org/sites/default/files/inline-files/APF%202026-05%20Editorial%20Valuation%20Manual%20Edits.docx)) | states its own scope as "VM-20 Section 7.F.3.b, VM-21 Section 7.C.9.b*, VM-21 Section 7.D.3.b, VM-21 Section 13, **VM-22 Section 10.H.2**, VM-31 Section 3.E.3, VM-31 Section 3.F.3.i, VM-50 Section 2.A*, VM-51 Section 2.E.*" | **no** |
| **APF 2025-18** VM-22 Deposit-Type Contracts (exposed 07/12/2025) ([docx](https://content.naic.org/sites/default/files/inline-files/EXPOSURE_APF2025-18%20VM-22%20Deposit-Type%20Contracts_Exposed20251207%20%281%29.docx)) | zero occurrences of `6.C.5`, `Table 6.5`, `Interest Guarantee`, `IGP` | **no** |
| **APF 2025-20** VM-22 Aggregation ([docx](https://content.naic.org/sites/default/files/inline-files/EXPOSURE_APF2025-20%20VM-22%20Aggregation.docx)) | same — zero occurrences | **no** |
| 2026 plenary-adopted amendments ([pdf](https://content.naic.org/sites/default/files/pbr_data_plenary_amendments_current_edition.pdf), 246 pp.) | contains the adopted VM-22 §6 text itself; see §2 | unchanged |

**Note the editorial-APF precedent.** APF 2026-05 shows that NAIC staff
themselves file APFs purely to fix "reference errors and formatting
inconsistencies … to ensure clarity, consistency, and technical accuracy". A
wrong worked example in a Guidance Note is squarely the kind of thing that
route exists for — which is relevant to §5 below.

---

## 2. Exposure drafts — the table never changed, but its *predecessor* did

This was the most promising line of enquiry (a changed header between drafts
would settle the reading outright). It produced a real structural finding, but
not a disambiguating one.

### Four copies of Table 6.5, all identical

| when | document | table no. | Example 3 |
|---|---|---|---|
| 27 May 2024 | APF 2025-11 attachment, *VM-22 PBR for Non Variable Annuities*, from the VM-22 Subgroup page ([docx](https://content.naic.org/sites/default/files/inline-files/APF%202025-11%20VM-22%20PBR%20for%20Non%20Variable%20Annuities%20-%2005272024_v2%20%281%29.docx)) | 6.5 | `2.5%, 2.5%, 2.5%, 25%, 1%, 65%` |
| 17 Apr 2025 | *VM-22 Draft - 04172025* ([docx](https://content.naic.org/sites/default/files/call_materials/VM-22%20Draft%20-%2004172025%20%281%29.docx)) | **6.56** (renumbered) | `2.5%, 2.5%, 2.5%, 25%, 1%, 65%` |
| 2025/26 | 2026 plenary-adopted amendments ([pdf](https://content.naic.org/sites/default/files/pbr_data_plenary_amendments_current_edition.pdf), p. 107–108) | 6.5 | `2.5%, 2.5%, 2.5%, 25%, 1%, 65%` |
| 1 Jan 2026 | the Valuation Manual itself | 6.5 | `1%` — per the repo's existing note; *I did not re-fetch this, the companion agent has it* |

The column headers are identical in all three I retrieved, verbatim:

> Table 6.5: Base Lapse Rates for Fixed Annuities with no Guaranteed Living Benefits
>
> | Years Before or After Surrender Charge (SC) Expiration | Interest Guarantee Period (IGP) | | |
> |---|---|---|---|
> | | In Years where IGP <= 1 Year* | In Years where IGP > 1 Year, and not in Year of IGP Expiry | In Year of an IGP Expiry after IGP > 1 Year |
> | 3 yrs or more after expiry | 3.0% | 2.0% | 55.0% |
> | 2 yrs after expiry | 7.5% | 2.0% | 65.0% |
> | 1 yr after expiry | 10.0% | 2.0% | 75.0% |
> | Upon expiry | 25.0% | 6.0% | 75.0% |
> | 1 yr to expiry | 2.5% | 1.0% | 70.0% |
> | 2 yrs to expiry | 2.5% | 1.0% | 70.0% |
> | 3 yrs or more to expiry | 2.5% | 1.0% | 70.0% |
>
> \* includes floating rate structures

and the Guidance Note, verbatim from the 27 May 2024 docx:

> Guidance Note: Examples of how to apply the table above:
> Example 1: For a contract with an initial 3-year IGP and 3-year SC period,
> then renewing into 1-year IGPs with no SC, the base lapse rates in contract
> years 1 to 7 would be 1%, 1%, 1%, 75%, 10%, 7.5%, 3%.
> Example 2: For a contract with an initial 3-year IGP and 3-year SC period,
> then renewing into another 3-year IGP with 3-year SC period, the base lapse
> rates in  contract years 1 to 7 would be 1%, 1%, 1%, 75%, 1%, 1%, 75%.
> Example 3: For a contract with an initial 1-year IGP and 3-year SC period,
> then renewing into a 2-year IGP with no SC, the base lapse rates in contract
> years 1 to 6 would be 2.5%, 2.5%, 2.5%, 25%, 1%, 65%.

(The doubled space in "in  contract years" survives in every copy — these are
the same source file propagated forward, not independent re-typings. That is
itself evidence the examples were never re-derived after first drafting.)

### The predecessor table was a different animal entirely

*VM-22 SPA Draft July 2023 — Comments/LRT Proposal, Attachment A*, 18 pp.,
page footer "© 2022 National Association of Insurance Commissioners / M-14 /
Confidential":

<https://content.naic.org/sites/default/files/call_materials/VM-22%20SPA%20Draft%20July%202023_Comments%20LRT%20Proposal%20-%20Attachment%20A%20(1).pdf>

**Primary, retrieved.** In this draft the fixed-annuity table is numbered 6.10,
titled differently, keyed by **attained age**, and **completely empty**:

> Table 6.10: Base Lapse Rates for Non-Indexed Fixed Deferred Annuities
> with no Guaranteed Living Benefits
>
> | Years Before or After Surrender Charge Expiration | Attained Age | | | |
> |---|---|---|---|---|
> | | Before 60 | 60 to 69 | 70 to 79 | 80 and above |
> | 5 or more yrs after expiry | | | | |
> | 4 yrs after expiry | | | | |
> | … (all eleven rows blank) | | | | |

There is **no Guidance Note and no worked example** anywhere near it in the
July 2023 draft.

**What this establishes (inference, labelled as such):** the entire IGP
dimension — three new columns, seven re-cut rows, and three worked examples —
was substituted for an attained-age placeholder in a single drafting step
between July 2023 and May 2024, and shipped complete. There is no intermediate
version in the public record in which one could watch the examples being
built. That is consistent with the examples having been written once, by hand,
alongside a newly-invented table structure, and never checked against it again.
It is *not* evidence of which reading is correct.

### Where the sibling table came from, and why that matters

*Standard Projection Amount — Base Surrender Rates for Fixed Indexed Annuities*
(NAIC call materials, 10 pp.):

<https://content.naic.org/sites/default/files/call_materials/Standard%20Projection%20Amount%20Surrenders%20Withdrawals%20and%20Dynamic%20Lapse%20Assumptions%20for%20FIA.pdf>

**Primary, retrieved.** It documents the derivation of what is now **Table 6.4**
in full — data source, cell definitions, and exposure volumes behind every rate:

> Data Source:   2019/2020 LIMRA Fixed Indexed Annuity Study
> Within each policy type, the base surrender rates are categorized by the
> following attributes:
> • Attained Age Group (0-59, 60-64, 65-69, 70-74, 75-79, 80+)
> • Qualified versus Non-Qualified
> • Years before the end, at the end, and after the end of the surrender charge period
> • In-the-moneyness (0-99%, 100-124%, 125%+)

Its "FIA, NO GLWB" grid gives `6.5% / 7.0% / 6.0% / 5.0%` at "5 or more yrs
after expiry" against exposures of `362,439,974 / 872,825,158 / 1,559,973,113 /
2,340,007,393` — and those four rates are exactly the top row of Table 6.4 as
adopted.

**The point is the absence.** This document covers FIA only. I found no
equivalent for the IGP-keyed fixed-annuity table. So Table 6.4's cells are
traceable to a named study with exposure counts behind each one; Table 6.5's
are not traceable to anything published. The earlier drafting-group material
explains why — *VM-22 SPA PHB Assumption Drafting Group*, 10 Dec 2022 deck,
18 pp. (<https://content.naic.org/sites/default/files/call_materials/VM-22%20SPA%20PHB%2010-12-2022.pdf>),
**primary, retrieved**, slide 3, marked "DRAFT, Confidential and Privilege":

> • The current target is to develop PHB assumptions for fixed annuities (FA),
> fixed indexed annuities (FIA), with or w/o guaranteed living benefit (GLB).
> • **MYGA is grouped with FA. If we have time, we may develop specific PHB
> assumptions for MYGA.**

and slide 13, the earliest published recognition that the IGP is a distinct
lapse driver for these contracts:

> Minimum Lapse = 1%
> Maximum Lapse = 60%; **90% for MYGA at the end of the interest guaranteed period**

The multi-year-guarantee/IGP structure therefore entered as a late, explicitly
lower-priority ("if we have time") workstream, bolted onto a framework built
around attained age. That is the drafting history in which a worked example
would most easily go unchecked.

---

## 3. Minutes, comment letters, field test, commentary — all negative

Each of these was fetched and searched for `6.C.5`, `Table 6.5`,
`Interest Guarantee`, `IGP`.

| document | result |
|---|---|
| **VM-22 Comment Log 2021–2024** ([xlsx](https://content.naic.org/sites/default/files/inline-files/VM22%20Comment%20Log%20-%202021%20Comments_2022%20Comments_SPA%20Comments_Project%20Plan%20%281%29.xlsx)); sheets `2026 Target Timeline`, `Comment Log - SPA Exposure`, `Comment Log - 2022 Exposure`, `Comment Log - 2021 Exposure` | **Primary, retrieved.** 426 distinct strings. Nothing on Table 6.5, IGP, or the examples. Nearest items are topic-level: *"Base and Dynamic Lapse Rates / Calibrate base lapse rates in tandem with development of dynamic lapse formula"* and *"Dynamic Lapse - Distribution Channel"*. Neither has a recorded outcome touching the base table's structure. |
| **VM-22 Field Test Specifications, 30 July 2024** ([pdf](https://content.naic.org/sites/default/files/inline-files/20240730F%20VM22%20Field%20Test%20Specs%20%281%29.pdf), 9 pp.) — a *later* revision than the 6 Mar 2024 one already logged at `docs/sources/naic-vm22-field-test-2024.md` | **Primary, retrieved.** **Zero** occurrences of any search term. The field test specs do not spell out the base lapse assumption. |
| **VM-22 Field Test participant results, 7 Apr 2025** ([pdf](https://content.naic.org/sites/default/files/inline-files/PUBLIC_20250407F%20FT%20Participant%20Results%20for%20VM-22%20Subgroup_final%20%281%29.pdf), 32 pp.) | **Primary, retrieved.** Zero occurrences. |
| **LATF Meeting Materials, Spring National Meeting, 21–22 March 2026** ([pdf](https://content.naic.org/sites/default/files/national_meeting/LATF%20Materials%20SpNM%202026.pdf), 229 pp.) | **Primary, retrieved.** Two hits, both the APF 2026-03 dynamic-lapse item already covered. |
| **LATF summaries, 2025 Spring / Summer / Fall National Meetings** ([spring](https://content.naic.org/sites/default/files/national_meeting/2025-spnm-a-latf-summary.pdf), [summer](https://content.naic.org/sites/default/files/national_meeting/2025-sunm-a-latf-summary.pdf), [fall](https://content.naic.org/sites/default/files/national_meeting/2025-fanm-summary-a-latf.pdf)) | **Primary, retrieved.** No hits on any search term. The only lapse discussions are VM-20 ULSG. |
| **Milliman, *Current state of principle-based reserving for non-variable annuities (VM-22)*** (<https://www.milliman.com/en/insight/current-state-principle-based-reserving-non-variable-annuities-vm-22>) | **Secondary, retrieved.** Explicitly does **not** mention Table 6.5, IGP, or any worked lapse sequence. It says only that the SPA uses "prescribed assumptions for mortality, policyholder behavior, and expenses, varying by the contract type". |

**Searches that returned nothing on point.** Targeted queries for a published
worked example — `"VM-22" "Table 6.5" lapse`; `VM-22 "interest guarantee
period" lapse rate table guidance note surrender charge example`; and a query
naming Milliman / Oliver Wyman / Deloitte against Table 6.5 — surfaced no
document that reproduces the sequences. **Nobody has published a worked lapse
sequence off Table 6.5.** The "jackpot" the brief hoped for does not exist as
of this search.

*Not exhaustively checked (declared gap):* ACLI's comment letter on the October
re-exposure of VM-22 was downloaded (4 pp.,
<https://content.naic.org/sites/default/files/call_materials/ACLI%20Comments%20on%20the%20October%20Re-Exposure%20of%20VM-22.pdf>)
but predates the SPA assumption tables and I did not find §6.C.5 content in it.
I did not systematically sweep every LATF minutes packet from 2021–2026, nor
the Academy's full VM-22 comment-letter series on actuary.org. Given that the
comment log, the field test, three national-meeting summaries and the 229-page
2026 materials packet are all silent, I judge the marginal return low, but the
sweep is genuinely incomplete.

---

## 4. Who drafted this, which sharpens where to send it

An incidental but useful provenance finding. In the 2026 plenary-adopted
amendments PDF, every page of the VM-22 draft attachment — including pp. 107–108
carrying Table 6.5 and its Guidance Note — bears the running footer:

> 1850 M Street NW     Suite 300     Washington, DC 20036     Telephone 202 223 8196     Facsimile 202 872 1948    www.actuary.org

That is the **American Academy of Actuaries**' address. The APF cover page for
the same attachment names the submitter as:

> VM-22 (A) Subgroup
> VM-22 principle-based reserving (PBR) for non-variable annuities
> May 28, 2025 / VM-22 PBR Draft 05 28 2025.docx

*Inference, labelled as such:* the VM-22 §6 text was drafted on an Academy
document template and carried into the Valuation Manual through a Subgroup APF.
The Academy's Annuity Reserves Work Group is therefore a plausible second
audience for the question alongside the Subgroup — though I could not retrieve
an ARWG page or roster to confirm current membership, so **that routing is
unverified**.

---

## 5. The route to the drafters — concrete, current, verified

### The people

From the NAIC's 2026 committee membership lists, read from the raw HTML of
<https://content.naic.org/committee-reports?committeeID=~%7C1000000185>
(**primary, retrieved** — I parsed the page source rather than trusting a
summariser, because an earlier automated read of this page conflated two
committees):

**Valuation Manual (VM)-22 (A) Subgroup** — list dated **June 11, 2026**:

> Ben Slutsker, Chair — Minnesota
> Elaine Lam, Vice Chair — California
> … Lei Rao-Knight (CT), Matt Cheung (IL), Mike Yanacheak (IA), William Leung (MO),
> David Wolf (NJ), William B. Carmello (NY), Matt Elston (OH), Yujie (Iris) Huang (TX),
> Tomasz Serbinowski (UT), Craig Chupp (VA)
> NAIC Committee Support: Amy Fitzpatrick

**Life Actuarial (A) Task Force** — list dated **July 13, 2026**:

> Amanda Crawford, Chair — Texas (representative: Rachel Hemphill)
> Scott A. White, Vice Chair — Virginia (representative: Craig Chupp)
> NAIC Committee Support: Scott O'Neal / Amy Fitzpatrick

**This is the single most actionable fact in the note:** Ben Slutsker and Elaine
Lam, the Subgroup's chair and vice-chair, are personally the two named authors
of **APF 2026-03**, the amendment against **VM-22 §6.C.5** adopted 30 April
2026. They are demonstrably the people currently editing this exact subsection.

### The contacts

From the LATF page (<https://content.naic.org/committees/a/life-actuarial-tf>),
**verified from `mailto:` links in the raw HTML**:

> Scott O'Neal — Managing Life Actuary — 816-783-8814 — **soneal@naic.org**
> Jennifer Frasier — Assistant Managing Life Actuary — 816-783-8834 — **jfrasier@naic.org**

From the VM-22 Subgroup page
(<https://content.naic.org/committees/a/valuation-manual-22-sg>), **primary,
retrieved**:

> Amy Fitzpatrick — Life Associate Actuary — 816-783-8837

⚠️ **Unverified:** Amy Fitzpatrick's *email address* is **not** published as a
`mailto:` link on either page — only her name, title and phone. An automated
read of the page returned `afitzpatrick@naic.org`, which matches the NAIC's
evident `first-initial + surname` convention but which I **could not confirm
from the page source**. Do not rely on it. The only email addresses actually
published on both the LATF and VM-22 Subgroup pages are `soneal@naic.org` and
`news@naic.org`; `jfrasier@naic.org` appears on the LATF page.

### The mechanism

The blank Amendment Proposal Form:

<https://content.naic.org/sites/default/files/committee_related_documents/committees_a_latf_b_hatf_amend_proposal_form.doc>

**Primary, retrieved** (binary `.doc`, read via `strings`). It is a four-field
form — identify yourself and the issue; identify the document and location;
supply a tracked-changes red-line; state the reason — carrying this footnote,
which is the key routing instruction:

> \* This form is not intended for minor corrections, such as formatting,
> grammar, cross-references or spelling. Those types of changes do not require
> action by the entire group and may be submitted via letter or email to the
> NAIC staff support person for the NAIC group where the document originated.

**Reading of that footnote (inference, labelled as such):** a Guidance Note
whose worked example contradicts its own table is *not* a formatting or
spelling matter — resolving it either changes a prescribed lapse rate or
changes how every company reads the table's third column. It needs the APF
route, not the email route. The precedent cuts the same way: APF 2026-03 was
filed for something the drafters themselves described as leaving "the Total
Lapse result … unchanged", i.e. an even lower-stakes clarification than this
one.

### Recommended route, in order

1. **File an APF** against *January 1, 2026 Edition of the Valuation Manual —
   VM-22 Section 6.C.5*, using the form above, sent to **Scott O'Neal
   (soneal@naic.org)**, who is the LATF staff support person and whose initials
   ("S.O.", "SO") appear on the NAIC staff-comment stamps of both APF 2026-03
   and APF 2026-05. Field 3 should offer *two* red-lines — one correcting
   Example 3 to `2%`, one amending the column header if the note's `1%` is
   intended — so the Subgroup can pick the direction rather than having to
   re-derive it.
2. **Copy the Subgroup chair and vice-chair**, Ben Slutsker (Minnesota
   Department of Commerce) and Elaine Lam (California Department of Insurance),
   as the authors of the last amendment to this subsection.
3. **Or raise it on the record first.** The VM-22 Subgroup and LATF both hold
   public Webex calls with published agendas; LATF's exposure-draft and meeting
   listing is on its committee page, and the Subgroup's most recent exposure was
   *VM-22 Retrospective Application Exposure*, comments to Scott O'Neal by
   **Monday, June 22, 2026** (a 90-day period, now closed — as of this search
   the Subgroup page states "There are no meeting materials at this time").
   There was no open VM-22 comment period on 6 August 2026; the only LATF
   exposure open then was a 30-day period ending **Friday, August 28, 2026**,
   and it concerns the Financial Analysis and Financial Condition Examiners
   Handbooks, not the Valuation Manual.

### How to check definitively, later

Re-fetch the "not yet adopted" PDF at the fixed URL in §1 — it is a standing
link that NAIC replaces in place as APFs are adopted — and re-read §6.C.5 in
each successive January edition of the Valuation Manual. If a correction is
ever made, one of those two will show it, and the repo's existing test in
`tests/test_vm22_prescribed.py` is already the right place to catch it.

---

## Ranked list of what was retrieved

**Primary — fetched, machine-read, quoted above**

1. 2027 VM LATF Amendments Not Yet Adopted (53 pp.) — the erratum answer, and APF 2026-03 in full
2. APF 2025-11 attachment, VM-22 PBR draft, 27 May 2024 (docx) — earliest complete Table 6.5
3. VM-22 SPA Draft July 2023, Attachment A (18 pp.) — the attained-age, empty predecessor table
4. 2026 plenary-adopted VM amendments (246 pp.) — Table 6.5 as adopted, Academy footer
5. VM-22 Draft 04172025 (docx) — Table 6.5 renumbered 6.56, otherwise identical
6. SPA Base Surrender Rates for Fixed Indexed Annuities (10 pp.) — Table 6.4's derivation; nothing for 6.5
7. VM-22 SPA PHB Assumption Drafting Group, 10 Dec 2022 (18 pp.) — "MYGA is grouped with FA… if we have time"
8. NAIC 2026 committee membership lists (HTML source) — Subgroup and LATF leadership
9. LATF and VM-22 Subgroup committee pages (HTML source) — staff contacts, exposures
10. Blank LATF/HATF Amendment Proposal Form (.doc) — the footnote on what the form is *not* for
11. APF 2026-05 Editorial VM Edits (docx) — scope excludes §6.C.5
12. APF 2025-18, APF 2025-20 (docx) — scope excludes §6.C.5
13. VM-22 Comment Log 2021–2024 (xlsx) — 426 strings, nothing on point
14. VM-22 Field Test Specs 30 July 2024 (9 pp.) — zero hits
15. VM-22 Field Test participant results, Apr 2025 (32 pp.) — zero hits
16. LATF Materials SpNM 2026 (229 pp.); LATF 2025 Spring/Summer/Fall summaries — zero hits on point
17. ACLI Comments on the October Re-Exposure of VM-22 (4 pp.) — retrieved; predates the SPA tables

**Secondary — fetched, characterised, not load-bearing**

18. Milliman, *Current state of PBR for non-variable annuities (VM-22)* — confirmed silent on Table 6.5

**Unverified — could not confirm from primary source**

- **Amy Fitzpatrick's email address.** Name, title and phone are published; the
  address is not. `afitzpatrick@naic.org` is a convention-based guess returned
  by an automated page read and is **not** confirmed.
- **The American Academy of Actuaries' Annuity Reserves Work Group** as the
  drafting body for §6.C.5. Inferred from the Academy footer on the adopted
  attachment. I did not retrieve an ARWG page, roster or chair.
- **The 1 January 2026 Valuation Manual's own Table 6.5.** I relied on the
  repo's existing extract and on three other primary copies rather than
  re-fetching the 2026 VM PDF, since a companion agent is doing exactly that.
- No fetch in this search failed, was blocked, or returned an empty body.
  Every URL above returned HTTP 200 with the promised content.

---

## Confidence

**High** that no published erratum or amendment corrects Example 3 as of
6 August 2026. The NAIC maintains one canonical list of adopted-but-unpublished
amendments; I read all eight entries in it, and the only §6.C.5 item is
demonstrably about something else.

**High** that Table 6.5 and its Guidance Note are textually unchanged across
every exposure draft in the public record, including the shared whitespace
artefact that shows the examples were propagated rather than re-derived.

**High** that no consultancy, Academy or SOA publication works the examples.
Multiple query formulations, nothing.

**Moderate** on the drafting history. The July 2023 → May 2024 substitution of
the IGP dimension for an attained-age placeholder is solid primary evidence,
but I could not find an intermediate draft, a drafting-group deck, or a minute
in which the IGP columns are proposed and their meaning explained. Such a
document may exist in materials I did not reach.

**What I could not establish, plainly:** *why* the third column is worded as it
is, and therefore which of the two candidate readings the drafters intended.
Nothing published defines "In Year of an IGP Expiry after IGP > 1 Year" beyond
the header text itself, and nothing published explains why Example 3's year 5
should be 1% rather than 2%. The question is genuinely open in the record, and
answering it requires asking the people in §5.
