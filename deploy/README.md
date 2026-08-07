# Deploying the engine

RFC-078. Three targets, one image.

| | for | file |
|---|---|---|
| Docker | building the image | `Dockerfile` |
| Compose | a single box | `docker-compose.yml` |
| Helm | Kubernetes | `helm/` |

## Before anything else: the principals file

`principals.example.json` **will not load**. Its token digests are
deliberately malformed, and `tests/test_deploy.py` asserts that they are — an
example file with valid digests is a set of credentials published in a git
repository.

Mint real ones:

```python
from engine.api.auth import mint_token, token_digest

token = mint_token()          # give this to the client, once
print(token_digest(token))    # put this in the file
```

The file stores the digest and never the token. There is no route that can
change it: identity is deployment configuration and arrives through whatever
change process the rest of the deployment uses.

## Tenancy

A `tenant` on each principal turns on RFC-078. It is all or nothing — a file
where some principals carry one and others do not is **refused at startup**,
because "absent means its own tenant" and "absent means sees everything" are
both defensible readings and they differ by exactly the amount that matters.

Two things worth knowing before you enable it:

- Identical work from two tenants is **computed once**. That is the intended
  behaviour and it leaves one signal: a tenant learns that *somebody* has
  already run a request it can construct. Not who, and no results. Set
  `ACTUARIAL_DEDUPE_ACROSS_TENANTS=0` to pay for the recompute instead — it
  moves the run ids.
- `/health` reports whether tenancy is on and never which tenants exist.

## Compose

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Binds `127.0.0.1:8000`, not `0.0.0.0` — single-box means a reverse proxy in
front, and a default that binds a public interface is a default that puts an
unencrypted API on the internet the first time somebody runs it on a cloud VM.

## Helm

```bash
kubectl create secret generic actuarial-principals \
    --from-file=principals.json=./my-principals.json
helm install actuarial ./deploy/helm
```

The principals file is a **Secret**, not a value: `helm get values` prints
values. The deployment annotates itself with the secret's checksum so a
revoked token does not keep working until an unrelated restart.

`replicaCount > 1` requires `persistence.accessMode: ReadWriteMany`, and the
chart **fails the render** otherwise rather than letting two replicas race on
one volume. The registry is content-addressed, so equal digests write equal
bytes — but a partial write is not equal bytes, and that surfaces as a corrupt
artifact rather than a scheduling error.

## Environment

Read by `engine/api/deployment.py`, which is the seam between a library that
takes arguments and a container configured by environment. It **refuses to
start** if `ACTUARIAL_PRINCIPALS` names a file it cannot read, rather than
falling back to no authentication — and refuses an ambiguous boolean rather
than defaulting it.

| variable | default | |
|---|---|---|
| `ACTUARIAL_PRINCIPALS` | none | RFC-043 principals file |
| `ACTUARIAL_REGISTRY` | in-memory | artifact registry root |
| `ACTUARIAL_AUDIT` | none | RFC-045 chained audit log |
| `ACTUARIAL_EVIDENCE` | none | evidence pack root |
| `ACTUARIAL_MAX_WORKERS` | `1` | projection threads |
| `ACTUARIAL_UI` | `1` | serve the HTML page |
| `ACTUARIAL_DEDUPE_ACROSS_TENANTS` | `1` | share compute between tenants |
