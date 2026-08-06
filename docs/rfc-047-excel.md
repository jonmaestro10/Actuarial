# RFC-047: The workbook an audit file is kept in, and what it may not pretend to be

Status: **implemented** — `engine/excel/workbook.py`,
`tests/test_excel_workbook.py`, extra `[excel]`

## Summary

Execution plan §7, item E2, opening milestone M4:

> `engine/excel/workbook.py` behind a `[excel]` extra (openpyxl): a workbook
> writer — run summary, per-variable aggregates, assumption snapshot sheet,
> parity-report sheet (A1) — with the run fingerprint and assumption digests
> stamped on every sheet. The workbook is what audit files actually contain;
> it ships before the live add-in (E4) for that reason.

```python
from engine.excel import write_workbook
written = write_workbook("audit.xlsx", result, record,
                         assumptions=basis, parity=report, detail=["claims"])
```

`record` is a `RunRecord`, for the same reason `Warehouse.write_run` takes
one (RFC-046): provenance a caller assembled by hand is not provenance.
Five sheets at most — run summary, aggregates, assumption snapshot, one
detail sheet per named variable, parity — and the run fingerprint, the
assumption digest and the results digest at the top of every one of them.

The landscape report's §5.4 note is that Excel is still the universal
delivery vehicle: RAFM ships text files plus an Excel reporting add-in, R³S
manages Excel reports end-to-end, Prophet teaches extraction templates,
AXIS and Integrate both meet Excel on the way out. Reaching parity here is
a day's work. The three things below are what the day was actually spent
on.

## The finding: a workbook cannot carry a float64

The plan asked for a workbook writer. What the writer found, and what now
shapes the module, is that a spreadsheet is a **lossy transport**, in three
distinct ways that were measured rather than assumed:

| what | what happens |
|---|---|
| precision | openpyxl serialises a float at **16 significant digits**. A float64 needs 17 to round-trip. `123456789.123456789` and the next representable double above it become the same cell. |
| Excel itself | parses **15** significant digits on load, so the application is a digit worse again than the file. |
| non-finite values | openpyxl writes `NaN` and `±inf` as **empty cells**. A blank in a claims column is read as zero by every human being who has ever opened a spreadsheet. |
| negative zero | `-0.0` is written `-0` and comes back as the integer `0`. |

None of that is openpyxl's fault and none of it can be fixed here. What can
be decided is whether the workbook lies about it, and the module makes four
choices so that it does not:

1. **Non-finite values are written as the text `NaN` / `+Inf` / `-Inf`**,
   never left blank, and the count is reported on the run-summary sheet.
   The text is ugly on purpose — it is meant to stop a reader, not to blend
   in. This is the module's clearest application of the standing preference
   for an explicit refusal over a silently wrong number: a blank cell *is*
   a silently wrong number, and it is the default.
2. **The summary sheet states the precision limit inside the workbook**,
   next to the digests. A limit a reader discovers has already cost
   somebody an afternoon.
3. **`as_written(value)` is public.** It returns the number a float64
   becomes on the way through, so a caller comparing a cell against the
   engine can tell a serialisation artefact from a wrong number instead of
   estimating a tolerance. The tests use it, and they use it as an
   *equality* — the aggregates on the sheet equal the engine's aggregates
   to the format's own limit and no looser. A `pytest.approx` here would
   have hidden exactly the class of error the workbook exists to surface.
4. **The exact record stays elsewhere, and the workbook says where.** The
   bit-exact copy of a run is its registry entry and its Parquet warehouse
   rows (RFC-046), both keyed by the fingerprint stamped on every sheet.
   The workbook points at them. It is the readable artifact, not the
   authoritative one, and it is better for the distinction being written
   down in the file rather than in a manual.

That is a limit stated rather than discovered, and it is why E2 shipping
before E4 (the live add-in) matters: the add-in inherits this stamping and
this posture rather than inventing a looser one under time pressure.

## Every sheet is stamped, because sheets get copied

The plan says "stamped on every sheet"; the reason is worth recording,
because a cheaper implementation is obvious and wrong. A provenance block
on sheet one is provenance for whoever is holding sheet one. What actually
happens to an audit workbook is that somebody copies the aggregates tab
into a board pack, or emails one sheet to an auditor, and the block stays
behind.

So rows 1–3 of *every* sheet carry the run fingerprint, the assumption
digest and the results digest; row 4 names the sheet; row 5 is blank and
the pane freezes below it, so scrolling cannot lose the stamp either.
`read_stamps(path)` reads them back off every tab, which is what makes
"every sheet is stamped" a test rather than a claim — and it raises on a
workbook whose sheet is missing one, so it is a check a reviewer can run
against a file this module did not write.

Two refusals fall out of the same instinct, and both are the interesting
half of the module:

