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
    InvalidRequestError, UnknownModelError, builder, catalogue,
)
from engine.api.store import RunState, RunStore
from engine.core.modeldoc import document
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
               allow_nan: bool = False) -> "FastAPI":
    """Build the application.

    ``models`` restricts the catalogue; ``build`` replaces the whole
    request-to-engine translation, which is how a deployment supports an
    assumption basis richer than :mod:`engine.api.catalogue` exposes.
    ``on_event`` is the webhook hook — an outbound HTTP call is a dependency
    this package does not take, so the notifier is injected and a deployment
    supplies whatever client it already has.
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
        return {"models": [
            {"name": name, "variables": len(cls.var_names()),
             "pooled": len(cls.pooled_names())}
            for name, cls in resolved.items()
        ]}

    @app.get("/models/{name}")
    def describe_model(name: str) -> dict:
        cls = resolved.get(name)
        if cls is None:
            raise HTTPException(404, f"unknown model {name!r}")
        doc = document(cls)
        return {
            "name": name,
            "doc": cls.__doc__,
            "coverage": doc.coverage,
            "variables": [
                {"name": v.name, "doc": v.doc, "assumption": v.assumption,
                 "pooled": v.pooled, "source": v.source}
                for v in doc.variables
            ],
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
    def get_results(run_id: str):
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
            "outputs": list(arrays),
            "results": _jsonable(arrays),
        }
        # Returned as a rendered Response rather than a dict: FastAPI's own
        # encoder would run first and would turn a non-finite float into
        # null, quietly.
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

    return app
