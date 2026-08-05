# RFC-031: The REST API, and what happens to a float on the way out

Status: **implemented** — `engine/api/`, `tests/test_api.py`

## Summary

PLAN §6's last unbuilt line:

> **REST API** for run submission, status, results retrieval;
> webhook/event stream for orchestration tools (Airflow/Dagster/Prefect
> operators provided).

On FastAPI, as an optional extra — `pip install -e ".[api]"`, then
`uvicorn engine.api:create_app --factory`. The engine core takes no
dependency on it, the same way `[data]` holds pyarrow.

The routing is not the interesting part. Three things are.

## A projection is not a request

`POST /runs` returns **202 Accepted** and an identifier. The work happens on
a worker thread; the client polls `GET /runs/{id}` or watches `GET /events`.
A hundred thousand model points over sixty years takes minutes, so this is
not a performance choice — it is the only honest shape for the thing being
exposed. Anything synchronous would be a projection inside an HTTP timeout.

`GET /runs/{id}/results` returns **409** while a run is still going, not
404: the run exists and the answer does not yet, which is a different thing
from a run that never was.

## The identifier is a fingerprint, not a ticket

RFC-003 built the run registry so that "same question" is a *computable*
property — `run_id` digests the inputs and `results_digest` digests the
answer. The API gets idempotency by using it rather than inventing a job
table: submitting the same request twice returns the same run and does no
second computation.

That makes the identifier mean something. Two clients that ask the same
question, anywhere, get the same one.

The catch is that it must survive how the request was *written*, not just
what it says. Measured, and both hold:

- key order does not move it — dictionaries fingerprint by sorted key;
- `1` and `1.0` do not move it — integers are canonicalised to floats,
  because JSON clients disagree about which to emit and no model in this
  library distinguishes them.

Booleans are excluded from that canonicalisation. `True` is an `int`
subclass in Python, and folding it to `1.0` would make two different
requests collide.

## The finding: a float does not survive FastAPI intact

This is the first place in the repo where numbers leave the process, and
every accuracy claim in it is bitwise. So the question is whether the bytes
on the wire are the numbers the engine computed.

**Ordinary floats: yes.** Python writes a float as `repr`, the shortest
string that round-trips to the same float64. A result serialised, sent and
parsed back fingerprints **identically** to the arrays the engine produced —
asserted against the registry's own `results_digest`, which is the same
check RFC-003 uses to detect a non-deterministic engine.

**Rounded floats: no, and by more than expected.** Rounding output looks
harmless:

| rounded to | bitwise identical | worst error |
|---|---|---|
| 6 dp | no | 5.0e-07 |
| 10 dp | no | 5.1e-11 |
| 15 dp | **no** | 4.4e-16 |
| 17 significant digits | yes | 0 |

Fifteen decimal places is far more than any actuarial report shows and it
still moves the numbers. Only 17 significant digits round-trips, which is
what `repr` already gives. So this module does not round, anywhere, and
returns the digest alongside the numbers so a client can check rather than
trust.

**Non-finite floats: silently changed.** Starlette's `JSONResponse` renders
with `allow_nan=False` and so refuses `NaN` — RFC 8259 has no literal for
it. But a handler that returns a `dict` never reaches Starlette with the
float intact, because **FastAPI serialises the return value through its own
encoder first, and that encoder writes a non-finite float as `null`**.

The result is valid JSON, a 200, and a different number. Nothing downstream
can tell `null` from "this projection produced a NaN" — and a client that
reads `null` as zero or as missing has lost the one signal that something
broke.

So `GET /runs/{id}/results` renders its own bytes and returns a `Response`,
which FastAPI passes through untouched. That is not a micro-optimisation.
It is the only arrangement in which the wire carries what was computed.

On top of that the handler checks for non-finite values before serialising
and returns a **500 naming the problem**, because the alternative is a
`ValueError` raised from inside the encoder after the handler returned,
which reaches the client as an unexplained 500. A deployment that wants the
non-compliant literals anyway passes `allow_nan=True`, which needs a
response class of its own — the wrapper this module ships is the *lenient*
one, since strictness was never the thing in short supply.

## The API serves PLAN §7's documentation

`GET /models` lists the catalogue, discovered by walking `engine.library`
rather than maintained by hand, so a new template is exposed by existing.
`GET /models/{name}` returns RFC-030's generated documentation as JSON and
`GET /models/{name}/documentation` returns it as Markdown.

PLAN §6's API serving PLAN §7's formula browser, from the same place the run
is submitted — which is the point of having generated it rather than written
it.

## The event stream, and the webhook that is not an HTTP client

`GET /events` is server-sent events, one per state change, ordered
`queued → running → succeeded|failed`. An Airflow or Dagster sensor watches
it instead of polling.

The ordering took a fix worth recording: publishing after handing the run to
the pool let the worker overtake the submission, so `queued` was lost and
`running` was published twice. Events are now published while the store's
lock is held, which costs holding it across the notifier and is stated in
the docstring rather than discovered later.

`?timeout=` bounds the stream so it ends. Without it the generator runs
until the client disconnects, which is right for a sensor and untestable
through a synchronous client — and it is what a proxy does to a long-lived
connection anyway, so bounding it is a deployment feature that happens to
also make the test deterministic.

The **webhook** is an injected callable, not an HTTP client. An outbound
call is a dependency this package does not take, so `create_app(on_event=…)`
receives every event and a deployment supplies whatever client it already
has. Same move RFC-014 made for the risk margin's run-off driver: where the
right answer is somebody else's, take it as an input.

## Why the assumption schema is small

`Assumptions` takes twenty-odd arguments, most of them rich objects — a
decrement table, a reinsurance treaty, a tax basis, an index-credit rule.
Exposing all of it over HTTP means inventing a serialisation format for each
one, and a format invented here would be wrong the moment any of those
classes changed.

So the request carries the scalar basis plus a flat or tabulated mortality
rate, and says so in the error when it is handed anything else. A deployment
needing more passes its own `build` to `create_app`. The same is true of the
catalogue: `create_app(models=…)` restricts what will run at all.

A malformed request is a **422 before anything is queued**, so a client does
not have to poll to discover that it misspelled an output name. A request
that is well-formed and then fails — a model point missing a field the
template reads — is a **failed run**, which is a different thing and is
reported as one.

## Not in scope

- **Arrow results.** PLAN §6 wants `engine.run(...)` to return Arrow tables,
  and results over the wire should follow. JSON is 3.0 MB for a
  2,000-policy, 30-year, three-output run, which is already the wrong
  format at real sizes. The endpoint is where a `format=arrow` belongs, and
  it belongs after the `[data]` extra is a hard dependency of the API rather
  than a separate one.
- **The Airflow/Dagster/Prefect operators** PLAN names. The API they would
  wrap exists now; the operators are three more packages' worth of
  dependency and belong outside this repo.
- **Persistence.** The store is in-process, so runs do not survive a
  restart. The registry already serialises to JSON and Parquet, so the
  missing piece is a store backed by it rather than by a dictionary.
- **Authentication, rate limiting, and multi-tenancy**, all of which are
  deployment concerns and none of which have an actuarial answer.
- **Cancellation.** A queued run cannot be withdrawn, which needs the
  executor to be interruptible and the engine is not.
