# User guide

*For the actuary. How to run a valuation, read what comes back, and — the
section no incumbent ships — **what this engine refuses to do, and why**.*

For installation and extension see [`developing.md`](developing.md); for how
it is put together, [`architecture.md`](architecture.md).

---

## 1. The model catalogue

Nineteen product templates, discovered by walking `engine.library` rather than
listed — a template is exposed by existing.

| family | templates |
|---|---|
| protection | `TermLife`, `WholeLife`, `Endowment`, `GroupLife`, `CreditLife`, `IncomeProtection` |
| savings & annuity | `FixedAnnuity`, `PayoutAnnuity`, `UnitLinkedGMDB`, `UnitLinkedGMxB`, `UniversalLife`, `FixedIndexedAnnuity`, `VariablePayoutAnnuity`, `WithProfitsEndowment` |
| pensions & longevity | `PensionBuyout`, `LongevitySwap` |
| health & general | `LongTermCare`, `GeneralInsurance` |
| takaful | `FamilyTakaful` |

Every one carries a **worked example** — a complete, runnable request — which
is also what puts it in the evidence pack. `GET /models` lists them;
`GET /models/{name}/example` returns the worked request.

## 2. Assumptions and bases

An assumption set is an object, not a spreadsheet tab. `Assumptions` carries
flat rates for the simple templates; a `ValuationBasis` carries
sex-distinct mortality with improvement scales, yield curves, expense and
commission scales, reinsurance treaties, tax, and multi-state transitions.

Two properties matter more than the field list:

**Assumption sets are fingerprinted.** `POST /assumptions/digest` returns a
content address; `POST /assumptions/diff` says what changed between two. An
approval is keyed to the digest, so a basis cannot be edited out from under a
sign-off.

**Prescribed regulatory data is dated.** VM-22 §6.C's prescribed tables and
Solvency II's delegated parameters are carried as dated objects
(`VM22_PRESCRIBED_2026`, `DELEGATED_2015`/`DELEGATED_2026`) with their own
provenance, because a valuation is performed under a *text* and texts are
amended. A figure the text puts in **square brackets** — NAIC drafting for
"still under discussion" — arrives as `Provisional`, and anything computed
from one says so.

## 3. Running a valuation

```http
POST /runs
{
  "model": "FixedAnnuity",
  "modelpoints": [...],
  "assumptions": {"kind": "scalar", "interest": 0.03, ...},
  "proj_len": 40
}
```

The response carries the results and a **run record**. Through Excel, the
add-in submits the same request; through Python, `engine.core.registry.record_run`
is the same path.

Choosing an executor is usually unnecessary — `auto` picks the vectorized one,
or the stochastic one when scenarios are supplied. `interpreted` forces the
per-policy path (useful for debugging a single policy); `compiled` fuses the
graph into a native loop where it can.

## 4. Reading a run record

```
run_id           fingerprint of the question: model source, assumptions,
                 model points, horizon
results_digest   fingerprint of the answer
executor         which path produced it
shards           the dispatch topology, if it was dispatched
arithmetic       what the workers attested to
```

Two records with the **same `run_id` and different `results_digest`** are a
non-deterministic engine, and the registry treats that as an error rather
than a curiosity.

What is deliberately *not* in `run_id` is as informative as what is. Chunk
size and dispatch topology are excluded because they provably cannot change a
number — so a run split five ways and the same run split eight ways share an
identifier, and the shard tree sits beside it as evidence.

**`created_at` and the git commit are context, not identity**: a run repeated
from the same source at a later time is the same run.

## 5. The evidence pack

`GET /evidence`, or `python scripts/evidence_pack.py --out <dir>`. It reports
**only what CI actually asserts** — every line is generated from a passing
test or a registered digest, never hand-written.

Sections include executor equivalence (which templates are bitwise-identical
across which executors, and which are outside a class *by construction*
rather than in breach), docstring coverage, reconciliations on record, the
audit chain, and benchmarks.

**Read `reproducibility_scope` before quoting a pack digest.** A digest is an
identity **on a machine**, not across machines: `np.exp` and `**` are not
bit-portable between microarchitectures. The pack says so rather than
implying a stronger claim.

## 6. Approvals and audit

`POST /approvals/{digest}` records a sign-off against an assumption digest;
four-eyes is enforced, so the approver cannot be the proposer.
`GET /audit` returns the append-only chain. An approval cannot drift from
what was approved, because it is keyed to the content address rather than to
a name.

---

## 7. What the engine refuses to do

The part worth reading twice. Each of these is a case where returning a
number would have been easy and wrong.

**A prescribed table it has not transcribed.** `fx_factor` refuses a VM-22
Reserving Category whose factor set is not carried, by name, rather than
serving a different category's. A mortality factor from the wrong section is
a plausible number nothing downstream would question.

**VM-22 Table 6.5 entirely.** Its Guidance Note contradicts its own grid: two
of three worked examples reproduce exactly and the third cannot be produced
by *any* cell of the table. 144 parameterised readings, none reproduces it.
`base_lapse_rate` refuses the table and names the reason. The evidence is in
[`sources/vm22-table-6-5-reading.md`](sources/vm22-table-6-5-reading.md),
ready to send to the NAIC.

**A pooled model run one policy at a time.** Every policy would see a pool
consisting of itself — and it would *complete*, 40% wrong, looking entirely
ordinary. `PooledBlockError` names the pooled variables.

**A dispatch across workers whose arithmetic disagrees.** Workers attest what
their floating-point unit does; a mismatch raises before any number exists,
because a reduced answer would be right to fifteen digits and different in
the sixteenth depending on which worker got which shard.

**An assumption argument it cannot band.** VM-22's structured-settlement
tables band contract years two different ways, so `contract_year` is required
for those categories and **refused** for the two that have no such axis —
accepting one would let a caller believe a banding had been applied.

**An improvement scale outside [0, 1), a projection year before the base
year, a negative age, a sex the table is not quoted by.** Each raises rather
than extrapolating.

### And where a wrong reading is tempting, it is computed on purpose

So the gap is reportable rather than hidden: `vm22.floor_outside_reserve`,
`MackResult.quadrature_total`, `takaful.surplus_if_qard_ignored`.

[`findings/`](findings/) collects the sharp edges, each with a script CI runs
— including a Solvency II band boundary that moves capital by a third for an
arbitrarily small change in the book, and an analysis of surplus whose answer
depends on the order you peel the drivers.