- **A parity report belonging to another run is refused.** If
  `report.results_digest` names results other than the run's, the workbook
  is not written. A reconciliation stapled to results it did not reconcile
  is the one document in the pack a reviewer would be entitled to be angry
  about. A report that names *no* run — a reconciliation of bare arrays —
  is accepted: the refusal is for a mismatch, not for silence.
- **An assumption snapshot of a different basis is refused.** The
  snapshot's root row digests to `fingerprint(assumptions)`, which is the
  value the registry recorded as `assumptions_digest`; if the object handed
  to the writer does not match the run's, there is no workbook. A sheet
  describing a basis the run was not made on is worse than no sheet.

The snapshot is per-row rather than per-set for a reason that only shows up
on the second workbook: each row carries the digest of the subtree beneath
it, so when two workbooks disagree the reader learns *which component*
moved, not merely that the digest did. Subtrees deeper than `max_depth` or
wider than `max_items` collapse to a single row — still carrying their
digest, because a summarised subtree must stay checkable, and a snapshot
sheet nobody can read is not a control.

## What it refuses to write, and what it refuses to recompute

**The aggregates are the engine's, not a `SUM()`.** The obvious
spreadsheet-native design is a detail sheet plus a formula row. It is
wrong: Excel would recompute the total in its own op order and could
disagree with the run in the last bits, leaving the workbook contradicting
the digest printed at the top of the same sheet. The workbook reports the
run; it does not re-derive it. Nothing in a workbook this module writes is
a formula.

**A block that will not fit is refused, not truncated.** An Excel grid is
1,048,576 rows by 16,384 columns. A model-point detail sheet for a
100,000-policy block does not fit, and the alternative to refusing is
16,383 policies under a heading that says "detail". The error names the
warehouse instead. The same applies at Excel's 31-character sheet-name
limit: two variables sharing a long prefix collide after truncation, and
renaming one silently is how a reader ends up reading the wrong variable's
numbers.

**A stochastic run must name its scenario** — an index, or `"mean"` — as
the warehouse makes it name its scenarios. A scenario silently picked is a
number nobody can reproduce.

## The digest is over the content; the bytes are reproducible anyway

`.xlsx` is a zip of XML. Its serialisation is openpyxl's business and its
entry timestamps are the clock's, so `WorkbookWrite.content_digest`
fingerprints the **cells** — what this module decided to write — and that
is what goes into the artifact registry (§1.6) as the content digest,
alongside an `artifact_id` over the run and the sheets requested. It
survives an openpyxl upgrade that reorders an XML attribute, which a digest
of the file would not.

The file bytes are made reproducible regardless: the document properties
are pinned, the zip entry timestamps are pinned to 1980-01-01, and
openpyxl's habit of overwriting `dcterms:modified` at save time is undone
on the way out. Writing the same run twice produces the same file, byte for
byte — asserted in the suite. An audit artifact that changes when nothing
changed trains its reader to ignore the fact that it changed, which is a
bad habit to teach the one person who might have caught something.

The real clock is not lost: the run's `created_at` is on the summary sheet
and in the registry record, where a reader should be looking for it anyway.

## What this does not do

- **No live connection.** Submitting runs from a sheet and pulling results
  by fingerprint is E4 (RFC-056, xlwings), which depends on this module for
  the stamping and formatting and on RFC-043 for auth.
- **No charts, no pivot tables, no conditional formatting.** The workbook
  is an audit file, and every one of those is a view somebody should build
  on top of numbers they can trace rather than something the writer should
  decide for them.
- **No number formats.** Cells are written `General`. A display format is a
  rounding a reader cannot see, and this module has enough rounding it
  cannot help.
- **No `.xls`, no `.xlsb`.** One format, current, openpyxl's.

## Acceptance

`tests/test_excel_workbook.py` — 29 tests. The stamp is on every sheet and
above the frozen pane, and an unstamped workbook is reported rather than
read; the aggregates equal the engine's reduction to `as_written`; the
snapshot's root row equals the run's `assumptions_digest` and a changed
component moves its own rows and not its siblings'; the parity sheet
carries the verdict and names the failing cells. The refusals are asserted
as well as the grants: a parity report for another run, a snapshot of
another basis, a stochastic run with no scenario named, a deterministic run
with one, a variable the run does not carry, a detail block wider than the
Excel grid, two sheet names colliding at 31 characters. The precision loss
is asserted directly, so a future openpyxl that fixes it makes this
document — not the code — the thing that needs correcting. Two writes of
the same run are one artifact in the registry and one file on disk, byte
for byte.

`engine/core`, `engine/data`, `engine/library` and `engine/report` are
untouched and still NumPy-only (§1.4); openpyxl is imported inside
`_openpyxl()` at the module boundary and the suite `importorskip`s it.
