# RFC-043: Who is asking, and the ladder we did not build

Status: **implemented** — `engine/api/auth.py`, `engine/api/app.py`,
`scripts/principals.py`, `tests/test_auth.py`

## Summary

Execution plan §6, item D1, against landscape §5.3 — governance is what
gates real deployment, and every incumbent has RBAC:

> token-based authentication (hashed tokens in a config file; no new runtime
> dependency), four roles … Unauthenticated mode remains the default for
> library/local use — auth activates when a principals file is configured.

`create_app(principals=...)` takes a `Principals`, a path, or a mapping.
Without it nothing changes; with it every route carries a role requirement,
`GET /health` says `"auth": "required"`, and `GET /principals` shows an
admin who can do what.

## The default has to be off, and that is a design constraint rather than a concession

This engine is a library that happens to ship an API. A library has no
principals — the process that imported it is the principal, and it has
already decided. So authentication cannot be something the core learns
about; it has to be a property of the deployment, bolted to the one place
deployments exist, which is `create_app`.

That is why the guard is a route dependency that returns immediately when
`app.state.principals` is `None`, and why the first test in the suite is
that an app built without principals behaves exactly as it did before this
RFC. Everything else here is only allowed to exist because that test passes.

## No role implies another

Four roles: *viewer* reads, *runner* submits, *approver* is RFC-044's, and
*admin* reads the principal list. The obvious next move is a ladder — admin
implies runner implies viewer — and it is the wrong one.

A ladder is a convenience with a failure mode: the first time somebody adds
a rung in the middle, every principal above it silently gains whatever the
new rung grants. Nobody re-reads the file, because the file did not change.
So roles here are a set with no ordering, `Principal.has` requires *every*
role a route asks for, and an operator who needs to submit and read carries
`["viewer", "runner"]` — written down, in the file, where an auditor sees
both.

The consequence is deliberate and slightly inconvenient: an admin cannot
list models. The tests assert that, because a test suite that only checked
the grants would pass against a system with no access control at all.

Two routing judgements follow from the same instinct:

- **Anything that executes client-supplied model code is a runner route**,
  including `POST /models/{name}/graph`. It looks like documentation, and it
  is a three-period trace of a model over assumptions the caller supplied.
  What a route *is* for authorization purposes is what it runs, not what it
  is called.
- **`/health` is the one public route**, because a load balancer has no
  token and an unreachable health check is an outage. With authentication on
  it answers liveness and stops: the model count and run count are
  inventory, and inventory is not something a stranger needs. The demo UI
  stays public too — it is HTML and JavaScript, and every number on the page
  comes from a call the browser must authenticate for itself.

## Tokens are minted, and identity is deployed

`mint_token()` is `secrets.token_urlsafe(32)`; the file stores
`sha256(token)` and the plaintext is printed once, by
`scripts/principals.py`, and stored nowhere. Because the token is
high-entropy random, a plain hash is the correct primitive — a
password-hashing KDF exists to slow down guessing at human-chosen secrets,
and using one here would be theatre that suggested the file could safely
hold something guessable. Authentication compares with
`hmac.compare_digest` against each stored digest rather than looking the
candidate up in a dict: a dict answers a wrong token faster than a right
one, and closing that channel costs nothing at this scale.

There is a route to read the principal list and **none to change it**. The
file is configuration: it arrives through the same change process as the
rest of the deployment, which is the process that already has an audit
trail. An API that could rewrite its own access control is one bug away from
granting itself the roles it likes — and the alternative on offer, an admin
endpoint plus an audit log to watch it, is a strictly larger thing to get
right than not having the endpoint.

The loader refuses everything it does not understand: an unknown role, a
digest that is not a SHA-256, two principals sharing a token, a duplicate
name, an entry with no roles. Each of those would leave somebody believing
an access grant exists that does not, or the reverse. A principals file that
will not load is an API that will not start, which is the correct failure.

## What is next

RFC-044 (D2) gives the approver role something to do: four-eyes approval
bound to an assumption digest rather than to a label. RFC-045 (D3) adds the
append-only audit log, at which point the question "who did that" has an
answer with a chain on it.
