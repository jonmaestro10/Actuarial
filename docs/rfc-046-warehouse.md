# RFC-046: The warehouse, and the number a dashboard cannot trace

Status: **implemented** — `engine/data/warehouse.py`,
`tests/test_warehouse.py`

## Summary

Execution plan §7, item E1, completing milestone M3:

> a star schema in partitioned Parquet … The beyond-parity move: every fact
> row carries the run fingerprint, so any number in any downstream dashboard
> traces to a registered, reproducible run.

`Warehouse(root).write_run(result, record)` writes one fact table and three
dimensions. Reading it needs nothing from this repo — DuckDB, Power BI and
Tableau all read the directory directly, and the module docstring carries
the SQL.

## The claim is a join, so the test is a fingerprint

"Every number traces to a run" is the sort of thing every warehouse says and
few can demonstrate. The demonstration here is direct: `Warehouse.array`
rebuilds a `(t, model point)` array out of Parquet, and the test
fingerprints the reconstructed arrays against the run's own
`results_digest` — the value RFC-003's registry recorded when the run
happened.

That passes only if nothing was rounded, reordered, aggregated, or lost to a
float32 column on the way through. It is the difference between a warehouse
whose numbers *are* the run's and one whose numbers merely look like them,
and it is one assertion.

The provenance columns come from the `RunRecord` rather than from the
caller. `write_run` takes the registered form of a run for exactly that
reason: a fact row whose `assumptions_digest` was assembled by whoever
wrote the load script is a fact row with no provenance at all.

## Long, not wide; and a column, not a path

Two layout decisions, each buying the same thing — a schema that does not
move.

**One row per (model point, scenario, t, variable)** rather than a column
per variable. A wide table needs migrating every time a template gains a
variable, so a wide warehouse is one that breaks a dashboard every quarter.
Long costs storage, which is cheap, and buys a schema that never changes,
which is not.

**The run id lives in a column, and the directory is not Hive-style.** The
obvious layout is `fact_cashflow/run_id=<digest>/`, which makes the
partition key a column for free. It also gives `run_id` two sources — the
path and the file — and the first thing pyarrow does when both exist is
refuse to merge them, which is the polite version of the real problem: two
places a value can come from is one place it can be wrong. So the directory
is the bare digest, the fingerprint is a real column in every file, and a
fact file copied out of the tree still knows which run it came from.

Partitioning by run then does the work a load-management system usually
does. Writing a run creates a directory nobody else's data is in; rewriting
one replaces exactly that directory; and re-loading a run therefore replaces
it rather than doubling it — the failure every warehouse load script has had
at least once, asserted here as a test.

## A stochastic run has to say how big it is

A deterministic run of 100k policies over 60 years and 20 variables is 120
million fact rows. The same run with a thousand ESG scenarios is 120
*billion*, and the difference between those two is one keyword argument
nobody typed.

So a three-dimensional result refuses to be written until the caller names
its scenarios — `scenarios="all"` or a list of indices — and the error says
how many there are. The point is not that the big write is wrong; it is that
it should be a decision somebody made on purpose. Deterministic runs write a
null scenario rather than a zero, because scenario 0 of a stochastic run is
a different thing from "there was no scenario axis", and a BI tool cannot
tell those apart from a number.

The variable dimension carries each `@var`'s declared assumption and the
first line of its docstring. It is the cheapest useful thing in the schema:
"is this gross or net" is a question a dashboard author asks constantly, and
the answer already exists in the model.

## What is next

M3 is complete: RBAC (D1), four-eyes approval (D2), the audit log and run
calendar (D3) and this. E2 puts the same provenance stamps on an Excel
workbook, which is where an audit file actually lives.
