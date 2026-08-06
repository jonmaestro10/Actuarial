# RFC-056: The spreadsheet as a client, and the lie a refresh can tell

Status: **implemented** — `engine/excel/addin.py`,
`tests/test_excel_addin.py`, extra `[excel]`

## Summary

Execution plan §7, item E4, closing milestone M4:

> The tool actuaries will never give up, made a first-class client of the
> API (PLAN §6's "Excel add-in (later)" — now). **Build:**
> `engine/excel/addin.py` on xlwings, behind the same `[excel]` extra:
> submit a run from a sheet (request built from named ranges), poll by
> fingerprint, pull aggregates and model-point drill-downs into sheets.
> Authentication uses D1 tokens; every pulled block is stamped with the run
> fingerprint and assumption digests in adjacent cells, exactly as the E2
> workbooks are — a live spreadsheet that can still prove where its numbers
> came from.

```python
from engine.excel import AddIn, EngineClient, XlwingsBook

addin = AddIn(client=EngineClient("https://engine.internal", token=TOKEN),
              book=XlwingsBook(), sheet="Results", anchor="A1")
addin.refresh()          # submit the sheet, wait, pull it back, stamped
```

Two buttons' worth of behaviour — `submit` and `refresh` — over a client
that speaks only the documented REST API.

## The finding: what a refresh can quietly do

The failure this module is built around is not authentication and not
formatting. It is a **stale stamp beside fresh numbers**, and its worst
form is subtler than that.

Pull a 40-period run into a sheet. Change the horizon, refresh, and the new
10-period block is written over the first rows — while **rows 12 to 42 of
the old run stay exactly where they were**. Under the new run's heading. In
the new run's columns. Below the new run's fingerprint. Nothing looks
wrong: there is no gap, no marker, no second stamp. Everything past the new
block's last row is a different projection, and the cell that says which
run these numbers came from is telling the truth about the top of the
column and a lie about the bottom of it.

That is the exact shape of error this repository exists to make impossible
elsewhere — a number whose provenance is *wrong* rather than *missing* —
and a spreadsheet is where it is easiest to produce and hardest to see.

The response is structural rather than procedural:

1. **A block records its own extent in its own stamp.** The `block` row
   carries `rows x cols`, written after the rectangle is assembled, so it
   is the extent that was actually written and not an estimate of it.
2. **Writing a block clears the extent the previous one recorded**, before
   writing anything. `read_block` reads the old stamp out of the sheet
   first; the clear is driven by what is *in the workbook*, not by what the
   process remembers, so it survives closing Excel, reopening it, and
   refreshing from a different machine.
3. **The stamp and the values are one write.** There is no window in which
   the sheet holds one run's numbers under another run's fingerprint.
4. **An unreadable extent stops.** If the `block` cell has been edited into
   nonsense, `read_block` raises rather than guessing a rectangle to clear
   — a guess is either too small, which leaves the tail, or too big, which
   eats a neighbour's cells.

The suite asserts the tail is gone by reading the sheet, not by trusting
the writer's bookkeeping: after a long pull and then a short one, every
column the long block ever touched is empty below the short block. The same
test exists sideways, for a refresh with fewer variables leaving an orphan
column.

## The add-in cannot compute anything

`engine/excel/addin.py` imports no executor, no template and no assumption
object. It builds a request, POSTs it, polls, and formats what comes back.
So a number this add-in put in a cell came from a registered run, because
there is nowhere else it could have come from — and that is a property, not
a habit, so a test reads the module's own source and fails if it ever
reaches for `engine.core.runner`, `engine.library`, `record_run` or their
neighbours.

It is RFC-032's rule for the demonstration page, applied to the
spreadsheet: no privileged channel, no second code path. The consequence
that matters commercially is that the sheet inherits every guarantee the
API has — idempotency, the fingerprint, the digests, D1's roles — rather
than a subset somebody re-implemented in VBA.

**No new client dependency.** The transport is `urllib` from the standard
library rather than httpx or requests. The add-in runs on an actuary's
laptop next to Excel, and the fewer things that have to be installed there,
the more likely it is to be installed at all. `[excel]` stays openpyxl plus
xlwings. An error body is read and re-raised with the API's own explanation
attached — a client that showed the status code and dropped the detail
would be the least helpful consumer of an API that goes out of its way to
say which variable and which model point.

## Named ranges are a schema, and a missing one says its own name

`engine.model`, `engine.proj_len`, `engine.modelpoints` are required;
`engine.assumptions`, `engine.outputs`, `engine.executor` are optional. A
fixed vocabulary rather than a convention: a name this module does not know
is a name somebody misspelled, and guessing at it is how a run gets
submitted with the wrong horizon.

The ordinary failure here is not exotic — somebody inserted a row and a
range slid — so the message has to name the range to go and look at, rather
than arriving as a 422 from the far end about a field the person at the
keyboard never typed.

Two smaller decisions, both about Excel's one numeric type:

- **A fractional projection length is refused, not narrowed.** `20` arrives
  as `20.0` and must become `20`; narrowing `20.5` the same way would
  submit a horizon nobody typed.
- **Trailing blank rows are dropped; half-filled rows are not.** A named
  range almost always reaches past the data, so blank rows are noise. A row
  with *some* cells filled is a data problem the engine should be allowed
  to complain about, not something this discards on the way past.

Assumption keys are dotted — `mortality.90` — because an assumption spec is
a tree and a spreadsheet is a grid.

## Idempotency is what makes the button cheap

`POST /runs` returns a fingerprint of the request, so refreshing an
unchanged sheet returns the same identifier and does no computation
anywhere. Refresh is therefore safe to press repeatedly, which is what
people do to spreadsheets.

The other half is better: when something *has* changed, the fingerprint
changes, and the stamp beside the numbers is where that shows up. A
spreadsheet's own "has anything moved since I last looked?" becomes a string
comparison against a cell. No incumbent's Excel integration can answer that
question at all, because none of their run identifiers is a function of the
inputs.

`submit` and `refresh` are separate calls because a projection is not a
request (RFC-031) and Excel is frozen while a blocking call blocks. `wait`
takes a timeout, and a timeout is reported as *not finished yet* rather
than as a failure: "refresh again later; the fingerprint is stable, so
nothing is recomputed." Nothing is written to cells on a timeout, on a
failed run, or on a rejected request.

## Testing an Excel add-in without Excel

Everything above the spreadsheet is written against `BookPort` — four
methods: `has_name`, `read_name`, `write`, `read`. `XlwingsBook` is the
only class that has ever heard of a COM object, and its `import xlwings` is
inside `__init__`, so importing the module on a machine with no Excel — every
CI machine this repo has — costs nothing. A subprocess test asserts that
importing the add-in does not import xlwings at all. (Checked in a
subprocess rather than by reloading the module: a reload would mint a second
`Block` class and quietly break identity for every test after it.)

The API side is not mocked. The client's transport is injectable, and the
suite injects one backed by a real `create_app()` instance, so the tests see
the API's real 422s, its real `partial` flag and its real refusals rather
than a fixture's idea of them. `urllib_transport` — the code that actually
runs on the laptop — is exercised separately against a real socket, including
the 404-with-a-body case.

### The manual smoke procedure

The one thing no test here can cover is Excel itself. Run this on a machine
with Excel and `pip install -e ".[excel,api]"`:

1. Start a deployment: `uvicorn engine.api:create_app --factory`.
2. In a new workbook, on a sheet named `Inputs`, define named ranges:
   `engine.model` (one cell: `TermLife`), `engine.proj_len` (one cell:
   `20`), `engine.modelpoints` (a table with a header row — `id`,
   `age_at_entry`, `term_years`, `sum_assured`, `annual_premium`,
   `init_pols`), and `engine.assumptions` (two columns, key and value —
   `interest`, `lapse`, `expense_per_policy`, and `mortality.<age>` rows).
   `GET /models/TermLife/example` gives a worked request to copy from.
3. Add an empty sheet named `Results`.
4. From a Python prompt with the workbook open:

   ```python
   from engine.excel import AddIn, EngineClient, XlwingsBook
   addin = AddIn(client=EngineClient("http://127.0.0.1:8000"),
                 book=XlwingsBook())
   addin.refresh()
   ```

5. Check `Results!A1:B5` carries the stamp and that the numbers below it
   match `GET /runs/{fingerprint}/results?aggregate=true`.
6. **The one that matters:** change `engine.proj_len` from 20 to 5, refresh
   again, and confirm that the rows the longer run occupied are *empty* —
   not left over under the new stamp.

## What this does not do

- **No ribbon, no `.xlam`, no VBA.** xlwings' UDF and ribbon machinery is
  Windows-and-Excel-only and cannot be tested here at all; what ships is
  the Python side, which a deployment wires to a button with xlwings'
  standard `RunPython` one-liner. Shipping an untestable ribbon would be
  shipping the part of this that CI cannot defend.
- **No writing *into* the model.** The add-in reads named ranges and writes
  result blocks. It never edits the input ranges, so a refresh cannot
  change what the next submission asks for.
- **No local caching.** The sheet holds the last pull and the fingerprint
  that produced it; anything else lives in the registry.
- **No stochastic scenario axis**, for the same reason as E3: a scenario
  dimension needs a layout decision, not a default.

## Acceptance

`tests/test_excel_addin.py` — 27 tests, no Excel and no xlwings required.
The request is built from named ranges and every refusal is asserted: a
missing range names itself, a fractional horizon is refused, a header-less
table is refused, an unknown model and an unknown variable are refused
with the API's own message and **nothing is written to cells**. Submission,
polling and the stamped pull go against a real API instance; refreshing an
unchanged sheet returns the same fingerprint, and changing an assumption
changes both the fingerprint and the digest in the cells. The numbers
written are checked against `GET /runs/{id}/results?aggregate=true`.

The two tests that carry the finding: a shorter refresh leaves no tail in
any column the longer block touched, and a narrower one leaves no orphan
column. Both read the sheet rather than the writer's return value.

Milestone M4 (E2 + E3 + E4) is closed.
