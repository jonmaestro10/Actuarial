# VM-22 §6.C.5, Table 6.5 — evidence for a human decision

Investigation of the unresolved discrepancy recorded in
`docs/sources/vm22-section-6-prescribed-assumptions.md`
("Table 6.5 fails one of its own worked examples").

**Nothing in the repo was modified.** No table was carried, `engine/` is
untouched, `tests/test_vm22_prescribed.py`'s pinned discrepancy is unchanged.
The repo's 31 tests pass on the branch as found.

**Why this is in `docs/sources/` at all.** The rest of this directory records
published figures the engine is checked *against*. This one records a
published table the engine **refuses to carry**, and why — the same kind of
claim, needing the same provenance. `base_lapse_rate` raises on
`table="fixed"`; this is the working that justifies the refusal, and the thing
to re-run against the 2027 edition.

The scripts are in [`scripts/`](scripts/) beside it and run standalone with no
repo imports:

| file | what it does |
|---|---|
| `scripts/enumerate_readings.py` | stage 1: 144 parameterised readings × 3 examples |
| `scripts/brute_force_cells.py` | model-free: which cells *could* produce each example |
| `scripts/rescue_reading.py` | the minimal reading that rescues Example 3, and its cost |
| `scripts/clock_family.py` | stage 2: row-clock-per-column readings, with dead-cell counts |

`python docs/sources/scripts/enumerate_readings.py` reproduces §2.3's
signature table. The PDF is not kept — see this directory's README on extracts
rather than PDFs — and §0 records its digest so the same file can be
re-fetched.

**The corrections this note raised have been made**: the page range (§5.1a)
and the stale `__fingerprint__` string (§5.1b) are fixed, and §5.1(c)'s
framing point is applied to `vm22-section-6-prescribed-assumptions.md`,
`vm22_prescribed.py` and `tests/test_vm22_prescribed.py`. Table 6.4's
transcription in §1 was re-checked against `_BASE_LAPSE['indexed']` cell for
cell before this file was committed, and the APF quotations in the companion
note were checked against the source PDF.

---

## 0. Provenance and extraction soundness

Fetched `https://content.naic.org/sites/default/files/pbr_data_valuation_manual_current_edition.pdf`
on 2026-08-06.

- SHA-256 `6613dea507978eab74b2ca756f59ee2abe3c8142aa404bff27affd8aeee29d40`
- 4,580,180 bytes, PDF 1.7, 457 pages
- embedded title `2026 Edition - Valuation Manual`, creationDate `2026-01-07`
- cover page: "Jan. 1, 2026 Edition"; front matter: "NAIC Adoptions through
  August 13, 2025"

This is the same edition `docs/sources/vm22-section-6-prescribed-assumptions.md`
records. **The current-edition PDF has not moved on from what the repo
recorded.** Nothing here is resolved by a text change.

Extracted with PyMuPDF 1.28.0 (MuPDF 1.29.0).

**Located by heading text, bounded at the next heading.** VM-22 occupies PDF
pages 226–317 (chapter pages 22-1 … 22-92). `Section 6: Requirements for the
Standard Projection Amount` begins at chapter page 22-25 and ends at 22-53,
where `Section 7: Stochastic Exclusion and Single Scenario Testing` begins.
Within it, `C. Prescribed Assumptions` begins 22-27 and runs to the end of
Section 6; its numbered subsections are 1 Assignment of Guaranteed Benefit
Type (22-27), 2 Maintenance Expenses (22-28), 3 Guarantee Actuarial Present
Value (22-29), 4 Partial Withdrawals (22-30), **5 Full Surrenders (22-32)**,
6 Annuitizations (22-35), 7 Index Transfers and Future Deposits (22-35),
8 Mortality (22-35), 9 Account Value Depletions (22-52), 10 Other Voluntary
Contract Terminations (22-52), 11 Crediting Rates and Investment Spread
(22-53). §6.C.5 therefore spans 22-32 to 22-35 and is bounded above by
§6.C.4 and below by §6.C.6. Tables 6.4, 6.5 and 6.6 are all inside it.

