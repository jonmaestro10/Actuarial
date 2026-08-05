# RFC-032: A page for the engine, and what a catalogue owes a caller

Status: **implemented** — `engine/api/examples.py`, `engine/api/reports.py`,
`engine/api/ui/`, `engine/core/modeldoc.py`, `tests/test_api_demo.py`

## Summary

Forty-three merged changes into a projection platform, and the only ways to
see a number were `pytest` and the `print` loops in `scripts/`. PLAN §2.4
puts a UI in Phase 4 and says "not needed for MVP", which is right about the
*platform* UI it describes — run monitoring, assumption diffing,
multi-tenant governance. It is not an argument against a demonstration, and
PLAN §4 makes the case for one in its own words: *marketing = engineering
here*.

So: a page at `/ui`, built on the REST API and nothing else.

The page turned out to be the easy half. Building it meant answering three
questions the API had been able to avoid, and each of them is a gap a
machine client had too.

## What a catalogue owes a caller

`GET /models` walked `engine.library` and offered fourteen templates. Nine
of them could not be run.

Not through any fault in the engine — `IncomeProtection` needs a
`TransitionMatrix`, `FixedIndexedAnnuity` an index-crediting rule, the
unit-linked pair a bound scenario set, the two payout annuities a
`ValuationBasis` and model points carrying dates. Every one of those is an
*object* on `Assumptions`, and RFC-031 decided deliberately that the request
schema carries scalars plus a mortality table. That decision stands. What
did not stand is a catalogue that listed all fourteen and said nothing.

`GET /models` now carries `example` and `unavailable` per template, and a
test submits every unavailable one and requires it to fail. A note claiming
a template needs a transition matrix is worth nothing if the template in
fact runs — that would be the catalogue lying in the other direction.

Of the six with no example, five are assumption-shaped. The sixth is worth
naming separately: `PayoutAnnuity` and `VariablePayoutAnnuity` carry `dob`
and `valuation` as `datetime.date` on the model point. **JSON has no date**,
and `from_dicts` does not coerce one — a string arrives as a string and the
template asks it for a `.year`. Coercing ISO strings in the model-point
loader is a real option and is not taken here: it would change a
repo-wide loader, and every model point flows through the fingerprint.

## What a model needs from its data, and why nothing knew

`ModelPoint` is an open attribute bag with no schema. So "which fields does
this template need" had no answer anywhere in the engine, and a request
missing one failed *inside a projection* with an `AttributeError` naming an
attribute rather than a rejected input.

`engine.core.modeldoc.modelpoint_fields` answers it by **parsing** the
template, and the choice of parsing over running is the whole point. The
dependency graph is discovered by tracing — by running three periods and
watching — which is right for a graph and wrong for this: a field read only
under `if t == 9` is invisible to a three-period trace, and a scan that
missed it would report a model needing less data than it needs. The source
has every branch in it whether or not a specimen took one.

The source also carries required against optional, which is what makes the
answer usable rather than merely present:

```python
self.mp.sum_assured                      # required — nowhere to fall back to
getattr(self.mp, "duration_in_force", 0) # optional — says so in its own
                                         #            third argument
```

Every template in the library uses the second form for its options, so the
split is read off the code rather than curated.

What parsing cannot see is the read whose *name* is computed.
`UnitLinkedGMxB` collects its rider parameters out of `self.mp.__dict__`,
and no static scan can say what is in there. That sets a `reflective` flag,
so the answer says `required` is a lower bound instead of implying it is the
answer. A scan that reported its limit is more useful than one that did not
have one.

## Worked examples, and the test that stops them rotting

Field names are not enough to run anything. A demonstration needs *values* —
that a term assurance is written at 40 for twenty-five years and not at 400
for three — and no amount of reading the source produces those.

`engine/api/examples.py` carries one runnable request per template that this
deployment's builder can reach: eight of the fourteen. They are specimens
and say so. Nothing in them is calibrated and none of it is anybody's
assumption basis.

What makes them worth shipping is that four tests run them:

- every example runs, and every output series is finite;
- every example supplies every field its model requires — two independent
  readings of the same template, one from the source and one written by
  hand, so a field added to a formula and not to the example goes red here
  rather than 500ing in a demonstration;
- every example is unchanged by a JSON round trip;
- every catalogued model is in the examples **or** in the unavailable list,
  so a new template cannot join the API without a decision being made about
  it.

### The finding: a JSON object key is a string, and a run is its request

The round-trip test is not ceremony. The specimen mortality table was
written keyed by integer age, the obvious way to write it in Python. JSON
has no other kind of object key, so it came back keyed by strings — and
because `RunStore.identify` fingerprints the request *as submitted*, the
same example submitted from Python and submitted over HTTP produced **two
different run identifiers for the same question**, and the engine would have
computed it twice.

`build_assumptions` accepts either, which is exactly why nothing failed and
nothing would have. The examples are request bodies, so they are keyed by
string, and the test that would have caught it now exists.

## The overlay is a view of a run, not a calculator

`engine/report/` carries eleven overlays. This exposes one:
`POST /runs/{id}/reports/ifrs17`.

