# RFC-034: Reading someone else's model office, and refusing to guess

Status: **implemented** — `engine/migrate/prophet.py`,
`tests/test_migrate_prophet.py`, `tests/fixtures/prophet/`

## Summary

Execution plan §3, item A2, discharging landscape §5.5:

> `read_modelpoints(path, dialect) -> ModelPoints` … the mapping report
> lists every incumbent field consumed, renamed, or ignored — the same
> "state what a template needs" instinct, applied to ingestion.

A migration begins with somebody else's files. This reads two of them: a
Prophet model point file into `ModelPoint` objects the engine can run, and a
results extract into the `ExternalTable` RFC-033's parity core consumes. The
two halves meet in `tests/test_migrate_prophet.py`, where a `TermLife` run
over the fixture model points reconciles against the fixture results.

## What the reader will not do

A file reader looks like plumbing, and the plumbing is not the risk. Every
interesting decision here is a refusal, and each one has a cost paid in
inconvenience now rather than in a wrong number later.

**It will not rename by resemblance.** `DEFAULT_FIELD_MAP` is twenty-odd
literal renames — `POL_NUMBER` → `id`, `POL_TERM` → `term_years` — onto the
field catalogue read live off the library through RFC-032's
`modelpoint_fields`. A column whose name is *already* an engine field is
consumed unchanged. Everything else is ignored, by name, in the report.

**It will not convert a unit it was not told about.** The fixture carries
`DURATION_IF_M`, months in force; the engine's `duration_in_force` is years.
That pair is exactly the kind a name-similarity matcher gets right-looking
and wrong. It is absent from the default map, so it lands in the mapping
report as *ignored*, where a human can map it in one line —
`read_modelpoints(path, mapping={"DURATION_IF_M": "duration_months"})` — and
own the conversion. A reader that had "helpfully" mapped it would divide by
twelve or fail to, and nobody would be asked.

**It will not skip a line it cannot parse.** A short row names its line
number and both field counts; a bad value names its line *and its column*; an
undefined type code names the code; a repeated column name is refused
outright, because a reconciliation that compared "the" `RESERVE` column could
not say which one. The person holding a 400,000-policy extract needs to know
which row to open.

**It will not invent column names.** A file that reaches its end with no
header raises rather than falling back on positional names — under the wrong
dialect that is exactly what happens (a results extract read with the MPF
dialect has no `!` line), and it is the error you want.

The one place it is deliberately lenient: an integer column containing
`20.0`, which is what an estate that exported through a spreadsheet writes.
That is absorbed; `20.5` in an integer column still raises.

## The dialect is the product

No two Prophet installations write the same file, and the repo holds no
proprietary ones. Faking confidence about the format would be the worst of
both: a reader that claims coverage it cannot demonstrate and forks the
moment a real client's variant arrives.

So the format choices are data. `ProphetDialect` carries the delimiter, the
comment prefix, the header marker, the record-row marker, the type keyword,
the type codes, the missing-value markers and the accepted date formats;
`MPF_DIALECT` matches the layout as publicly documented and `RESULTS_DIALECT`
is the same reader with the prefixes off. A pipe-delimited estate with `@` on
its records is one constructor call, asserted as such in the tests.

What the fixtures prove is the reader's *behaviour*. They do not prove format
coverage, and this RFC does not claim any: the fixtures are hand-authored to
the documented layout, and the first pilot's real variant becomes the second
fixture (execution plan §11's stated mitigation).

## The fixture results are not our own output handed back

The tempting way to build a results fixture is to run the engine and write
the answer to a file. The reconciliation test then passes forever and proves
nothing whatsoever.

Instead `tests/test_migrate_prophet.py` carries `naive_projection` — a term
assurance projected from the product definition in about ten lines, mortality
then lapse on whoever is left — the committed extract is asserted cell for
cell against it (so a stale fixture fails loudly rather than drifting), and
the engine run is then reconciled against the file through the parity core at
`1e-12` relative. Two independent implementations, one document. The
companion test perturbs one cell by one part in ten million and requires the
same reconciliation to fail: a green parity report is only evidence if the
red one is reachable.

## What is next

A3 (MoSes readers) is the same shape and waits on pilot demand. A4 takes a
model-point read from here plus an incumbent variable list and emits a
`Model` skeleton with the mapping stated — the same refusal to guess, applied
to code rather than to data.
