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
from typing import Any, Callable

import numpy as np

try:
    from fastapi import FastAPI, HTTPException, Query, Request, Response
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover - exercised by the extra
    raise ImportError(
        "the REST API needs FastAPI: pip install -e '.[api]'"
    ) from exc

from engine.api.catalogue import (
    InvalidRequestError, UnknownModelError, build_run, builder, catalogue,
)
from engine.api.examples import example as worked_example, unavailable
from engine.api.reports import measure_run
from engine.api.store import RunState, RunStore
from engine.api.ui import UI_FILES, media_type, read_asset
from engine.core.modeldoc import document, graph_is_settled, modelpoint_fields
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
               ui: bool = True) -> "FastAPI":
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
    """
    resolved = catalogue() if models is None else dict(models)
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

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "engine_version": ENGINE_VERSION,
                "models": len(resolved), "runs": len(store)}

    # --- the model catalogue, and PLAN §7's documentation -----------------

    @app.get("/models")
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

    @app.get("/models/{name}")
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

    @app.get("/models/{name}/example")
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

    @app.post("/models/{name}/graph")
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

    @app.get("/models/{name}/documentation", response_class=Response)
    def model_documentation(name: str) -> Response:
        """RFC-030's generated Markdown, served as Markdown."""
        cls = resolved.get(name)
        if cls is None:
            raise HTTPException(404, f"unknown model {name!r}")
        return Response(document(cls).to_markdown(),
                        media_type="text/markdown; charset=utf-8")

    # --- runs -------------------------------------------------------------

    @app.post("/runs", status_code=202)
    async def submit(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, f"body is not JSON: {exc}") from exc
        try:
            # Validate before queueing, so a bad request is a 4xx rather
            # than a run that fails a second later for a reason the client
            # has to poll to discover.
            (build or builder(resolved))(payload)
        except UnknownModelError as exc:
            raise HTTPException(404, str(exc)) from exc
        except InvalidRequestError as exc:
            raise HTTPException(422, str(exc)) from exc
        run = store.submit(payload)
        return run.summary()

    @app.get("/runs")
    def list_runs(state: str | None = Query(default=None)) -> dict:
        try:
            wanted = RunState(state) if state else None
        except ValueError as exc:
            raise HTTPException(
                422, f"state must be one of "
                     f"{[s.value for s in RunState]}") from exc
        return {"runs": [run.summary() for run in store.list(wanted)]}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = store.get(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run {run_id!r}")
        return run.summary()

    @app.get("/runs/{run_id}/results", response_class=response_class)
    def get_results(run_id: str,
                    aggregate: bool = Query(default=False)):
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
        """
        run = store.get(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run {run_id!r}")
        if run.state is RunState.FAILED:
            raise HTTPException(409, run.error or "the run failed")
        if run.state is not RunState.SUCCEEDED:
            # 409 rather than 404: the run exists and the answer does not
            # yet, which is a different thing from a run that never was.
            raise HTTPException(409, f"run {run_id} is {run.state.value}")
        arrays = run.arrays or {}
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
            "outputs": list(arrays),
            "results": _jsonable(arrays),
        }
        # Returned as a rendered Response rather than a dict: FastAPI's own
        # encoder would run first and would turn a non-finite float into
        # null, quietly.
        return response_class(content=payload)

    # --- reporting overlays -----------------------------------------------

    @app.post("/runs/{run_id}/reports/ifrs17", response_class=response_class)
    def ifrs17_report(run_id: str, spec: dict):
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
        run = store.get(run_id)
        if run is None:
            raise HTTPException(404, f"unknown run {run_id!r}")
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

    @app.get("/events")
    async def events(request: Request,
                     timeout: float | None = Query(default=None, gt=0)
                     ) -> StreamingResponse:
        """Server-sent events, one per state change.

        The orchestration half of PLAN §6. An Airflow or Dagster sensor
        watches this instead of polling, and a webhook deployment gets the
        same events through ``on_event``.
        """
        queue = store.subscribe()

        async def stream():
            deadline = None if timeout is None else (
                asyncio.get_event_loop().time() + timeout)
            try:
                for run in store.list():
                    yield f"data: {json.dumps(run.summary())}\n\n"
                while True:
                    if await request.is_disconnected():
                        return
                    while queue:
                        yield f"data: {json.dumps(queue.pop(0))}\n\n"
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
