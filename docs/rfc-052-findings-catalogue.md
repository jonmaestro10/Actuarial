# RFC-052: A finding with a script beside it

Status: **implemented** — `docs/findings/`, `scripts/findings/`,
`tests/test_findings.py`

## Summary

F4's line, discharged:

> The sharp-edge findings … are currently scattered through RFCs. Collect
> them: `docs/findings/` with one page per finding, each backed by a runnable
> script under `scripts/findings/` asserted in CI — a demonstrable
> audit-and-review capability, per landscape §4.4, and sales collateral that
> is also a regression suite.

Six findings are catalogued. Two named in F4 are not, and the README says so
rather than leaving a reader to notice.

## The distinction the catalogue is built on

A finding that lives only in an RFC is an **assertion**. A finding with a
script beside it that CI runs is a **demonstration**, and the difference is
worth the machinery twice over.

To a reviewer: they can re-run it against the current engine instead of
trusting a paragraph written against an engine eighteen commits ago.

To this repository: a finding whose demonstration stops reproducing has
*changed*, and a changed finding is news whether the change is a fix or a
regression. RFC-028's cliff is a fact about Article 200; if the engine ever
stops reproducing it, either the engine broke or the article was amended, and
both are things to be told about rather than discover.

## Where the claim is asserted, and why not in the script

Each script exposes `FINDING` (slug, claim, source) and `demonstrate()`,
which returns numbers and **asserts nothing**. The claims live in
`tests/test_findings.py`.

That split is the one real design decision here. A script that asserted its
own claim would pass in CI while proving nothing about the engine, because it
would be checking itself — the demonstration and the check would share every
assumption, including the wrong ones. Putting the claim in the test means the
script computes and the test judges, and the two can disagree.

The scripts also print for a human, which is what makes them collateral as
well as tests: `python scripts/findings/counterparty_band_cliff.py` walks a
book across Article 200's boundary in the terminal.

## The correspondence, enforced both ways

`docs/findings/<slug>.md` and `scripts/findings/<slug>.py` are one-to-one,
and both directions fail the build:

- a **page without a script** is an unbacked claim — exactly the state the
  catalogue exists to leave;
- a **script without a page** is a demonstration nobody can read.

Each page is further required to name its own script and link its source RFC,
because a reader who cannot re-run the demonstration is back to trusting
prose. And the slug is asserted against the filename, so a script whose
`FINDING.slug` drifted would satisfy the page check while pointing at
someone else's page.

`CATALOGUED` pins the set. Adding a finding wants a line there; removing one
should be a decision rather than an accident, and the parametrised cases
would otherwise silently stop running over anything — the trap RFC-071 hit
and RFC-072 guarded against, met a third time.

## What the six are

| finding | the sharp edge |
|---|---|
| `counterparty-band-cliff` | Article 200's *lower* boundary moves capital by 14 points of ΣLGD for an arbitrarily small change; the upper one is continuous by construction |
| `pool-of-one` | A pooled model run per policy completes, 40% wrong, looking entirely ordinary |
| `vm22-contract-year-bands` | Two bandings share a boundary at contract year 11, which opens the third band of one table and the second of another |
| `representation-error` | A float basis carries error before any arithmetic; an audit mode converted the wrong way hides it *while looking like success* |
| `reduction-order` | Reductions disagree from twelve elements, with no threshold above which they are safe |
| `aos-ordering` | Every ordering attributes different surplus; quoting one is presenting a choice as a measurement |

Three of these were found in the last two sessions, which is the argument for
building the catalogue now rather than later: the rate at which this
repository produces findings is not falling.

## It caught something while being built

`representation_error.py` failed on its first run, and not in the script.
`as_stored` returned a bare `Decimal` rather than an `Exact`, so values read
that way could not meet the float literals in a `@var` body — `reader=as_stored`
would fail on the first template it touched, while the default reader worked.

`tests/test_exact.py` had a test for exactly that path and it passed, because
it asked only for `v`, whose body never meets a float literal. The catalogue
script asked for the whole projection and did not.

That is the case for demonstrations in one paragraph: the test checked the
feature the way the author was thinking about it, and the demonstration used
it the way a user would.

## Acceptance

`tests/test_findings.py` — 20 tests. Both directions of the correspondence;
every script's metadata and page structure; every demonstration executed
against the current engine; and each of the six claims asserted with the
numbers that make it, not merely that the script ran.

The refusals and floors: the catalogue may not empty out, the slug set is
pinned, a page must name its script and its source, and each claim's
assertion is written so it cannot pass vacuously — the reduction-order test
requires that *some* length above the first disagreement still agrees,
because if none did, a threshold rule would work and the finding would be
wrong.
