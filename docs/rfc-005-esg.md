# RFC-005: ESG file adapters, and the mistakes they exist to make loud

Status: **implemented** — `engine/data/esg.py`, `engine/data/scenarios.py`

## Summary

PLAN.md §6 lists ESG scenario formats alongside model-point files as the
integration surface that makes the engine usable next to an existing estate,
and §8 makes it a Phase 2 exit. Until now a scenario set could only be
generated in-process, which is fine for golden tests and useless for a real
valuation, where the scenarios arrive as a file from Moody's, Conning, or an
in-house generator.

Two pieces:

- **Named series on `ScenarioSet`**, because a real ESG file carries equity
  returns, bond returns, a short rate and inflation, not one column.
- **Readers** for the two layouts every vendor's output reduces to, with the
  format-specific traps handled explicitly rather than defaulted past.

## Named series

A scenario set is now a rectangle of `(n_scenarios, horizon)` values per
named series. One series is the **primary** — the one `ret(t)` returns and
the unit-linked templates compound a fund by — and the rest ride along until
a template asks for them.

`ScenarioSet(returns)` still builds a single-series set named `"return"`, so
every template and test that predates this is unchanged. `with_primary(name)`
returns the same set read through a different series, which lets a template
written against `ret(t)` be run on the bond series without touching the
template.

Only the primary is validated as a *return* (above −100%). A credit spread
or a log rate is not compounded by anything here, and inventing a bound for
it would reject legitimate files.

## Two layouts

**Wide** — one row per scenario, one column per period. The usual dump for a
single series, and the one that comes with a vendor metadata block on top
(`skip_lines`) and one or more identifier columns on the left
(`id_columns`).

**Long** — a tidy `(scenario, period, series, value)` table. This is what a
Parquet extract or a warehouse query gives you, and the only layout that
carries several series at once. Scenarios and periods are ordered by their
*values*, not by their position in the file, so a differently-sorted export
reads to the same set.

CSV goes through the standard library; Parquet needs `pyarrow` (the `data`
extra), imported lazily, exactly as model points do. Both Parquet and CSV
long-format files go through one implementation of "is this rectangle
complete", because a second implementation is a second place for it to be
wrong.

## The traps

Parsing an ESG file is easy. Parsing one *correctly* is where the accuracy
pillar gets tested, because every way of getting it wrong produces a
plausible number rather than an error. Each of these is a test in
`tests/test_esg.py` named after the mistake.

### An index is not a return

Many generators publish a cumulative total-return index, not a per-period
return. Feeding an index to a template that compounds it is catastrophic and
silent.

`kind="index"` converts, and converting needs the level at time zero: either
the file carries a period-0 column (`starts_at=0`) or `index_base` says what
it was. With `kind="index"` and neither, the reader **raises**.

There is deliberately no default. A generator publishing on 100.0 and one
publishing on 1.0 produce identical-looking files, and guessing wrong is a
hundredfold error in the first period that survives every downstream check —
`test_the_index_base_only_has_to_be_the_right_scale` pins exactly what that
looks like.

### Column order is period order

The k-th value column is the return earned during projection period `k`,
whatever the header calls it. A file whose first column is a period-0 index
of ones, read as returns, shifts the whole projection by one period and
changes nothing else — the numbers stay plausible.

No parser can tell that from a genuinely deterministic first year, so it is
not an error. `describe()` **reports** it: `constant_first_period` is true
when the first period is identical across every scenario, which is what the
mistake looks like from the outside.

### A file that claims to be risk-neutral should prove it

`martingale_error(scenarios, rate)` discounts each scenario's accumulated
fund and reports, per horizon, how far the mean is from 1 — next to its
Monte Carlo standard error and the ratio of the two.

The error bar is the point. A set of 1,000 scenarios at 16% vol *will* miss
by a percent or so at 25 years, and that is sampling noise rather than a
defect; the same absolute deviation on 1,000,000 scenarios is damning. A
fixed basis-point tolerance cannot tell those apart, so `check_risk_neutral`
is stated in standard errors and its failure message quotes the horizon, the
deviation, the standard error and the ratio.

### A rectangle with a hole is not a scenario set

The long reader requires every (scenario, period) pair for every series and
names the missing ones — up to five, then a count. A duplicate cell raises
rather than letting last-write-wins decide.

## Provenance without polluting identity

Reading a file records `source`: the path, the layout, the kind, and a
BLAKE2b digest of the **bytes that were actually read**.

That is context, not identity. `__fingerprint__` covers the values and the
primary series name, and nothing else — so two sets holding the same numbers
are the same set whatever file they came from, and a run repeated from a
copied file gets the same `run_id`. This is the same split RFC-003 makes
between a run's inputs and the `code_version` recorded beside them, and
`test_the_same_numbers_from_different_files_are_the_same_set` pins it.

## Not in scope

- **Writing incumbent formats.** PLAN §6 is explicit that reading them is
  the migration on-ramp and writing them is never needed. `to_wide_csv`
  writes *our* layout, which is enough to hand a generated set to another
  tool and enough to round-trip the reader against something other than its
  own assumptions.
- **Vendor-specific header parsing.** The layouts are supported; a
  particular vendor's metadata block is `skip_lines` away. Claiming support
  for a proprietary format without a file to test against would be a claim
  this repository has no business making.
- **Correlated multi-economy structure.** Several series load and travel
  together; nothing yet models the dependence between them, because nothing
  yet consumes more than one.
- **Nested stochastic scenario trees.** A Phase 3 concern with its own
  storage question.
