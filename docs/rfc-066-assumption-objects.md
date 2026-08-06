# RFC-066: The one assumption object worth a format

Status: **implemented** — `engine/api/catalogue.py`, `engine/api/examples.py`,
`tests/test_api_demo.py`

## Summary

Not a planned item. It came out of C3 (RFC-041), which added two templates
to the library and could give neither of them a worked example, taking the
count to **eight of sixteen templates unavailable over HTTP** — and
therefore invisible to the evidence pack's specimen set, which walks
`EXAMPLES`.

```json
"assumptions": {
  "kind": "valuation_basis",
  "mortality": {"rates": {"M": {"18": 0.0004, …}, "F": {…}},
                "year_start": 2014,
                "improvement": {"M": {…}, "F": {…}}},
  "curve": {"rates": 0.04, "freq": 1, "horizon_years": 60}
},
"modelpoints": [{"id": "A1", "dob": "1956-01-01", "valuation": "2021-01-01", …}]
```

Eleven specimens now, up from eight. `PayoutAnnuity`, `PensionBuyout` and
`LongevitySwap` are runnable over the API and in the pack.

## Why the original reasoning was right and stopped being right

`engine/api/catalogue.py` has always said why the assumption schema is
small, and the argument was a good one:

> `Assumptions` takes twenty-odd arguments, most of them rich objects — a
> decrement table, a reinsurance treaty, a tax basis, an index-credit rule.
> Exposing all of that over HTTP would mean inventing a serialisation format
> for every one of them, and a format invented here would be wrong the
> moment any of those classes changed.

That holds for twenty-odd moving classes. It stopped holding for one of
them, because `ValuationBasis` is not one of twenty — it is the chassis half
the library has been *growing on*. Every template added to it widened the
same gap, and C3 widened it twice in one commit. A limit that grows with the
codebase is a different thing from a limit that is fixed, and the argument
for tolerating it is weaker every time it bites.

The five that remain — a `TransitionMatrix`, an index-crediting rule, and a
bound scenario set for three templates — keep the original reasoning intact,
and it is now the *whole* of what is out of scope rather than half of it.

## `kind` defaults to `scalar`, and that default is load-bearing

`assumptions` is a discriminated union on `kind`, defaulting to `"scalar"` —
which is what every request written before the other kinds existed already
means.

That is not a convenience. A request that omitted `kind` and quietly got a
different basis than it did last week would be a **silent revaluation**,
which is the one failure this whole layer exists to prevent, and it would be
invisible: the run would succeed, the numbers would move, and nothing in the
response would say why. So the test for it asserts the *fingerprint* of the
built assumptions with and without the key, not merely that both are an
`Assumptions` — a type check would pass against a default that had changed
the tables underneath.

## Dates are coerced at the boundary, not in the core

JSON has no date. `from_dicts` does not coerce one — a string arrives as a
string and the template asks it for a year — and it should not start,
because it is a core function whose other callers pass real `date` objects
and would not thank it for guessing on their behalf.

So `coerce_dates` lives in `build_run`, at the HTTP boundary where the
strings actually come from. It is deliberately narrow: a value matching
`^\d{4}-\d{2}-\d{2}$` **exactly** becomes a `date`, anchored at both ends,
and nothing else is touched. A partial or prefixed match is a string that
resembles a date, and guessing at those is how a coercion rule stops being a
rule — the test asserts that `"born 1956-01-01 in Leeds"` and `"10"` both
survive untouched.

A string in exactly that shape that is *not* a valid date — `2021-13-01` —
is **refused** rather than passed through. Someone who wrote it meant a
date, and silently handing them back a string is the precise failure the
coercion exists to remove.

## What the schema refuses

- **A basis missing half of itself.** A mortality basis and a discount curve,
  and neither stands in for the other; defaulting the missing half would be
  inventing an assumption.
- **A mortality basis with no `year_start`.** A generational basis whose rates
  are not dated means something different every year it is used, and nothing
  downstream would say so.
- **A swap with one leg.** Two legs are two survival schedules and that is the
  whole content of the contract. One leg silently reused as the other gives a
  swap that settles at zero in every period — which looks exactly like a
  working hedge, and is the reason this refusal is worth more than the
  convenience it costs.
- **An unknown `kind`.**

## Acceptance

`tests/test_api_demo.py` — 10 further tests, on top of the existing
per-example ones that now cover eleven templates instead of eight. The three
new specimens parse, run, and supply every field their model requires, which
is checked by the harness that was already there.

The specimen count is asserted, not described: `default_specimens()` returns
eleven. And the `UNAVAILABLE` list is asserted by name, so the reasons cannot
be reworded while the list stays the same length — the failure this item
existed to fix.