**Extraction verified before it was trusted.** Tables 6.4 and 6.6 sit on the
same two pages as Table 6.5 and are already carried in
`engine/report/vm22_prescribed.py` as `_BASE_LAPSE`. Parsing them with the
same code path that parsed 6.5 reproduces the module's constants exactly:

```
6.4 matches code: True
6.6 matches code: True
```

Table 6.5 was then re-extracted a second time by **coordinate-based** table
detection (`page.find_tables()`), independently of reading order, to rule out
a column mis-assignment. Both methods agree cell for cell.

**No layout ambiguity to declare.** The grid is fully ruled; the only merged
cells are the two spanning header cells (`Years Before or After Surrender
Charge (SC) Expiration` spanning the two header rows, `Interest Guarantee
Period (IGP)` spanning the three value columns), and both are unambiguous.
The single footnote marker (`*`) is attached to the column-1 header and its
text `* includes floating rate structures` sits directly beneath the grid.
Every number below is transcribed, none inferred.

---

## 1. Verbatim transcription

### §6.C.5 — Full Surrenders (chapter page 22-32), opening prose

> **5. Full Surrenders**
>
> For contracts that offer surrender benefits, base lapse and full surrender
> rates shall be dynamically adjusted upward (or downward) when the actual
> credited rate is below (or above) the competitor rate. For contracts with a
> guaranteed living benefit, base lapse and full surrender rates shall be
> further adjusted based on the ITM of the rider value. The following formula
> shall be used:
>
> Total Lapse = (Base Lapse x GMIR Factor + Rate Factor x MVA Factor) × ITM Factor

The subsection then defines ITM Factor, Rate Factor, MVA Factor, GMIR Factor,
Market Factor, Minimum/Maximum Lapse, Crediting Rate, Market Rate, Pricing
Spread and Buffer Factor (22-32 to 22-33), and then:

> **Base Lapse**
>
> Base Lapse = Determined using the following tables:

followed immediately by Table 6.4, then Table 6.5 and its Guidance Note, then
Table 6.6, then the closing prose:

> Any lapse skew applied should be consistent with the company's best
> estimate.
>
> For contracts in which there is no account value or surrender benefit, such
> as some contracts within the Payout Annuity Reserving Category and Longevity
> Reinsurance Reserving Category, this section is not applicable.

**That is the whole of §6.C.5's guidance on how to read Table 6.5.** There is
no other prose in the subsection bearing on the row or column axes. The
Guidance Note's three examples are the only instruction.

One nearby item worth having, from §6.C.5's Market Rate definition on 22-33 —
it is the only other place in §6.C that keys off the IGP, and it confirms the
IGP is a contract-level attribute that varies over the projection:

> For indexed annuities and fixed annuities with Interest Guarantee Period < 2 Years:
> MR = Max (3-month Treasury rate, 5-year Treasury rate plus 50% A / 50% AA spread) minus Pricing Spread
> For fixed annuities with Interest Guarantee Period ≥ 2 Years:
> MR = N-year Treasury rate plus 50% A / 50% AA spread minus Pricing Spread

### Table 6.5, verbatim, with headers exactly as printed (22-34)

> **Table 6.5: Base Lapse Rates for Fixed Annuities with no Guaranteed Living Benefits**
>
> | Years Before or After Surrender Charge (SC) Expiration | Interest Guarantee Period (IGP) | | |
> |---|---|---|---|
> | | **In Years where IGP <= 1 Year\*** | **In Years where IGP > 1 Year, and not in Year of IGP Expiry** | **In Year of an IGP Expiry after IGP > 1 Year** |
> | 3 yrs or more after expiry | 3.0% | 2.0% | 55.0% |
> | 2 yrs after expiry | 7.5% | 2.0% | 65.0% |
> | 1 yr after expiry | 10.0% | 2.0% | 75.0% |
> | Upon expiry | 25.0% | 6.0% | 75.0% |
> | 1 yr to expiry | 2.5% | 1.0% | 70.0% |
> | 2 yrs to expiry | 2.5% | 1.0% | 70.0% |
> | 3 yrs or more to expiry | 2.5% | 1.0% | 70.0% |
>
> \* includes floating rate structures

Note the shape differs from Tables 6.4 and 6.6, which the repo already
carries: **Table 6.5 has seven rows, not eleven**, and its end rows are
"3 yrs or more" rather than "5 yrs or more". Any future carry must not reuse
`SURRENDER_CHARGE_ROWS`.

