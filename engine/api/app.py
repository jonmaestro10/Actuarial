"""PLAN §6's REST API, on FastAPI.

    pip install -e ".[api]"
    uvicorn engine.api:create_app --factory

PLAN.md §6 asks for

    **REST API** for run submission, status, results retrieval;
    webhook/event stream for orchestration tools.

All four are here. The interesting parts are not the routing.

**A projection is not a request.** ``POST /runs`` returns **202 Accepted**
and an identifier; the work happens on a worker thread and the client polls
``GET /runs/{id}`` or watches ``GET /events``. Anything else would mean a
sixty-year run of a hundred thousand policies inside an HTTP timeout.

**The identifier is a fingerprint, not a ticket.** RFC-003's registry
already computes "same question" as a digest of the inputs, so submitting
the same request twice returns the same run and does no second computation.
Two clients that ask the same thing get the same identifier, anywhere.

**Floats must survive the wire.** This is the first place in the repo where
numbers leave the process, and every accuracy claim in it is bitwise. Python's
``json`` writes a float as ``repr``, which is the shortest string that
round-trips, so a result serialised and read back fingerprints identically —
measured in RFC-031, along with what rounding costs: **fifteen decimal places
is not enough**, and only 17 significant digits round-trips. So this module
does not round, and ``GET /runs/{id}/results`` returns the digest alongside
the numbers so a client can check rather than trust.

**NaN does not survive FastAPI, and it does not fail loudly either.** RFC
8259 has no literal for ``NaN`` or ``Infinity``. Starlette's
``JSONResponse`` renders with ``allow_nan=False`` and so refuses them — but
a handler that returns a ``dict`` never reaches it with the float intact,
because FastAPI serialises the return value through its own encoder first
and that encoder turns a non-finite float into **``null``**. The result is
valid JSON, a 200, and a silently different number: a client cannot tell
``null`` from "this projection produced a NaN".

So results are rendered here and returned as a ``Response``, which FastAPI
passes through untouched. That is not a micro-optimisation, it is the only
way the bytes on the wire are the numbers the engine computed. RFC-031
measures it.

**The API serves PLAN §7's documentation.** ``GET /models/{name}`` returns
RFC-030's generated model documentation, so the formula browser is reachable
from the same place the run is submitted.

**And it serves a demonstration of itself.** RFC-032 adds the four things a
caller needs before any of the above is usable without reading the library:
a worked example per template (``GET /models/{name}/example``), the
model-point fields a template requires (on ``GET /models/{name}``), the
dependency graph as data rather than as Markdown (``POST
/models/{name}/graph``), and an IFRS 17 measurement of a completed run
(``POST /runs/{id}/reports/ifrs17``). ``GET /ui`` is a page built on exactly
those endpoints and nothing else — no private access, no second code path,
so anything it shows is something a client can ask for.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import numpy as np

try:
    from fastapi import (
        Depends, FastAPI, HTTPException, Query, Request, Response,
    )
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover - exercised by the extra
    raise ImportError(
        "the REST API needs FastAPI: pip install -e '.[api]'"
    ) from exc

from engine.api.auth import Principals, Role, principal_of, require
from engine.api.catalogue import (
    InvalidRequestError, UnknownModelError, build_assumptions, build_run,
    builder, catalogue,
)
from engine.api.examples import example as worked_example, unavailable
from engine.api.reports import measure_run
from engine.api.store import RunState, RunStore
from engine.api.tenancy import Tenancy, tenant_of, tenants_in
from engine.api.ui import UI_FILES, media_type, read_asset
from engine.core.approvals import (
    ApprovalRegistry, ApprovalRequired, assumptions_digest, check_approved,
)
from engine.core.audit import AuditLog
from engine.core.modeldoc import document, graph_is_settled, modelpoint_fields
from engine.core.registry import ArtifactRegistry
from engine.core.snapshot import diff_snapshots
from engine import __version__ as ENGINE_VERSION


class LenientJSONResponse(JSONResponse):
    """JSON that *permits* ``NaN`` and ``Infinity``.

    The opposite of what a wrapper here would normally be for. Starlette's
    :class:`~starlette.responses.JSONResponse` already renders with
    ``allow_nan=False``, so strictness is the default and needs no help;
    emitting the non-compliant literals is what takes an override.

    Only reachable through ``create_app(allow_nan=True)``, and the output is
    not valid JSON under RFC 8259. It exists because "my client is Python
    and I would rather see the NaN" is a real position, not because it is a
    good idea.
    """

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, allow_nan=True,
                          separators=(",", ":")).encode("utf-8")


def _jsonable(arrays: dict) -> dict:
    """Arrays as nested lists of Python floats.

    ``tolist`` gives Python floats, which ``json`` writes with ``repr`` —
    the shortest string that round-trips to the same float64. No rounding
    anywhere: RFC-031 measured that fifteen decimal places already breaks
    the fingerprint.
    """
    return {name: np.asarray(a).tolist() for name, a in arrays.items()}


def _has_nonfinite(arrays: dict) -> bool:
    return any(not np.all(np.isfinite(np.asarray(a))) for a in arrays.values())


def create_app(models: dict | None = None, *,
               build: Callable[[dict], dict] | None = None,
               max_workers: int = 1,
               on_event: Callable[[dict], None] | None = None,
               allow_nan: bool = False,
               ui: bool = True,
               principals: Any = None,
               approvals: Any = None,
               require_approval: bool = False,
               audit: Any = None,
               artifacts: Any = None,
               evidence: Any = None,
               dedupe_across_tenants: bool = True) -> "FastAPI":
    """Build the application.

    ``models`` restricts the catalogue; ``build`` replaces the whole
    request-to-engine translation, which is how a deployment supports an
    assumption basis richer than :mod:`engine.api.catalogue` exposes.
    ``on_event`` is the webhook hook — an outbound HTTP call is a dependency
    this package does not take, so the notifier is injected and a deployment
    supplies whatever client it already has.

    ``ui`` serves RFC-032's demonstration page at ``/ui``. On by default
    because an API nobody can see is a hard thing to evaluate, and off by
    one argument: a deployment that wants only the machine surface, or that
    does not want an HTML page on an origin it shares with something else,
    says ``ui=False`` and gets a 404 there.

    ``principals`` turns on RFC-043's authentication: a
    :class:`~engine.api.auth.Principals`, a path to a principals file, or a
    mapping in the same shape. ``None`` — the default — leaves the API
    exactly as it was, which is the right default for a library and the
    wrong one for a deployment. ``GET /health`` says which mode it is in.

    ``audit`` is RFC-045's digest-chained log — an
    :class:`~engine.core.audit.AuditLog` or a path to one. Every mutation
    the API performs is recorded in it; reads are not, because a log that
    records everything is a log nobody reads.

    ``artifacts`` is RFC-003's :class:`~engine.core.registry.ArtifactRegistry`
    — the parity reports (RFC-033), workbooks (RFC-047) and packs derived
    from runs. An empty one is created when none is given, so ``GET
    /artifacts`` answers with a list rather than a 404 on a deployment that
    has recorded none. ``evidence`` points at a *built* RFC-049 evidence
    pack directory; ``GET /evidence`` serves it and refuses to build one
    per request, because collecting a pack runs the test suite.

    ``approvals`` is RFC-044's approval log — an
    :class:`~engine.core.approvals.ApprovalRegistry` or a path to one — and
    ``require_approval`` turns on **approved mode**, where a run whose
    assumption digest nobody else has signed for is refused. Approved mode
    without ``principals`` raises: four-eyes over anonymous callers is one
    pair of eyes with extra steps.
    """
    resolved = catalogue() if models is None else dict(models)
    identities = Principals.resolve(principals)
    approvals_path = None
    if isinstance(approvals, ApprovalRegistry) or approvals is None:
        approval_log = approvals
    else:
        approvals_path = Path(approvals)
        approval_log = (ApprovalRegistry.from_json(approvals_path)
                        if approvals_path.is_file() else ApprovalRegistry())
    if require_approval:
        if identities is None:
            raise ValueError(
                "require_approval needs principals: an approval by an "
                "unidentified caller is not a second pair of eyes"
            )
        if approval_log is None:
            approval_log = ApprovalRegistry()
    if isinstance(artifacts, ArtifactRegistry) or artifacts is None:
        artifact_log = artifacts if artifacts is not None else ArtifactRegistry()
    else:
        artifact_path = Path(artifacts)
        artifact_log = (ArtifactRegistry.from_json(artifact_path)
                        if artifact_path.is_file() else ArtifactRegistry())
    evidence_root = Path(evidence) if evidence is not None else None
    audit_path = None
    if isinstance(audit, AuditLog) or audit is None:
        audit_log = audit
    else:
        audit_path = Path(audit)
        audit_log = (AuditLog.from_json(audit_path) if audit_path.is_file()
                     else AuditLog())

    def _record(request: "Request", action: str, subject: str = "",
                **detail) -> None:
        """Append one mutation to the audit log, if there is one.

        The actor is the authenticated principal, or ``anonymous`` on a
        deployment with no principals — which is honest rather than useful,
        and is why RFC-045 says an audit log without RFC-043 records what
        happened but not who did it.
        """
        if audit_log is None:
            return
        who = principal_of(request)
        audit_log.append(who.name if who else "anonymous", action, subject,
                         detail)
        if audit_path is not None:
            audit_log.to_json(audit_path)

    reads = require(Role.VIEWER)
    runs = require(Role.RUNNER)
    approves = require(Role.APPROVER)
    administers = require(Role.ADMIN)
    store = RunStore(build or builder(resolved), max_workers=max_workers,
                     on_event=on_event)
    response_class = LenientJSONResponse if allow_nan else JSONResponse

    @asynccontextmanager
    async def lifespan(_app):  # pragma: no cover - lifecycle
        yield
        store.shutdown(wait=False)

    app = FastAPI(
        lifespan=lifespan,
        title="Actuarial engine",
        version=ENGINE_VERSION,
        description=__doc__,
        default_response_class=response_class,
    )
    app.state.store = store
    app.state.models = resolved
    app.state.principals = identities
    app.state.approvals = approval_log
    app.state.audit = audit_log
    app.state.artifacts = artifact_log
    app.state.evidence = evidence_root

    # RFC-078. Derived from the principals file rather than configured
    # separately: a deployment that has written tenants into its identities
    # has already said what it wants, and a second switch that could disagree
    # with the first is a switch that eventually will. `tenants_in` refuses a
    # partly tenanted file, so this raises at startup rather than serving a
    # deployment whose separation is half there.
    named_tenants = tenants_in(identities)
    scope = Tenancy(dedupe_across_tenants=dedupe_across_tenants)
    app.state.tenancy = scope
    app.state.tenants = named_tenants

    def _tenant(request: Request):
        """The tenant this request speaks for."""
        return tenant_of(principal_of(request))

    def _visible_run(run_id: str, request: Request):
        """Fetch a run this caller is allowed to see, or 404.

        **404 and not 403.** A 403 confirms the run exists, which hands one
        tenant a membership oracle over another's run ids — and run ids are
        request fingerprints, so a tenant who can guess a request could
        confirm a competitor submitted it. Indistinguishable from "no such
        run" is the only answer that says nothing.
        """
        run = store.get(run_id)
        if run is None or not scope.may_see(run_id, _tenant(request)):
            raise HTTPException(404, f"unknown run {run_id!r}")
        return run

    @app.get("/health")
    def health() -> dict:
        """Liveness, and how much of it a stranger is told.

        Deliberately the one route with no role requirement: a load
        balancer has no token and an unreachable health check is an outage.
        With authentication on it answers the liveness question and stops —
        the model and run counts are inventory, and inventory is not
        something an unauthenticated caller needs.
        """
        if identities is not None:
            # RFC-078: whether the deployment is tenanted is said here, and
            # nothing else is. The tenant *names* are inventory in exactly
            # the way the model and run counts are, and a stranger who can
            # enumerate a SaaS platform's customers has learned something
            # worth having.
            return {"status": "ok", "engine_version": ENGINE_VERSION,
                    "auth": "required",
                    "tenancy": "enabled" if named_tenants else "disabled"}
        return {"status": "ok", "engine_version": ENGINE_VERSION,
                "auth": "disabled", "tenancy": "disabled",
                "models": len(resolved), "runs": len(store)}

    @app.get("/principals", dependencies=[Depends(administers)])
    def list_principals(request: Request) -> dict:
        """Who can do what — names and roles, never tokens.

        There is no route to *change* this. The principals file is
        configuration and arrives through the deployment's own change
        process; an API that could rewrite its own access control is one bug
        away from granting itself the roles it likes.
        """
        if identities is None:
            raise HTTPException(
                404, "this deployment has no principals configured"
            )
        you = principal_of(request)
        return {"principals": identities.summary(),
                "you": you.summary() if you else None}

    @app.get("/audit", dependencies=[Depends(administers)])
    def read_audit(limit: int = Query(default=100, ge=1, le=10_000)) -> dict:
        """The chained log of mutations, newest last, verified on the way out.

        ``head`` is the value worth copying somewhere this deployment does
        not control: the chain catches an edited entry, and only a published
        head catches a deleted one.
        """
        if audit_log is None:
            raise HTTPException(404, "this deployment keeps no audit log")
        try:
            verified = audit_log.verify()
            problem = None
        except Exception as exc:
            verified, problem = False, str(exc)
        return {
            "entries": [e.to_dict() for e in audit_log.events[-limit:]],
            "total": len(audit_log),
            "head": audit_log.head,
            "verified": verified,
            "problem": problem,
        }

    # --- the model catalogue, and PLAN §7's documentation -----------------

    @app.get("/models", dependencies=[Depends(reads)])
    def list_models() -> dict:
        """Every template, and whether this deployment can run it.

        ``example`` and ``unavailable`` are the pair that matter: a
        catalogue that offers fourteen models of which five need an
        assumption object the request schema does not carry is a catalogue
        that will fail five ways, and saying which — and why — costs one
        field. See :mod:`engine.api.examples`.
        """
        return {"models": [
            {"name": name, "variables": len(cls.var_names()),
             "pooled": len(cls.pooled_names()),
             "example": worked_example(name) is not None,
             "unavailable": unavailable(name)}
            for name, cls in resolved.items()
        ]}

    @app.get("/models/{name}", dependencies=[Depends(reads)])
    def describe_model(name: str) -> dict:
        cls = resolved.get(name)
        if cls is None:
            raise HTTPException(404, f"unknown model {name!r}")
        doc = document(cls)
        fields = modelpoint_fields(cls)
        return {
            "name": name,
            "doc": cls.__doc__,
            "coverage": doc.coverage,
            # What the model needs from its *data*, which the catalogue
            # cannot say and a caller has to know before writing a single
            # model point. Static, and says so: ``reflective`` means the
            # scan found a read whose name is computed, so ``required`` is
            # a lower bound.
            "modelpoint_fields": {
                "required": list(fields.required),
                "optional": list(fields.optional),
                "reflective": fields.reflective,
            },
            "unavailable": unavailable(name),
            "variables": [
                {"name": v.name, "doc": v.doc, "assumption": v.assumption,
                 "pooled": v.pooled, "source": v.source}
                for v in doc.variables
            ],
        }

    @app.get("/models/{name}/example", dependencies=[Depends(reads)])
    def model_example(name: str) -> dict:
        """A worked ``POST /runs`` body for this template.

        404 rather than an empty body when there is not one, with the
        reason: a template needing a transition matrix or a scenario set
        has no example *here* because the request schema does not reach
        it, and that is worth saying in the response that fails.
        """
        if name not in resolved:
            raise HTTPException(404, f"unknown model {name!r}")
        found = worked_example(name)
        if found is None:
            raise HTTPException(
                404,
                f"no worked example for {name}: "
                f"{unavailable(name) or 'none is carried for this template'}"
            )
        return found

    @app.post("/models/{name}/graph", dependencies=[Depends(runs)])
    def model_graph(name: str, request_body: dict,
                    trace_length: int = Query(default=3, ge=1, le=120),
                    check_settled: bool = Query(default=True)) -> dict:
        """The dependency graph, as data.

        PLAN §7 wants a graph visualizer and RFC-030 built the graph, but
        only ever rendered it — into Mermaid, or into Markdown. This
        returns the edges, so a client can draw it, query it, or walk it.

        A **POST**, and a body, because a graph is not a property of a
        model class: :meth:`engine.core.model.Model.trace` discovers it by
        *running* a short projection, so it needs a model point and an
        assumption basis exactly as a run does. The body is a run request
        and is validated as one — the specimen is the same specimen, which
        is what makes the graph the graph of the run the caller is about to
        submit.

        ``check_settled`` re-traces at four times the length and compares.
        RFC-030's finding is that a three-period trace reports **no
        dependencies at all** for a variable that first reaches back six
        periods, and nothing raises; the answer therefore travels with how
        far it looked and whether looking further changed it.
        """
        cls = resolved.get(name)
        if cls is None:
            raise HTTPException(404, f"unknown model {name!r}")
        spec = dict(request_body or {})
        spec["model"] = name
        try:
            built = build_run(spec, resolved)
        except UnknownModelError as exc:  # pragma: no cover - name is checked
            raise HTTPException(404, str(exc)) from exc
        except InvalidRequestError as exc:
            raise HTTPException(422, str(exc)) from exc
        specimen = built["modelpoints"][0]
        assumptions = built["assumptions"]
        try:
            graph = cls.trace(specimen, assumptions, trace_length)
            settled = (graph_is_settled(cls, specimen, assumptions,
                                        short=trace_length,
                                        long=trace_length * 4)
                       if check_settled else None)
        except Exception as exc:
            # Tracing runs the model, so this is a failed run rather than a
            # bad request — the same distinction the store draws.
            raise HTTPException(
                422, f"tracing {name} failed: {type(exc).__name__}: {exc}"
            ) from exc
        pooled = set(cls.pooled_names())
        return {
            "model": name,
            "trace_length": trace_length,
            "settled": settled,
            "horizon": graph.horizon(),
            "order": list(graph.order()),
            "roots": list(graph.roots()),
            "leaves": list(graph.leaves()),
            "pooled": sorted(pooled),
            "edges": [
                {"from": dep, "to": var, "offset": offset}
                for var in graph.variables
                for dep, offset in sorted(graph.edges[var])
            ],
            "lineage": {
                var: {"inputs_of": list(graph.inputs_of(var)),
                      "affected_by": list(graph.affected_by(var))}
                for var in graph.variables
            },
            "mermaid": graph.to_mermaid(),
        }

    @app.get("/models/{name}/documentation", response_class=Response,
              dependencies=[Depends(reads)])
    def model_documentation(name: str) -> Response:
        """RFC-030's generated Markdown, served as Markdown."""
        cls = resolved.get(name)
        if cls is None:
            raise HTTPException(404, f"unknown model {name!r}")
        return Response(document(cls).to_markdown(),
                        media_type="text/markdown; charset=utf-8")

    # --- assumption approval (RFC-044) ------------------------------------

    def _approval_store() -> "ApprovalRegistry":
        if approval_log is None:
            raise HTTPException(
                404, "this deployment records no approvals"
            )
        return approval_log

    def _save_approvals() -> None:
        if approvals_path is not None:
            approval_log.to_json(approvals_path)

    @app.post("/assumptions/digest", dependencies=[Depends(reads)])
    def assumption_digest(spec: dict) -> dict:
        """The digest an approval would bind to, for a given basis.

        The route that makes the workflow usable: a submitter refused for
        want of an approval needs a string to hand somebody, and an approver
        needs to be able to compute it from the basis they are looking at
        rather than from the run that was refused.
        """
        try:
            assumptions = build_assumptions(spec)
        except InvalidRequestError as exc:
            raise HTTPException(422, str(exc)) from exc
        digest = assumptions_digest(assumptions)
        approvers = (approval_log.approvers(digest)
                     if approval_log is not None else ())
        return {"assumptions_digest": digest, "approvers": list(approvers),
                "approved": bool(approvers)}

    @app.post("/assumptions/diff", dependencies=[Depends(reads)])
    def assumption_diff(body: dict) -> dict:
        """What changed between two assumption sets, by component.

        RFC-048. ``POST {"left": <assumptions spec>, "right": <spec>}``,
        the same spec shape a run request carries, and the answer is a
        per-component list rather than a text diff: a reordered mapping is
        not a change, and a changed rate is located at
        ``dynamic_lapse.base`` rather than at a line number.

        The verdict — ``identical`` — is taken from the two digests rather
        than from the change list, so it is the same bit RFC-044's approval
        check uses. The list explains the verdict; it does not decide it.
        """
        for side in ("left", "right"):
            if side not in body:
                raise HTTPException(
                    422, f"a diff needs {side!r}: an assumptions spec, the "
                         f"same shape a run request carries"
                )
        try:
            left = build_assumptions(body["left"])
            right = build_assumptions(body["right"])
        except InvalidRequestError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(422, f"assumptions not buildable: {exc}") from exc
        return diff_snapshots(left, right).to_dict()

    @app.get("/artifacts", dependencies=[Depends(reads)])
    def list_artifacts(kind: str | None = Query(default=None)) -> dict:
        """Derived artifacts on record: parity reports, workbooks, packs.

        Always answers, with an empty list on a deployment that has
        registered none — the same choice RFC-049's evidence pack makes for
        a section with nothing to report. A 404 here would read as "this
        server does not do reconciliations", which is a different and
        wrong statement.
        """
        rows = [record.to_dict() for record in artifact_log]
        # Every kind on record, not only the kinds that survived the
        # filter: a page that offered "workbook" as a filter option only
        # once a workbook matched the current filter would hide the thing
        # somebody was looking for.
        kinds = sorted({row["kind"] for row in rows})
        if kind:
            rows = [row for row in rows if row["kind"] == kind]
        return {"artifacts": rows, "n_artifacts": len(rows), "kinds": kinds}

    @app.get("/artifacts/{artifact_id}", dependencies=[Depends(reads)])
    def get_artifact(artifact_id: str) -> dict:
        found = artifact_log.find(artifact_id)
        if found is None:
            raise HTTPException(404, f"unknown artifact {artifact_id!r}")
        return found.to_dict()

    def _resolve_pack() -> Path:
        """The pack directory behind ``evidence=``.

        ``scripts/evidence_pack.py --out <dir>`` writes ``<dir>/<pack
        digest>/``, so a deployment naturally points at the parent. Both
        are accepted. What is *not* accepted is a parent holding two packs:
        picking one would mean the page reports a pack nobody chose, and
        which one it picked would depend on the filesystem.
        """
        if evidence_root is None:
            raise HTTPException(
                404,
                "no evidence pack is configured for this deployment. Build "
                "one with `python scripts/evidence_pack.py --out <dir>` and "
                "start the app with evidence=<dir>; it is deliberately not "
                "built per request, because collecting it runs the test "
                "suite."
            )
        if (evidence_root / "manifest.json").is_file():
            return evidence_root
        packs = sorted(child for child in evidence_root.glob("*")
                       if (child / "manifest.json").is_file())
        if len(packs) == 1:
            return packs[0]
        if not packs:
            raise HTTPException(
                404, f"{evidence_root} holds no evidence pack: no "
                     f"manifest.json in it or in any directory under it"
            )
        raise HTTPException(
            409, f"{evidence_root} holds {len(packs)} packs "
                 f"({', '.join(p.name for p in packs)}); point `evidence` at "
                 f"the one this deployment stands behind"
        )

    def _read_pack_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(
                500, f"evidence pack unreadable at {path.name}: {exc}"
            ) from exc

    @app.get("/evidence", dependencies=[Depends(reads)])
    def get_evidence() -> dict:
        """RFC-049's validation evidence pack, as built.

        Served from a pack directory the deployment points at, and **not**
        built on demand: the pack collects the test inventory by running
        pytest and the equivalence attestation by running templates under
        both executors, which is a CI job rather than a request. A page
        that rebuilt it per view would be a page that reported whatever the
        server had time for.

        With no pack configured this is a 404 that says how to make one,
        rather than a thinner pack computed on the spot — an evidence pack
        that quietly reports less than the real one is the failure mode
        RFC-049 exists to prevent.
        """
        pack = _resolve_pack()
        manifest = _read_pack_json(pack / "manifest.json")
        sections = []
        for name, digest in sorted(manifest.get("sections", {}).items()):
            content = _read_pack_json(pack / f"{name}.json")
            sections.append({
                "name": name, "digest": digest,
                # RFC-049's rule: a section with nothing to report is still
                # available. False here means the pack is incomplete.
                "available": bool(content.get("available", True)),
            })
        return {
            "path": str(pack),
            "pack_digest": manifest.get("pack_digest"),
            "code_version": manifest.get("code_version"),
            "machine_specific": manifest.get("machine_specific"),
            "environment_in_digest": manifest.get("environment_in_digest"),
            "complete": all(entry["available"] for entry in sections),
            "sections": sections,
        }

    @app.get("/evidence/{section}", dependencies=[Depends(reads)])
    def get_evidence_section(section: str) -> dict:
        """One section of the pack, in full.

        Separate from the summary because the test inventory alone is every
        test function in the suite by name; a client that wanted the
        headline should not have to download two thousand strings to get
        it.
        """
        pack = _resolve_pack()
        manifest = _read_pack_json(pack / "manifest.json")
        if section not in manifest.get("sections", {}):
            raise HTTPException(
                404, f"the pack has no section {section!r}; it has "
                     f"{sorted(manifest.get('sections', {}))}"
            )
        return {"name": section, "digest": manifest["sections"][section],
                "content": _read_pack_json(pack / f"{section}.json")}

    @app.get("/approvals", dependencies=[Depends(reads)])
    def list_approvals() -> dict:
        log = _approval_store()
        digests = []
        for digest in dict.fromkeys(e.assumptions_digest for e in log):
            digests.append({"assumptions_digest": digest,
                            "approvers": list(log.approvers(digest)),
                            "entries": len(log.history(digest))})
        return {"approvals": digests, "require_approval": require_approval}

    @app.get("/approvals/{digest}", dependencies=[Depends(reads)])
    def approval_status(digest: str) -> dict:
        log = _approval_store()
        return {
            "assumptions_digest": digest,
            "approvers": list(log.approvers(digest)),
            "approved": log.is_approved(digest),
            "history": [entry.to_dict() for entry in log.history(digest)],
        }

    @app.post("/approvals/{digest}", status_code=201,
              dependencies=[Depends(approves)])
    def approve(digest: str, request: Request, body: dict | None = None
                ) -> dict:
        """Sign for a content digest.

        The approver is the authenticated principal and cannot be supplied
        in the body: an approval whose signatory is a request field is an
        approval anyone can forge.
        """
        log = _approval_store()
        who = principal_of(request)
        if who is None:
            raise HTTPException(
                403, "approving needs an authenticated principal"
            )
        entry = log.approve(digest, who.name, (body or {}).get("note", ""))
        _save_approvals()
        _record(request, "assumptions.approve", digest, note=entry.note)
        return entry.to_dict()

    @app.delete("/approvals/{digest}", dependencies=[Depends(approves)])
    def revoke(digest: str, request: Request) -> dict:
        """Withdraw your own approval. Somebody else's is not yours to take."""
        log = _approval_store()
        who = principal_of(request)
        if who is None:
            raise HTTPException(
                403, "revoking needs an authenticated principal"
            )
        if who.name not in log.approvers(digest):
            raise HTTPException(
                404, f"{who.name} has no active approval of {digest}"
            )
        entry = log.revoke(digest, who.name)
        _save_approvals()
        _record(request, "assumptions.revoke", digest)
        return entry.to_dict()

    # --- runs -------------------------------------------------------------

    @app.post("/runs", status_code=202, dependencies=[Depends(runs)])
    async def submit(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, f"body is not JSON: {exc}") from exc
        try:
            # Validate before queueing, so a bad request is a 4xx rather
            # than a run that fails a second later for a reason the client
            # has to poll to discover.
            built = (build or builder(resolved))(payload)
        except UnknownModelError as exc:
            raise HTTPException(404, str(exc)) from exc
        except InvalidRequestError as exc:
            raise HTTPException(422, str(exc)) from exc
        approved_by = None
        if require_approval:
            # RFC-044: the digest checked here is the one the run registry
            # will record, so what was approved and what runs are the same
            # string or this refuses.
            submitter = principal_of(request)
            try:
                check_approved(built["assumptions"],
                               submitter.name if submitter else "",
                               approval_log)
            except ApprovalRequired as exc:
                raise HTTPException(403, str(exc)) from exc
            approved_by = [
                name for name in approval_log.approvers(
                    assumptions_digest(built["assumptions"]))
                if not submitter or name != submitter.name
            ]
        # RFC-078. The tenant joins the run's visibility set *before* the
        # submission is recorded, so there is no window in which the run
        # exists and its submitter cannot see it. With deduplication on, a
        # second tenant submitting identical work joins the same set and
        # shares the computation; with it off, `key` folds the tenant in and
        # the two runs are separate objects with different ids.
        here = _tenant(request)
        run = store.submit(payload, scope.salt(here))
        scope.note(run.run_id, here)
        summary = run.summary()
        if approved_by is not None:
            summary["approved_by"] = approved_by
        _record(request, "run.submit", run.run_id,
                model=payload.get("model"),
                assumptions_digest=assumptions_digest(built["assumptions"]),
                approved_by=approved_by)
        return summary

    @app.get("/runs", dependencies=[Depends(reads)])
    def list_runs(request: Request,
                  state: str | None = Query(default=None),
                  model: str | None = Query(default=None),
                  q: str | None = Query(default=None),
                  limit: int = Query(default=200, ge=1, le=10_000)) -> dict:
        """The runs list, filtered.

        RFC-048. ``q`` searches the four things a run is actually looked up
        by — its fingerprint, its model, and its results and assumption
        digests — by **prefix on the digests and substring on the model**.
        Prefix rather than substring on a digest is deliberate: a digest is
        quoted by its first characters everywhere in this repo, and a
        substring match would let a search for one run find another whose
        digest merely contains those characters in the middle.

        ``n_matched`` is reported alongside a truncated page, so a client
        that hit ``limit`` knows it did.
        """
        try:
            wanted = RunState(state) if state else None
        except ValueError as exc:
            raise HTTPException(
                422, f"state must be one of "
                     f"{[s.value for s in RunState]}") from exc
        # RFC-078: scoped *before* the search filters, not after. Filtering a
        # list the caller should never have held would still be correct here,
        # and would stop being correct the first time a `total` or a facet
        # count is computed from the wrong list.
        rows = [run.summary()
                for run in scope.visible(store.list(wanted), _tenant(request))]
        if model:
            rows = [row for row in rows
                    if model.lower() in str(row.get("model") or "").lower()]
        if q:
            needle = q.strip().lower()
            digests = ("run_id", "results_digest", "assumptions_digest",
                       "modelpoints_digest")

            def hit(row: dict) -> bool:
                if needle in str(row.get("model") or "").lower():
                    return True
                return any(str(row.get(key) or "").lower().startswith(needle)
                           for key in digests)

            rows = [row for row in rows if hit(row)]
        return {"runs": rows[:limit], "n_matched": len(rows),
                "truncated": len(rows) > limit}

    @app.get("/runs/{run_id}", dependencies=[Depends(reads)])
    def get_run(run_id: str, request: Request) -> dict:
        """One run, with the request that produced it.

        The list omits the request and this includes it, which is the whole
        difference between the two routes. RFC-048's assumption-diff screen
        needs the *assumptions* of two runs to compare them, and a client
        that had to keep its own copy of what it submitted would be a
        client that can only diff its own runs.
        """
        run = _visible_run(run_id, request)
        return {**run.summary(), "request": run.request}

    @app.get("/runs/{run_id}/results", response_class=response_class,
             dependencies=[Depends(reads)])
    def get_results(run_id: str,
                    request: Request,
                    aggregate: bool = Query(default=False),
                    variable: str | None = Query(default=None),
                    modelpoint: str | None = Query(default=None)):
        """The run's numbers.

        ``aggregate`` sums each series across the block and is not a
        convenience: the two executors reduce differently — the interpreted
        one with :func:`~engine.core.vector.stable_sum`, the vectorized one
        with NumPy's pairwise reduction — so a client that adds up the
        per-model-point arrays itself gets a number *close to* the engine's
        total rather than equal to it. Anything displaying a block total
        should ask the engine for it.

        The digest is unchanged by the flag and continues to cover the
        per-model-point arrays, because that is what the registry
        fingerprinted. ``aggregated`` says which of the two is in the body,
        so a client cannot check the wrong thing against it.

        RFC-048 adds the two axes a results explorer drills along:
        ``variable`` (a comma-separated subset) and ``modelpoint`` (one
        policy's column). They narrow what is sent and nothing else — the
        numbers are the run's either way — but a production block is a
        hundred thousand policies wide, and a screen that could only ask
        for all of them is a screen nobody can open. ``partial`` says the
        body is a selection, so a client cannot check a subset against a
        digest that covers the whole.
        """
        run = _visible_run(run_id, request)
        if run.state is RunState.FAILED:
            raise HTTPException(409, run.error or "the run failed")
        if run.state is not RunState.SUCCEEDED:
            # 409 rather than 404: the run exists and the answer does not
            # yet, which is a different thing from a run that never was.
            raise HTTPException(409, f"run {run_id} is {run.state.value}")
        arrays = run.arrays or {}
        if variable:
            wanted_names = [name.strip() for name in variable.split(",")
                            if name.strip()]
            missing = [name for name in wanted_names if name not in arrays]
            if missing:
                raise HTTPException(
                    422, f"run {run_id} carries no variable(s) {missing}; it "
                         f"carries {sorted(arrays)}"
                )
            arrays = {name: arrays[name] for name in wanted_names}
        mp_ids = [str(mp_id) for mp_id in
                  (run.result.mp_ids if run.result is not None else ())]
        if modelpoint is not None:
            if aggregate:
                raise HTTPException(
                    422, "aggregate=true sums across model points, so there "
                         "is no model point to select within it"
                )
            if modelpoint not in mp_ids:
                raise HTTPException(
                    404, f"run {run_id} has no model point {modelpoint!r}"
                )
            column = mp_ids.index(modelpoint)
            arrays = {name: np.asarray(values)[:, column]
                      for name, values in arrays.items()}
        if aggregate and run.result is not None:
            arrays = {name: np.asarray(run.result.aggregate(name))
                      for name in arrays}
        if not allow_nan and _has_nonfinite(arrays):
            # Starlette would raise from inside the encoder and the client
            # would get an unexplained 500. Naming it here costs one pass
            # over the arrays and turns it into something actionable.
            raise HTTPException(
                500,
                f"run {run_id} produced a non-finite value; JSON has no "
                "literal for NaN or infinity (RFC 8259), so it is not being "
                "serialised. Start the app with allow_nan=True to send it "
                "anyway."
            )
        payload = {
            "run_id": run_id,
            "results_digest": run.record.results_digest if run.record else None,
            "aggregated": bool(aggregate),
            "modelpoint": modelpoint,
            "modelpoints": mp_ids,
            # The digest covers the whole run; this body may not. Saying so
            # is what stops a client checking a selection against it.
            "partial": bool(variable) or modelpoint is not None,
            "outputs": list(arrays),
            "results": _jsonable(arrays),
        }
        # Returned as a rendered Response rather than a dict: FastAPI's own
        # encoder would run first and would turn a non-finite float into
        # null, quietly.
        return response_class(content=payload)

    # --- reporting overlays -----------------------------------------------

    @app.post("/runs/{run_id}/reports/ifrs17", response_class=response_class,
              dependencies=[Depends(runs)])
    def ifrs17_report(run_id: str, spec: dict, request: Request):
        """Measure a completed run's block as one IFRS 17 group.

        A view of a run rather than a calculator: the request names series
        the run already holds and no cashflow crosses the wire to get here.
        Synchronous, unlike ``POST /runs`` — the projection is the minutes,
        the roll-forward over its aggregates is milliseconds.

        Rendered rather than returned as a dict, for the reason
        ``/runs/{id}/results`` is: FastAPI's encoder would quietly turn a
        non-finite float into ``null``, and a CSM of ``null`` is a worse
        answer than an error.
        """
        run = _visible_run(run_id, request)
        if run.state is RunState.FAILED:
            raise HTTPException(409, run.error or "the run failed")
        if run.state is not RunState.SUCCEEDED:
            raise HTTPException(409, f"run {run_id} is {run.state.value}")
        try:
            payload = measure_run(run, spec)
        except InvalidRequestError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not allow_nan and _has_nonfinite(payload["statement"]):
            raise HTTPException(
                500,
                f"the measurement of run {run_id} produced a non-finite "
                "value; JSON has no literal for NaN or infinity (RFC 8259). "
                "Start the app with allow_nan=True to send it anyway."
            )
        return response_class(content=payload)

    # --- the event stream -------------------------------------------------

    @app.get("/events", dependencies=[Depends(reads)])
    async def events(request: Request,
                     timeout: float | None = Query(default=None, gt=0)
                     ) -> StreamingResponse:
        """Server-sent events, one per state change.

        The orchestration half of PLAN §6. An Airflow or Dagster sensor
        watches this instead of polling, and a webhook deployment gets the
        same events through ``on_event``.
        """
        queue = store.subscribe()
        # RFC-078. Resolved once, here, rather than inside the generator:
        # the stream outlives the request scope and `request.state` is not
        # something to be reading from a coroutine that may still be running
        # after the handler returned.
        here = _tenant(request)

        async def stream():
            deadline = None if timeout is None else (
                asyncio.get_event_loop().time() + timeout)
            try:
                for run in scope.visible(store.list(), here):
                    yield f"data: {json.dumps(run.summary())}\n\n"
                while True:
                    if await request.is_disconnected():
                        return
                    while queue:
                        event = queue.pop(0)
                        # Both halves of the stream are scoped, and the live
                        # half is the one that matters: the backlog can only
                        # leak runs that already existed, while this leaks
                        # every run any tenant submits from now on — a live
                        # feed of another tenant's activity.
                        if not scope.may_see(event.get("run_id", ""), here):
                            continue
                        yield f"data: {json.dumps(event)}\n\n"
                    if deadline is not None and \
                            asyncio.get_event_loop().time() >= deadline:
                        return
                    await asyncio.sleep(0.05)
            finally:
                store.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # --- the demonstration ------------------------------------------------

    if ui:
        @app.get("/ui", response_class=Response)
        @app.get("/ui/", response_class=Response)
        def demo_page() -> Response:
            """RFC-032's page. Everything it shows, it asks for over the
            endpoints above — there is no privileged path into the engine
            from here."""
            return Response(read_asset("index.html"),
                            media_type="text/html; charset=utf-8")

        @app.get("/ui/{asset}", response_class=Response)
        def demo_asset(asset: str) -> Response:
            """The page's two assets, served by name from a fixed set.

            A whitelist and not a directory: the alternative is a static
            mount, and a static mount over a package directory is one path
            traversal away from serving source. Three files do not need a
            filesystem.
            """
            if asset not in UI_FILES:
                raise HTTPException(404, f"unknown asset {asset!r}")
            return Response(read_asset(asset), media_type=media_type(asset))

    return app