The rest are not harder to write, they are harder to *ask for*. Solvency
II's SCR takes a dictionary of stressed module results, market risk takes an
asset portfolio, embedded value takes a capital basis. None of those is a
projection's output series, so none has a bridge — and exposing them would
mean inventing a serialisation format for a portfolio, which is the trade
RFC-031 declined for assumptions.

IFRS 17 has one. `Group.from_run` reads a projection's own aggregated series,
which means the request names series the run already holds and **no cashflow
crosses the wire to get there**. The measurement inherits the run's identity,
its assumptions and its digest.

The response carries the whole statement plus the one number that says
whether to believe it: total profit against the group's undiscounted net
cash. Accounting decides which periods report the money, not how much of it
there is, so those agree over a run-off. On the worked example the residual
is 1.4e-9 on 3.3e6 — float noise, returned rather than asserted, the same
move as returning the results digest alongside the results.

### The finding: a yield curve and an assumption set disagree about a year

`YieldCurve` defaults to twelve periods a year. `Assumptions` defaults to
one. The CSM accretes at `(1 + rate) ** (1 / curve.freq) - 1` per period.

Build the curve without looking at the run and an annual projection accretes
**a month of interest per year**. Nothing announces it: the roll-forward
still balances, the closing CSM is still exactly zero, the reconciliation to
net cash still holds — and every number in between is wrong. So the curve is
built at the run's own frequency, read from the request the run was
submitted with, and a test asserts it for both.

### Refusing an acquisition cashflow that is not one

`acquisition` accepts `{"series": "initial_expenses"}`, and checks rather
than trusts. A cost the projection puts in period four is not an acquisition
cashflow; summing the series anyway would move that money to period zero and
finance it for four periods it was never outstanding. RFC-012 found exactly
that error inside the module's own unwind, at a cost of `acquisition * i` in
total profit. It is not being reintroduced from the request side.

## Aggregation is not a client's job

`GET /runs/{id}/results` returns one row per period and one column per model
point. A page wants block totals, and adding them up in JavaScript is a
mistake this repo is unusually well placed to notice: the interpreted
executor reduces with `stable_sum` and the vectorized one with NumPy's
pairwise reduction, so a client's own sum is *close to* the engine's rather
than equal to it.

So `?aggregate=true` returns the executor's own totals, and the store keeps
the result object to make that possible. The digest is unchanged by the flag
and goes on covering the per-model-point arrays — which is what the registry
fingerprinted — and `aggregated` says which of the two is in the body, so a
client cannot check the wrong thing against it.

## The graph, as data

PLAN §7 wants a dependency graph visualizer. RFC-001 built the graph and
RFC-030 rendered it — into Mermaid, into Markdown — but never returned it.
`POST /models/{name}/graph` returns the edges, so a client can draw it, walk
it, or query it.

It is a **POST** with a body because a graph is not a property of a model
class: `Model.trace` discovers it by running a short projection, so it needs
a model point and an assumption basis exactly as a run does. The body is a
run request and is validated as one, which is what makes the graph the graph
of the run the caller is about to submit.

The response carries `trace_length` and `settled`, because RFC-030's finding
is that a three-period trace reports *no dependencies at all* for a variable
that first reaches back six periods, and nothing raises. `check_settled`
re-traces at four times the length and compares. Every template in the
library settles at three.

`lineage` carries both transitive directions per variable — what could have
moved this number, and what would move if I changed this formula. Those are
PLAN §1's "full lineage" row and PLAN §7's reviewer, and the graph could
answer both before anything could ask.

## The page

`/ui`, served by the same app, on by default and off with `create_app(ui=False)`.

Four tabs: submit a run and watch its states arrive on the event stream;
chart the aggregates; browse the formulae with the dependency graph and
click a variable for its lineage; measure the block under IFRS 17.

Three constraints, and the reasons they are constraints rather than taste:

**No build step, no package manager, no CDN.** This repository has one
runtime dependency. Adding a JavaScript toolchain to show it off would make
the largest thing in the tree the part that draws the charts, and the honest
comparison it invites is between projection engines. The charts are
hand-drawn SVG in a file you can read, the page works with no route out of
the machine, and `pip install -e ".[api]"` is the whole install. The cost is
real: a few hundred lines of DOM code, and a graph laid out by hand is not a
graph laid out by Graphviz.

**Assets are a whitelist, not a directory.** Three files served by name. A
static mount over a package directory is one path traversal away from
serving source, and three files do not need a filesystem.

**Nothing on the page has a privileged path into the engine.** A test reads
the URLs out of `app.js` and asserts each one is a route this app serves.
A demonstration of an API that reached around it would be a demonstration of
something else.

## Not in scope

- The other ten reporting overlays. They need request formats for portfolios
  and stress dictionaries, which is a different RFC and possibly several.
- Widening the assumption schema to reach the six unavailable templates. The
  escape hatch is unchanged and is the right one: pass your own `build` to
  `create_app`.
- Editing model points in a table rather than as JSON, saving requests,
  comparing two runs, and every other thing PLAN §4's Phase 4 UI describes.
  This is an evaluation surface, not a product.
- Coercing ISO date strings in `from_dicts`, which would let the payout
  annuities through. It changes a loader every model point flows through,
  and through the fingerprint with them.