### The Guidance Note, verbatim and complete (22-34)

> Guidance Note: Examples of how to apply the table above:
>
> Example 1: For a contract with an initial 3-year IGP and 3-year SC period,
> then renewing into 1-year IGPs with no SC, the base lapse rates in contract
> years 1 to 7 would be 1%, 1%, 1%, 75%, 10%, 7.5%, 3%.
>
> Example 2: For a contract with an initial 3-year IGP and 3-year SC period,
> then renewing into another 3-year IGP with 3-year SC period, the base lapse
> rates in  contract years 1 to 7 would be 1%, 1%, 1%, 75%, 1%, 1%, 75%.
>
> Example 3: For a contract with an initial 1-year IGP and 3-year SC period,
> then renewing into a 2-year IGP with no SC, the base lapse rates in contract
> years 1 to 6 would be 2.5%, 2.5%, 2.5%, 25%, 1%, 65%.

(The doubled space in "in  contract years" in Example 2 is in the source.)

For reference, the two neighbouring tables as printed, which is what the
extraction was checked against:

> **Table 6.4: Base Lapse Rates for Indexed Annuities with no Guaranteed Living Benefits**
>
> | Years Before or After Surrender Charge Expiration | Attained Age | | | |
> |---|---|---|---|---|
> | | Before 60 | 60 to 69 | 70 to 79 | 80 and above |
> | 5 yrs or more after expiry | 6.5% | 7.0% | 6.0% | 5.0% |
> | 4 yrs after expiry | 8.0% | 8.5% | 6.5% | 5.0% |
> | 3 yrs after expiry | 8.5% | 9.5% | 7.0% | 5.5% |
> | 2 yrs after expiry | 11.0% | 12.0% | 9.0% | 7.0% |
> | 1 yr after expiry | 15.0% | 17.5% | 13.5% | 9.0% |
> | Upon expiry | 33.5% | 41.5% | 37.0% | 23.5% |
> | 1 yr to expiry | 4.5% | 3.5% | 4.0% | 4.0% |
> | 2 yrs to expiry | 4.0% | 3.5% | 3.0% | 3.0% |
> | 3 yrs to expiry | 2.5% | 2.0% | 2.0% | 2.0% |
> | 4 yrs to expiry | 3.0% | 2.5% | 2.5% | 2.5% |
> | 5 yrs or more to expiry | 2.0% | 2.5% | 2.0% | 1.5% |

---

## 2. The answer: no reading of the printed grid reproduces all three, and
## Example 3 is inconsistent with the grid on its own

The strong result is not "the repo's reading fails Example 3". It is that
**Example 3 contradicts Table 6.5 without reference to Examples 1 and 2 at
all**, for any reading whose row axis means what its printed header says.

### 2.1 The three values that pin it

Take no reading. Just ask which cells of the 21 could produce each number the
Note states (`brute_force_cells.py`):

Example 3 = 2.5%, 2.5%, 2.5%, 25%, 1%, 65%

| contract year | value | cells in the grid with that value |
|---|---|---|
| 1 | 2.5% | `1 yr to`/A, `2 yrs to`/A, `3+ yrs to`/A |
| 2 | 2.5% | same three |
| 3 | 2.5% | same three |
| 4 | **25%** | **`Upon expiry`/A — unique** |
| 5 | 1% | `1 yr to`/B, `2 yrs to`/B, `3+ yrs to`/B |
| 6 | **65%** | **`2 yrs after expiry`/C — unique** |

Years 4 and 6 are each satisfied by exactly one cell in the whole table. So
Example 3 pins, of its own accord:

- **row(year 4) = `Upon expiry`** (offset 0)
- **row(year 6) = `2 yrs after expiry`** (offset +2)

and year 5 is sandwiched between them.

### 2.2 What the row header forces

The row axis is titled *"Years Before or After Surrender Charge (SC)
Expiration"*. For a contract year `t` and a surrender-charge expiry event at
year `E`, the row offset is `t − E`. Therefore, mechanically:

