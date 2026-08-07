# RFC-078: The tenant that scopes a name, and the one thing sharing compute leaks

Status: **implemented** — `engine/api/tenancy.py`,
`engine/api/deployment.py`, `engine/api/auth.py`, `engine/api/app.py`,
`engine/api/store.py`, `deploy/`, `tests/test_tenancy.py`,
`tests/test_deploy.py`

## Summary

§9's G1 asks for multi-tenant packaging and states the rule the item turns on:

> **isolation is asserted by tests, not by a policy document** — a tenant-A
> token can never enumerate, read, or collide with tenant-B runs, even when
> both submit the identical fingerprint (the fingerprint stays global and
> content-true; the *visibility* of the run is what tenancy scopes).

That parenthesis is the whole design, and it rules out the obvious
implementation before it is written.

## Ownership is the wrong model

`RunStore.identify` fingerprints the request, so two tenants submitting
byte-identical work **already collide on one `Run`** before any tenancy code
sees them. A run therefore cannot have an owner. It has a *set* of tenants who
may see it, and the second submitter joins the set rather than displacing
anything or receiving a copy.

Getting this wrong is not subtle in hindsight and is very natural in advance:
if visibility were a single owner field, the second tenant would receive a run
id from `POST /runs` that it is then forbidden to `GET`. A test asserts both
tenants can read the shared run, precisely because the failure is
self-inflicted and silent.

Keeping the fingerprint content-true is what lets tenancy sit on top of the
registry at all. A digest that meant something different in each tenant would
be a digest that means nothing, and the provenance story every other item
leans on is built from those digests.

## 404, never 403

A 403 confirms the resource exists. Run ids **are request fingerprints**, so a
tenant who can construct a plausible request could confirm that a competitor
submitted it — a membership oracle over another customer's activity, delivered
by the authorisation layer. Every run-scoped route answers the wrong tenant
with the same 404 an unknown id gets.

The same reasoning removes the tenant list from `/health`. That route
deliberately carries no role requirement, which makes it the one route where
inventory must not appear, and a SaaS platform's customer list is the most
valuable inventory it has. `/health` says `tenancy: enabled` and stops.

## An unclaimed run is invisible, which is the opposite of the natural code

`may_see` returns False for a run no tenant has claimed. The natural
implementation — absent from the ledger means unrestricted — reads as
permissive-by-default, and its consequence is specific: every run that existed
before tenancy was switched on becomes readable by every tenant at once. A
single-tenant deployment still sees everything, so the strict answer cannot be
satisfied by refusing everybody, and there is a test for each direction.

## The refusal that costs a startup

A principals file where *some* entries carry a tenant and others do not has two
defensible readings — absent means its own tenant, or absent means sees
everything — and they differ by exactly the amount that matters. `tenants_in`
refuses rather than choosing, at application startup, so the deployment does
not serve traffic with its separation half-applied.

Tenant names are validated where they are configured rather than where they are
used, because a name becomes a registry prefix and a warehouse partition
directory. `../other` is rejected by the principals loader; sanitising it later
would mean something had already been named after the unsanitised string.

## The leak, stated

Deduplicating compute across tenants is what G1 asks for, and it is not free.
A tenant submitting work another tenant has already run observes it reach a
terminal state sooner, and may observe it already complete. That is a
**cross-tenant liveness oracle**: it reveals that somebody ran that exact
request. It does not reveal who, exposes no results, and can only be asked
about a request the asker can already construct in full.

This RFC does not argue that away. `shared_compute_leak()` returns the sentence
in words, `/health`'s tenancy summary carries it, and a deployment that will
not accept it sets `dedupe_across_tenants=False` — which folds the tenant into
the fingerprint as a **salt**, recomputes, and moves the run ids. The salt
never reaches the *stored* request, so both tenants' records still show exactly
what was submitted; a test asserts that, because a privacy control that
rewrites history to achieve itself has traded one problem for a worse one.

A security posture is worth what its most inconvenient sentence is worth. This
one has a test asserting the sentence still says "sooner", still says "does not
reveal which tenant", and still names the way out — and another asserting the
caveat *disappears* when deduplication is off, because a warning that is
present when it does not apply is a warning that gets ignored when it does.

## The deployment must not come up open

`deploy/` is a Dockerfile, a compose profile and a Helm chart, and the
interesting part of it is one line of behaviour.

`uvicorn engine.api:create_app --factory` was the obvious command and is wrong:
it calls `create_app()` with **no arguments**, so a compose file that carefully
mounts a principals file serves an unauthenticated API on the port the
authenticated one was meant to occupy, with a healthy healthcheck and nothing
in the logs. `engine/api/deployment.py` exists to close that: it is the seam
between a library that takes arguments and a container that is configured by
environment, it **raises** when a named principals file is unreadable rather
than falling back, and it refuses an ambiguous boolean rather than defaulting
it — `ACTUARIAL_UI=maybe` is not `false`.

The shipped `principals.example.json` carries deliberately malformed digests
and a test asserts it *fails* to load. An example file with valid digests is a
set of credentials published in a git repository.

## What the tests can and cannot reach

There is no container runtime in CI, so nothing here boots. What is checked
instead is every place `deploy/` restates something the code already decides —
the port in four places, the entrypoint, the extras (pip only *warns* on an
unknown one), the chart version against `engine.__version__`, and every
`ACTUARIAL_*` variable the manifests set against the ones the factory reads.
Each of those is a second source of truth that fails at 3am rather than in
review.

G1's compose smoke test is written, marked `slow`, and **skipped where Docker
is absent rather than simulated**. A stubbed version would assert that the stub
works, and the thing worth learning — whether the image's base, install,
non-root user and read-only root filesystem actually serve a projection —
cannot be learned without building it.
