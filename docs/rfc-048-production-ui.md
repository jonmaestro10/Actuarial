# RFC-048: The production surface, and the questions a page made the API answer

Status: **implemented** — `engine/core/snapshot.py`, `engine/api/app.py`,
`engine/api/ui/`, `tests/test_api_ui.py`

## Summary

Execution plan §7, item E3:

> Grow `engine/api/ui` from demo to product: a runs list with filter/search
> over the registry; a results explorer (aggregate → variable → model-point
> drill-down); an assumption diff screen (two snapshot digests → semantic
> per-table diff, not a text diff); parity-report and evidence-pack views.
> Same architecture rule as RFC-032: everything on the page is a call to the
> documented REST API.

Four new screens — Runs, the drill-down on Results, Assumptions, Evidence —
and the six endpoints they needed, which the API did not have:

| route | what a screen could not do without it |
|---|---|
| `GET /runs?q=&model=&limit=` | find a run among more than a handful |
| `GET /runs/{id}` (now with `request`) | compare two runs it did not submit |
| `GET /runs/{id}/results?variable=&modelpoint=` | look at one policy |
| `POST /assumptions/diff` | say what changed between two bases |
| `GET /artifacts`, `/artifacts/{id}` | show the reconciliations on record |
| `GET /evidence`, `/evidence/{section}` | show the validation pack |

RFC-032's rule — everything on the page is a documented call, no privileged
channel — means the endpoints are the deliverable as much as the screens
are, and it is why this item is mostly Python. `tests/test_api_ui.py`
extracts every path the script fetches and asserts the API serves it, so a
screen cannot outrun its API without the suite noticing.

## What the field does, and what that says about scope

Landscape §7 was written for this item, and two of its findings changed the
design rather than confirming it.

**§7.3.1 — the platform UI is a run-operations UI.** Every incumbent splits
a thick-client authoring environment from a production layer (Enterprise
Manager, Unify, Workflow Manager, EnterpriseLink, Integrate), and only
SLOPE authors in a browser. So this screen set is deliberately
*operations and inspection*: find a run, look at its numbers, compare two
bases, read the evidence. Authoring stays in Python, in git, in CI — which
is the repo's thesis, and a browser formula-builder would compete with its
own best property.

**§7.3.2 — the real results UX is somebody else's BI tool.** Prophet lands
in SQL Server, AXIS is migrating off Access, Integrate embeds Power BI,
SLOPE ships a Snowflake connector. The industry converged on *land the
numbers in a queryable store and let BI do the looking*, which is E1's
warehouse. So this item does not grow a charting product: the charts are
the two hand-drawn SVG line charts RFC-032 already had, and anyone who
wants a dashboard is pointed at the warehouse. A bespoke BI suite here
would be the one part of the repo competing with something the customer
already owns.

**§7.3.5 — policy-level drill-down is what vendors lead with.** Prophet's
`.rpt` variable groups, AXIS's seriatim output, SLOPE's
drill-to-model-point. The note says to treat seriatim as the demo moment
rather than an option, so the drill-down populates the instant there is a
run to drill into rather than waiting to be found.

## The assumption diff is a join over digests, not a text diff

The interesting half of the item. `fingerprint(assumptions)` already
answers "is this the same basis?" with one bit — it is the bit RFC-044's
approval binds to — and it is useless to somebody who has just been told
the answer is no.

`engine/core/snapshot.py` flattens an assumption set into one row per
component, each carrying **the digest of the subtree beneath it**, walking
the object the same way the fingerprint encoder walks it:
`__fingerprint__()` where an object states what defines it, `vars()` where
it does not. Two things follow, and the module is only correct because of
them:

- the root row's digest **is** the run's `assumptions_digest`, so a
  snapshot cannot describe a basis other than the one it claims to;
- a component that did not change has the **same digest on both sides**, so
  the diff is a join over two row sets and an unchanged mortality basis
  contributes nothing rather than ninety identical rows.

A text diff would report a reordered mapping as a change and a changed rate
as a line number. This reports neither: change `lapse` and the answer is
one row, `dynamic_lapse.base 0.04 → 0.05`.

Two pruning rules, and they point in opposite directions on purpose.
**Deepest wins for a changed value** — `dynamic_lapse.base` beats
`dynamic_lapse`, which beats "the basis changed", because the whole point
is to locate the number that moved. **Shallowest wins for a component that
appeared or vanished** — a new treaty is one event, not eleven new fields.

The walk is bounded (`max_depth`, `max_items`) because a mortality basis is
thousands of rates and a diff nobody can read is not a control. A bounded
report and an incomplete one are different things, so a change located at a
node whose inside was not walked is flagged `summarised`, and the screen
says so: *the component is named; the individual rate is not*.

**The verdict does not come from the change list.** `identical` is the
comparison of the two root digests and nothing else. A bounded walk could
in principle find nothing below its horizon, and a diff that answered "no
changes" for two bases with different digests would be the worst failure
this module could have. The test pins it: at `max_depth=0` the walk finds
nothing, and the answer is still *not identical*, with `unlocated` set — the
report can say "they differ and I cannot tell you where", which is the one
thing it must never be unable to say.