- it **advances by exactly +1** per contract year;
- it can **hold** only at the clipped end rows (`3 yrs or more` either way);
- it can **fall** only when the governing expiry event changes — i.e. only
  when a *new* surrender charge exists.

Example 3's contract has one surrender-charge period and explicitly **"no
SC"** after renewal. So row(5) is forced to +1 by both neighbours.

Row +1 (`1 yr after expiry`) offers only three values in the entire table:

| column | value |
|---|---|
| A — IGP ≤ 1 year | 10.0% |
| B — IGP > 1 year, not expiry year | **2.0%** |
| C — year of an IGP expiry after IGP > 1 year | 75.0% |

**1% is not among them.** In fact 1% occurs nowhere in the table except
column B's three *before*-expiry rows, all of which require row(5) < 0 — which
contradicts row(4) = 0 for any non-decreasing row axis.

This has two consequences worth stating separately, because they cut off the
two obvious escape routes:

**(a) No re-reading of the *column* axis can help.** The disputed value is
unreachable in row +1 under all three columns. Whatever the columns mean, the
answer in Example 3's year 5 is 10%, 2% or 75%.

**(b) Positing a phantom second surrender charge does not help either.** A new
SC event lets the row *fall*; Example 3 needs it to *rise* by 3 in one year
(−1 → +2). Sweeping the model-free assignment space while granting 0, 1 and 2
permitted row resets returns **0 unit-step assignments in every case** (30 and
78 merely-non-decreasing assignments appear at 1 and 2 resets, but none of
them steps by one per year, so none corresponds to a coherent "years
before/after expiry" axis).

### 2.3 Stage-1 enumeration: 144 parameterised readings

`enumerate_readings.py` crosses six axes — where the "Upon expiry" row falls
relative to an n-year SC (`k ∈ {0,1}`); which SC event governs a year
(nearest-upcoming-else-last vs most-recent-else-next); whether "the Year of an
IGP Expiry" is the renewal year or the last year of the IGP; whether the
expiring or the incoming IGP sets the column in a renewal year; which column a
non-final year of a multi-year IGP takes (B, A or C); and what the row does
once the SC is gone (continue, switch to an IGP clock, or freeze) — and runs
each against all three examples. Example 3 is run under **three** readings of
its own under-specified prose (see §4.1).

```
total readings enumerated: 144
  fit Example 1: 2
  fit Example 2: 3
  fit Example 3 (annual IGPs through SC, 2-yr from yr 4): 0
  fit Example 3 (2-yr IGP from yr 2):                     0
  fit Example 3 (2-yr IGP from yr 5):                     0
  fit Ex1 AND Ex2: 1
  fit any Ex3 variant: 0
  fit all three: 0
```

Signatures over the whole cross-product — this is the "which pairs does each
reading satisfy" table asked for:

| readings | examples reproduced |
|---|---|
| 140 | none |
| 2 | Example 2 only |
| 1 | Example 1 only |
| **1** | **Examples 1 and 2** |
| 0 | anything including Example 3 |

The single reading that fits Examples 1 and 2 is exactly the one the repo
recorded, and Examples 1 and 2 **uniquely determine it** within this space:

> `sc_k=1` (an n-year SC has its `Upon expiry` row at contract year n+1);
> `sc_pick=next_or_last`; `igp_expiry=renewal_year`;
> `renewal_governed_by=expiring`; `nonfinal_col=B`; `row_after_sc=continue`.

Call it **reading S**. In words: the row is contract year minus the year the
surrender charge lapses (the nearest upcoming SC expiry governs, else the last
one); the column is C in the year an IGP longer than one year expires, A when
the governing IGP is one year or shorter, and B otherwise; and in a year that
both ends one IGP/SC and starts the next, the *expiring* event governs.

Reading S gives:

| example | Note | reading S | |
|---|---|---|---|
| 1 | 1, 1, 1, 75, 10, 7.5, 3 | 1, 1, 1, 75, 10, 7.5, 3 | exact |
| 2 | 1, 1, 1, 75, 1, 1, 75 | 1, 1, 1, 75, 1, 1, 75 | exact |
| 3 | 2.5, 2.5, 2.5, 25, **1**, 65 | 2.5, 2.5, 2.5, 25, **2**, 65 | year 5 differs |

The cell trace confirms the repo's characterisation of where the examples
stop discriminating:

```
Ex1  3+ to/B | 2 to/B | 1 to/B | Upon/C | 1 after/A | 2 after/A | 3+ after/A
Ex2  3+ to/B | 2 to/B | 1 to/B | Upon/C | 2 to/B    | 1 to/B    | Upon/C
Ex3  3+ to/A | 2 to/A | 1 to/A | Upon/A | 1 after/B | 2 after/C
```

Every column-B year in Examples 1 and 2 is a *before*-expiry year. Example 3's
year 5 is the only column-B year in the whole Guidance Note that falls
*after* the surrender charge has gone. That is precisely the cell in dispute.

### 2.4 Completeness sweep: it is not a misreading of the prose

Under reading S, sweeping **every** contract structure — SC lengths 1–8 with
and without a renewed SC of any length 1–8, crossed with every IGP length
sequence over lengths 1–6 for the first four IGPs — asking whether *any*
structure produces `2.5, 2.5, 2.5, 25, 1, 65`:

```
Sweep A: contract structures reproducing Ex3 under reading S: 0
```

So the failure is not an artefact of how Example 3's prose was interpreted. No
fixed-annuity contract of any shape produces that sequence under the reading
Examples 1 and 2 establish.

---

## 3. There *are* readings that fit all three — and they are more contrived,
## in a way the table's own printed values expose

Section 2 rules out any reading whose row is a single SC clock. Relaxing that
is the only remaining room, so `clock_family.py` enumerates the natural
relaxation: **each column may read its row off the SC clock or off the IGP
clock**, either always or only once the surrender charge has gone. Sixteen
readings; all of them agree with reading S on the column rule, so the question
is purely what the row means.

| row clock A | B | C | applied | Ex 1 | Ex 2 | Ex 3 | printed cells no contract can reach |
|---|---|---|---|---|---|---|---|
| sc | sc | sc | always | FIT | FIT | miss | **0** |
| sc | sc | igp | always | FIT | FIT | miss | 6 |
| sc | sc | igp | after SC | FIT | FIT | miss | 3 |
| **sc** | **igp** | **sc** | **always** | **FIT** | **FIT** | **FIT** | **4** |
| **sc** | **igp** | **sc** | **after SC** | **FIT** | **FIT** | **FIT** | **3** |
| sc | igp | igp | always | FIT | FIT | miss | 10 |
| sc | igp | igp | after SC | FIT | FIT | miss | 6 |
| igp | sc | sc | always | miss | FIT | miss | 5 |
| igp | sc | sc | after SC | miss | FIT | miss | 3 |
| igp | sc | igp | always | miss | FIT | miss | 11 |
| igp | sc | igp | after SC | miss | FIT | miss | 6 |
| igp | igp | sc | always | miss | FIT | miss | 9 |
| igp | igp | sc | after SC | miss | **FIT** | **FIT** | 6 |
| igp | igp | igp | always | miss | FIT | miss | 15 |
| igp | igp | igp | after SC | miss | FIT | miss | 9 |

Exactly **two** readings fit all three examples, and they are the same idea in
strong and weak form: *column B reads its row off the IGP expiry rather than
the SC expiry* (always, or only once the SC has gone). Both give:

```
Example 1: 1.0, 1.0, 1.0, 75.0, 10.0, 7.5, 3.0     (Note: 1, 1, 1, 75, 10, 7.5, 3)
Example 2: 1.0, 1.0, 1.0, 75.0, 1.0, 1.0, 75.0     (Note: 1, 1, 1, 75, 1, 1, 75)
Example 3: 2.5, 2.5, 2.5, 25.0, 1.0, 65.0          (Note: 2.5, 2.5, 2.5, 25, 1, 65)
```

Call the weaker one **reading R** (`rescue_reading.py`): reading S plus one
clause — *in a year that takes column B, if the surrender charge has already
expired, measure the row from the IGP's expiry year instead*.

### 3.1 The parsimony judgement: reading R is more contrived, and self-refuting

**More contrived than the reading that fails Example 3.** Three grounds, in
increasing order of force.

**(i) The table's printed headers do not support it.** The row axis is named
*"Years Before or After Surrender Charge (SC) Expiration"* — one axis, one
event, named explicitly, with "(SC)" glossed so there can be no doubt which
expiry is meant. Reading R makes that header mean "years before or after
surrender-charge expiration, except in the middle column after the surrender
charge has gone, where it means years before or after **interest guarantee
period** expiration". Nothing in §6.C.5 says that. Had the drafters intended
it, the row header is where they would have said so, and the header they wrote
is the one that contradicts it. Reading S needs no clause the headers do not
contain.

**(ii) It is one free parameter fitted to exactly one disputed cell, and it is
unfalsifiable against the rest of the evidence.** The extra clause is inert
wherever a surrender charge is still in force — which is every column-B year
in Examples 1 and 2 — and inert in columns A and C entirely. Its *only* live
application in the whole Guidance Note is Example 3's year 5. It was
constructed to change that number and it changes nothing else the drafters
wrote down. That is a curve fit through one point, not a reading.

**(iii) It kills three of the table's own printed numbers.** This is the
decisive one, and it is a fact about the grid rather than a matter of taste.
Sweeping every contract structure and asking which of the 21 printed cells any
contract can ever land on:

```
reading S: 21/21 cells reachable — every printed cell is reachable
reading R: 18/21 cells reachable
    UNREACHABLE: 1 yr after expiry    column B  (printed value 2.0%)
    UNREACHABLE: 2 yrs after expiry   column B  (printed value 2.0%)
    UNREACHABLE: 3+ yrs after expiry  column B  (printed value 2.0%)
```

(The stronger "always" variant kills a fourth, `Upon expiry`/B = 6.0%.)

Under reading R, a year in column B is by construction inside a multi-year IGP
and not its expiry year, so on the IGP clock it is *always* some number of
years *to* expiry — never after. Column B's entire after-expiry block becomes
dead print. So reading R asserts that the drafters laid out a 7×3 grid, gave
column B a distinct value of **2.0%** for its three after-expiry rows —
deliberately different from the **1.0%** they gave its before-expiry rows, so
the differentiation was a choice, not filler — and then made all three
unreachable. Reading S uses every printed cell, and uses one of exactly those
three 2.0% cells for the disputed year.

**The two hypotheses are therefore:**

- **(a)** The Guidance Note's Example 3, contract year 5, should read **2%**;
  the table is right, reading S is right, and one number in one worked example
  is wrong. *Cost: one typo.*
- **(b)** Column B's row axis silently switches to the IGP clock, and the
  printed 2.0% × 3 block of column B can never apply to any contract.
  *Cost: an unstated axis change, plus three printed values that are dead
  letters.*

**(a) is the better-supported hypothesis, and reading R is a negative result
in a positive result's clothes.** The recommendation this evidence supports is
that the repo's refusal is correct and its stated reason should be
strengthened, not that the table should now be carried under reading R.

### 3.2 A plausible account of how the drafters' error arose

Offered as motivation, not as evidence. In Examples 1 and 2, **every**
column-B year is a before-expiry year, so every column-B figure the drafters
wrote is 1%. Six of the fourteen numbers across those two examples are that
same 1%. Example 3's year 5 is the first and only time in the Note that column
B is reached *after* expiry, where the table switches to 2.0%. Carrying the
habitual 1% across is exactly the slip the drafters' own worked sequence would
produce. This also explains why the error survived review: the value looks
right against the two examples above it.

---

## 4. Where the ambiguity lives — what a drafter would need to clarify

**The single load-bearing item is the row header of Table 6.5:**

> **"Years Before or After Surrender Charge (SC) Expiration"**

— specifically, *what that row means for a contract that no longer has a
surrender charge at all.* Two of the three examples renew into a contract with
no SC (1 and 3), and the table has no row for "no surrender charge". Reading S
answers it one way (keep counting from the original expiry — which Example 1's
years 5–7 confirm, at 10%, 7.5%, 3%) and reading R answers it another (switch
to the IGP clock in column B). Example 1 settles it for column A and Example 3
would settle it for column B if its year-5 value were consistent with the rest
of Example 3 — which it is not.

The single sentence to put to the drafters:

> **Guidance Note, Example 3:** "For a contract with an initial 1-year IGP and
> 3-year SC period, then renewing into a 2-year IGP with no SC, the base lapse
> rates in contract years 1 to 6 would be 2.5%, 2.5%, 2.5%, 25%, 1%, 65%."

with the question:

> Example 3's year 4 value of 25% occurs only at `Upon expiry`/column A and
> its year 6 value of 65% occurs only at `2 yrs after expiry`/column C, so the
> example itself places year 5 at `1 yr after expiry`. That row offers 10%,
> 2.0% and 75.0%. Should year 5 read **2%** (column B, `1 yr after expiry`)?
> If 1% is intended, what rule sends a year that is one year after the
> surrender charge expired to a *before-expiry* row, and under that rule when
> does column B's printed `1 yr after expiry` / `2 yrs after expiry` /
> `3 yrs or more after expiry` value of 2.0% ever apply?

The second half is the part that makes the question answerable: it does not
merely report a mismatch, it asks the drafters to name a contract that reaches
the 2.0% block.

**Secondary items, worth raising in the same message but not load-bearing:**

### 4.1 Example 3's contract is under-specified

"an initial 1-year IGP and 3-year SC period, then renewing into a 2-year IGP"
does not say when the 2-year IGP begins. A 1-year IGP expires at the end of
contract year 1, so read literally the 2-year IGP starts in year 2. But the
Note's own years 2 and 3 are 2.5%, which is a column-A value ("IGP ≤ 1 Year"),
so the contract must be renewing into *further 1-year IGPs* through years 2
and 3 and only taking the 2-year IGP at year 4. That intermediate step is
never stated. All three readings of this were tested (2-year IGP starting at
year 2, 4 or 5); none rescues year 5, and the year-2/year-4/year-5 variants
break years 3, nothing, and years 5–6 respectively — so the under-specification
is a drafting wart, not the cause of the discrepancy.

### 4.2 The renewal year is governed by the *expiring* event, and this is never stated

Both axes need this rule and neither header gives it. Example 2's year 4 is
simultaneously `Upon expiry` of the first SC and `3 yrs to expiry` of the
second; the Note's 75% requires the first. Example 3's year 4 is
simultaneously the expiry year of a 1-year IGP (column A) and the first year
of a 2-year IGP (column B); the Note's 25% requires column A. The rule is
consistent — *an expiry event governs the year in which it occurs, on both
axes* — and it is inferable only from the examples. It should be in the text.

### 4.3 Table 6.5's shape differs from 6.4 and 6.6

Seven rows against eleven, and "3 yrs or more" end rows against "5 yrs or
more". Not an ambiguity, but a trap for anyone carrying all three tables
against a shared row scheme.

---

## 5. The current-edition PDF against what the repo recorded

**The edition matches.** Jan. 1, 2026, adoptions through Aug. 13, 2025 —
exactly what `docs/sources/vm22-section-6-prescribed-assumptions.md` states.
Nothing has been restated since the repo read it, so the 2027 edition remains
the next recheck point, as `tests/test_vm22_prescribed.py` anticipates.

Verified against the PDF, all correct:

| repo claim | verdict |
|---|---|
| §6.C prescribes eleven numeric tables, 6.1–6.11 | **correct** — captions confirmed at 22-28, 22-31 (×2), 22-34 (×2), 22-35, 22-36, 22-39, 22-41, 22-44, 22-47 |
| 6.9 = structured settlements standard lives; 6.10, 6.11 = substandard | **correct** — 6.10 is "age rate-ups of 1-20 years", 6.11 "≥21 years" |
| Table 6.1 = $50 / $100 / $75 by contract type | **correct**, verbatim |
| §6.C.2.c's $35 for a contract not administered | **correct** (22-29) |
| "Seven basis points of the projected account value" | **correct** (22-28, §6.C.2.b) |
| `[1.025]` and `[2.5%]` are the section's only bracketed figures | **correct** — the only other bracket in §6 is a formula grouping bracket in the §6.C.8 age-basis Guidance Note |
| Tables 6.4 and 6.6 as carried in `_BASE_LAPSE` | **correct**, cell for cell |
| Table 6.6 is flat across every after-expiry row | **correct** |
| Table 6.4: 3.5% one year before expiry vs 41.5% upon, ages 60–69 | **correct** |
| Table 6.5 is keyed by IGP rather than attained age | **correct** |
| Table 6.5, Guidance Note, three worked examples | **correct**, verbatim |
| Under the straightforward reading, Ex 1 and 2 exact, Ex 3 year 5 = 2.0% vs 1.0% | **correct**, and reading S is *unique* in fitting Examples 1 and 2 |

### 5.1 What contradicts the repo's account

Nothing on the arithmetic. Two corrections, both small, and one framing point
that matters:

**(a) Provenance page range is understated.** The source file records
"Retrieved and machine-read (PyMuPDF) at pages 22-25 to 22-45 of the chapter."
Section 6 runs 22-25 to **22-53**, and §6.C to the end of it; Tables 6.10 and
6.11 begin at 22-44 and 22-47 and continue past 22-45. The range as recorded
does not cover the material the file describes. Cosmetic, but it is the line
that documents what was actually read.

**(b) Nine vs two "not transcribed".** `VM22_PRESCRIBED_2026.text` still says
"Table 6.1 and Table 6.7 are carried; the other nine prescribed tables are
recorded in docs/sources/ and not transcribed." Seven are now carried and four
are not. The module docstring and RFC-067 both have it right; only this one
string is stale. It is a `__fingerprint__` field, so it travels into run
records.

**(c) The framing is weaker than the evidence warrants — this is the
substantive one.** All three of `docs/sources/…`, the `vm22_prescribed.py`
docstring, RFC-067 and the test say:

> "either the reading is wrong in a way the first two examples cannot
> discriminate, or the Guidance Note has an error."

The first disjunct is now closed off in the strong sense and heavily
disfavoured in the weak sense:

- **Strong sense — closed.** No reading whose row axis means "years before or
  after a surrender-charge expiry" can reproduce Example 3, for *any* number
  of surrender-charge events and *any* contract structure. Example 3's own
  year 4 and year 6 each match a unique cell and bracket year 5 at
  `1 yr after expiry`, whose three values are 10%, 2.0% and 75.0%. This needs
  neither Example 1 nor Example 2 — **Example 3 is inconsistent with Table 6.5
  by itself.**
- **Weak sense — disfavoured.** Readings that abandon the single-clock row do
  exist and do fit all three, but every one of them makes column B's printed
  2.0% block unreachable, and the straightforward reading is the *unique*
  reading in the enumerated space that leaves no printed cell dead.

The refusal in `base_lapse_rate` remains right. But the recorded reason
understates what is known: it reads as "we could not settle this", where the
evidence supports "the Guidance Note's Example 3 is internally inconsistent
with the table it illustrates, and the reading is exonerated." That is a
different message to send to the drafters, and a stronger one.

---

## 6. Explicit statement of uncertainty

- **The transcription is not in doubt.** Two independent extraction paths
  agree, and the method reproduces two already-verified tables from the same
  pages. No merged cell was resolved by guesswork; the one footnote is
  placeable.
- **The impossibility result in §2 is not in doubt**, conditional on one
  premise stated plainly: that the row axis means what its header says — years
  measured from a surrender-charge expiry, hence advancing one per contract
  year. Everything follows arithmetically from that plus three uniquely-matched
  cells. If a drafter rejects that premise, §3 is the space that opens up, and
  §3.1 is why it should not be entered.
- **The enumeration is broad but not exhaustive over all conceivable
  readings.** 144 single-clock readings × 3 prose variants, plus 15 clock-per-
  column readings, plus a model-free sweep over cell assignments and a sweep
  over contract structures. The model-free sweeps are the ones that carry the
  negative result, and they assume no column model at all. A reading that
  abandons *both* axes' printed meanings is not covered and is not worth
  covering.
- **§4.2's "expiring event governs its own year" rule is inferred from the
  examples, not transcribed.** It is required by Examples 2 and 3 and
  contradicted by nothing, but the text does not state it. It is flagged as an
  inference wherever used.
- **Not investigated:** whether any NAIC exposure draft, APF or LATF minutes
  restate Example 3. That is the other agent's task, and it could settle
  hypothesis (a) directly. If a prior or subsequent draft shows 2% in Example
  3's year 5, this becomes a closed typo.