The module lives in `engine/core` — NumPy only — because two surfaces need
it: RFC-047's workbook snapshot sheet and this diff route. `assumption_rows`
in `engine/excel/workbook.py` is now a two-line call into it. One walker, so
the workbook and the page cannot disagree about what a basis contains.

## A selection is not the run, and says so

The drill-down narrows what is sent and nothing else — the numbers are the
run's own, and the test asserts a policy's column equals the corresponding
column of the whole. But `GET /runs/{id}/results` returns
`results_digest`, and that digest covers the **whole** run. A client that
checked a one-policy selection against it would find a mismatch and blame
the engine.

So the response carries `partial`, and the screen says what it means. It is
the same instinct as RFC-047's precision note: the artifact states what it
is not, in the artifact, rather than leaving a reader to find out.

Three refusals for the same reason: a variable the run does not carry is a
422 naming what it does carry; an unknown model point is a 404; and
`modelpoint` together with `aggregate=true` is a 422, because a block total
has no model points left inside it to select — quietly ignoring one of the
two would answer a question nobody asked.

## Digest prefixes, and the URL as a citation

**Search matches a digest by prefix, a model by substring.** A digest is
quoted by its first characters everywhere in this repo, so prefix is how
somebody actually holds one — and a substring match would let a search for
one run turn up another whose digest merely contains those characters in
the middle. The test holds a mid-string needle up and requires no match.

**Every view's state lives in the URL.** Tab, run, variable, model point,
search, and the two sides of a diff. This is worth more here than on most
sites: a run identifier is a fingerprint of its inputs, so a pasted link
cannot rot into showing different numbers the way a link to run #4173 can.
The link is closer to a citation than to a shared session, which is
landscape §7.3.7's point — the incumbents audit reproducibility by
collecting evidence around a run, and here the identifier *is* the evidence;
the job left was to show it.

The citation is applied on `hashchange` as well as on load, and that is not
a detail. Pasting a link into a tab that is already open is a same-document
navigation: the browser changes the URL and reloads nothing. A citation
that only worked in a fresh tab would fail exactly when a reviewer uses it
during a review. (`writeHash` uses `replaceState`, which fires no
`hashchange`, so this cannot feed itself.)

## The evidence pack is served as built, never built here

`GET /evidence` reads a pack directory the deployment points at. It does
not build one, and the refusal when none is configured is a 404 that says
how to build one and why it is not built here: collecting a pack runs the
test suite and every template under both executors. A page that rebuilt it
per view would report whatever the server had time for, which is precisely
the "evidence pack that overclaims" risk the plan's §11 names.

RFC-049's rule carries onto the screen unchanged: a section with nothing to
report is still `available`, so `available: false` means the pack could not
be collected, and the page shows the pack as **incomplete** rather than as
fine. A parent directory holding two packs is a 409 rather than a guess —
picking one would mean the page reports a pack nobody chose, and which one
it picked would depend on the filesystem.

`GET /artifacts` answers with an empty list on a deployment that has
registered none. A 404 there would read as "this server does not do
reconciliations", which is a different and wrong statement — the same
choice, for the same reason, as the evidence pack's empty sections.

## What this does not do

- **No trace panel, no graph explorer, no compare screen, no what-if.**
  Those are `docs/calculation-ux-plan.md`, which is explicitly written on
  top of this item and reserves them. E3's drill-down ends at the number;
  making every number a door into the formula is that plan's §4.1.
- **No build toolchain.** Vanilla JS, hand-rolled SVG, no npm — the
  dependency discipline of §1.4 applied to the front end.
- **No new results view for stochastic runs.** The explorer reads the
  `(t, model point)` grid; a scenario axis needs a screen of its own.
- **No parity report *rendering*.** `GET /artifacts` shows what the
  registry holds — the derivation, the content digest and the verdict.
  The report body itself is RFC-033's Markdown and RFC-047's workbook
  sheet; storing a rendered copy in the API would be a second source for
  something already content-addressed.

## Acceptance

`tests/test_api_ui.py` — 29 tests. The snapshot root equals the run's
assumption digest; a changed scalar is located at the scalar; an unchanged
component contributes nothing; a re-derived identical basis shows no change;
a change inside an unwalked component is marked summarised; the verdict
survives a walk that finds nothing. The routes are exercised for their
grants and their refusals: prefix-not-substring digest search, the
truncation flag, `partial` on a selection, the three drill-down refusals,
the half-stated diff, the empty artifact list, the unconfigured pack, the
incomplete pack, and two packs under one root. The page's own test extracts
every path `app.js` fetches and asserts the API serves it.

The screens were driven in Chromium (Playwright, the pre-installed browser)
across all four views and both citation paths — fresh load and paste into an
open page — with no console or page errors. That is a manual check rather
than a CI one: adding a browser to the test matrix would put a build
toolchain in the tree to test a page whose whole design is not having one.
