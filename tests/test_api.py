"""PLAN §6's REST API — RFC-031.

Skipped whole if FastAPI is not installed, because it is an optional extra:
``pip install -e ".[api]"``. CI installs it, so these run there.

Two layers, as everywhere. The store's behaviour — identity, lifecycle,
idempotency — is tested without HTTP, because it is not an HTTP property.
Then the endpoints, and the thing that only matters once numbers leave the
process: that they arrive unchanged.
"""

import json
import math

import numpy as np
import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.catalogue import (  # noqa: E402
    InvalidRequestError, UnknownModelError, build_assumptions, build_run,
    builder, catalogue,
)
from engine.api.store import RunState, RunStore  # noqa: E402
from engine.core.fingerprint import fingerprint  # noqa: E402
from engine.data.assumptions import Assumptions, MortalityTable  # noqa: E402
from engine.data.modelpoints import from_dicts  # noqa: E402
from engine.library.term_life import TermLife  # noqa: E402

REQUEST = {
    "model": "TermLife",
    "proj_len": 8,
    "outputs": ["claims", "pols_if", "premiums"],
    "assumptions": {"mortality": 0.01, "lapse": 0.04, "interest": 0.025},
    "modelpoints": [
        {"id": "T1", "age_at_entry": 40, "term_years": 20,
         "sum_assured": 250_000.0, "annual_premium": 900.0, "init_pols": 1},
        {"id": "T2", "age_at_entry": 55, "term_years": 15,
         "sum_assured": 100_000.0, "annual_premium": 1_400.0, "init_pols": 3},
    ],
}


@pytest.fixture
def app():
    from engine.api import create_app

    application = create_app(max_workers=1)
    yield application
    application.state.store.shutdown(wait=True)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _finished(app, client, request=REQUEST):
    run_id = client.post("/runs", json=request).json()["run_id"]
    app.state.store.wait(run_id, timeout=120)
    return run_id


# --------------------------------------------------------------------------
# The store, without HTTP
# --------------------------------------------------------------------------

def test_a_run_is_queued_and_finishes():
    store = RunStore(builder())
    run = store.submit(REQUEST)
    assert run.state is RunState.QUEUED or run.state is RunState.RUNNING
    finished = store.wait(run.run_id, timeout=120)
    assert finished.state is RunState.SUCCEEDED
    assert finished.record is not None
    assert set(finished.arrays) == set(REQUEST["outputs"])
    store.shutdown()


def test_the_same_request_gets_the_same_run_and_does_no_second_computation():
    """Idempotency for free, because RFC-003's registry already made "same
    question" a computable property."""
    store = RunStore(builder())
    first = store.submit(REQUEST)
    store.wait(first.run_id, timeout=120)
    second = store.submit(REQUEST)
    assert second is first
    assert len(store) == 1
    store.shutdown()


def test_key_order_and_integer_spelling_do_not_change_the_identifier():
    """The guarantee rests on this. A client that writes ``1`` where another
    writes ``1.0``, or emits its keys in a different order, must land on the
    same run — otherwise "same question, same identifier" is a claim about
    JSON formatting rather than about the question."""
    reordered = {k: REQUEST[k] for k in reversed(list(REQUEST))}
    assert RunStore.identify(reordered) == RunStore.identify(REQUEST)

    integral = json.loads(json.dumps(REQUEST))
    integral["modelpoints"][0]["sum_assured"] = 250_000     # int, not float
    integral["proj_len"] = 8.0                              # float, not int
    assert RunStore.identify(integral) == RunStore.identify(REQUEST)

    # But a different question is a different run.
    changed = json.loads(json.dumps(REQUEST))
    changed["proj_len"] = 9
    assert RunStore.identify(changed) != RunStore.identify(REQUEST)


def test_booleans_are_not_quantised_into_numbers():
    """``True`` is an ``int`` subclass in Python, and canonicalising it to
    ``1.0`` would make two different requests collide."""
    assert RunStore.identify({"x": True}) != RunStore.identify({"x": 1})


def test_a_failed_run_is_a_result_not_a_crash():
    """A model point missing a field the template reads fails at run time,
    not at validation. The store records it and the API reports it."""
    broken = json.loads(json.dumps(REQUEST))
    del broken["modelpoints"][0]["sum_assured"]
    store = RunStore(builder())
    run = store.wait(store.submit(broken).run_id, timeout=120)
    assert run.state is RunState.FAILED
    assert run.error
    assert run.arrays is None
    assert "error" in run.summary()
    store.shutdown()


def test_state_changes_are_published_in_order():
    """``queued`` before ``running`` before the terminal state. Published
    while the lock is held, because otherwise the worker can overtake the
    submission and the queued event is simply lost."""
    events = []
    store = RunStore(builder(), on_event=events.append)
    run = store.submit(REQUEST)
    store.wait(run.run_id, timeout=120)
    assert [e["state"] for e in events] == ["queued", "running", "succeeded"]
    assert {e["run_id"] for e in events} == {run.run_id}
    store.shutdown()


def test_subscribers_receive_the_same_events():
    store = RunStore(builder())
    queue = store.subscribe()
    run = store.submit(REQUEST)
    store.wait(run.run_id, timeout=120)
    assert [e["state"] for e in queue] == ["queued", "running", "succeeded"]
    store.unsubscribe(queue)
    store.submit({**REQUEST, "proj_len": 4})
    assert len(queue) == 3
    store.shutdown()


def test_waiting_on_an_unknown_run_raises():
    store = RunStore(builder())
    with pytest.raises(KeyError):
        store.wait("nope")
    store.shutdown()


# --------------------------------------------------------------------------
# The request surface
# --------------------------------------------------------------------------

def test_the_catalogue_is_discovered_not_listed():
    models = catalogue()
    assert "TermLife" in models and "UnitLinkedGMxB" in models
    assert all(not name.startswith("_") for name in models)
    assert models["TermLife"] is TermLife


def test_mortality_takes_a_number_or_a_table():
    flat = build_assumptions({"mortality": 0.01, "lapse": 0.04})
    assert isinstance(flat, Assumptions)
    assert flat.annual_q(40) == pytest.approx(0.01)
    table = build_assumptions({"mortality": {"40": 0.01, "41": 0.02}})
    assert table.annual_q(41) == pytest.approx(0.02)


@pytest.mark.parametrize("spec, message", [
    ({}, "mortality is required"),
    ({"mortality": "high"}, "must be a number"),
    ({"mortality": 0.01, "wobble": 1}, "unsupported assumption fields"),
    ({"mortality": 0.01, "lapse": 2.0}, "lapse"),
])
def test_a_bad_assumption_basis_is_refused_with_a_reason(spec, message):
    with pytest.raises(InvalidRequestError, match=message):
        build_assumptions(spec)


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r.pop("model"), "model is required"),
    (lambda r: r.update(modelpoints=[]), "non-empty list"),
    (lambda r: r.update(proj_len=0), "positive integer"),
    (lambda r: r.update(proj_len="ten"), "positive integer"),
    (lambda r: r.update(outputs=["not_a_variable"]), "has no variables"),
    (lambda r: r.update(executor="magic"), "executor must be one of"),
])
def test_a_malformed_request_is_refused_before_anything_is_queued(mutate,
                                                                  message):
    request = json.loads(json.dumps(REQUEST))
    mutate(request)
    with pytest.raises(InvalidRequestError, match=message):
        build_run(request)


def test_an_unknown_model_is_its_own_error():
    request = {**REQUEST, "model": "Werewolf"}
    with pytest.raises(UnknownModelError, match="unknown model"):
        build_run(request)


def test_a_deployment_can_restrict_the_catalogue():
    build = builder({"TermLife": TermLife})
    assert build(REQUEST)["model_cls"] is TermLife
    with pytest.raises(UnknownModelError, match=r"carries \['TermLife'\]"):
        build({**REQUEST, "model": "UnitLinkedGMxB"})


# --------------------------------------------------------------------------
# The endpoints
# --------------------------------------------------------------------------

def test_health_and_the_model_catalogue(client):
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["models"] > 10
    models = client.get("/models").json()["models"]
    assert any(m["name"] == "TermLife" and m["variables"] > 20 for m in models)


def test_the_api_serves_the_generated_documentation(client):
    """PLAN §6's API serving PLAN §7's formula browser, from the same place
    the run is submitted."""
    described = client.get("/models/TermLife").json()
    assert described["coverage"] > 0.9
    claims = next(v for v in described["variables"] if v["name"] == "claims")
    assert "Death claims" in claims["doc"]
    assert "def claims" in claims["source"]

    markdown = client.get("/models/TermLife/documentation")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.text.startswith("# TermLife")

    assert client.get("/models/Werewolf").status_code == 404
    assert client.get("/models/Werewolf/documentation").status_code == 404


def test_submission_is_accepted_not_completed(client):
    """202, because a projection is not a request."""
    response = client.post("/runs", json=REQUEST)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] in ("queued", "running")
    assert body["run_id"]
    assert body["n_modelpoints"] == 2


def test_the_lifecycle_through_the_endpoints(app, client):
    run_id = _finished(app, client)
    status = client.get(f"/runs/{run_id}").json()
    assert status["state"] == "succeeded"
    assert status["results_digest"]
    assert status["engine_version"]
    listed = client.get("/runs").json()["runs"]
    assert [r["run_id"] for r in listed] == [run_id]
    assert client.get("/runs?state=succeeded").json()["runs"]
    assert client.get("/runs?state=queued").json()["runs"] == []
    assert client.get("/runs?state=nonsense").status_code == 422


def test_results_are_not_available_before_they_exist(app, client):
    """409 rather than 404: the run exists and the answer does not yet,
    which is a different thing from a run that never was."""
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/results").status_code == 404
    store = app.state.store
    run = store.submit({**REQUEST, "proj_len": 40})
    if run.state is not RunState.SUCCEEDED:
        assert client.get(f"/runs/{run.run_id}/results").status_code == 409
    store.wait(run.run_id, timeout=120)


def test_a_failed_run_reports_the_failure_on_the_results_route(app, client):
    broken = json.loads(json.dumps(REQUEST))
    del broken["modelpoints"][0]["sum_assured"]
    run_id = _finished(app, client, broken)
    assert client.get(f"/runs/{run_id}").json()["state"] == "failed"
    response = client.get(f"/runs/{run_id}/results")
    assert response.status_code == 409
    # The model points no longer agree on their fields, which the batch
    # refuses — a run-time failure, not a malformed request.
    assert "do not match first model point" in response.json()["detail"]


def test_a_bad_request_is_rejected_before_a_run_is_created(client):
    assert client.post("/runs", json={**REQUEST, "proj_len": 0}
                       ).status_code == 422
    assert client.post("/runs", json={**REQUEST, "model": "Werewolf"}
                       ).status_code == 404
    assert client.post("/runs", content=b"{not json",
                       headers={"content-type": "application/json"}
                       ).status_code == 400
    assert client.get("/runs").json()["runs"] == []


def test_resubmission_returns_the_same_run_over_http(app, client):
    first = client.post("/runs", json=REQUEST).json()["run_id"]
    reordered = {k: REQUEST[k] for k in reversed(list(REQUEST))}
    second = client.post("/runs", json=reordered).json()["run_id"]
    assert first == second
    assert len(client.get("/runs").json()["runs"]) == 1
    app.state.store.wait(first, timeout=120)


# --------------------------------------------------------------------------
# The finding: numbers have to survive the wire
# --------------------------------------------------------------------------

def test_results_round_trip_bitwise_through_json(app, client):
    """The API is the first place in this repo where numbers leave the
    process, and every accuracy claim in it is bitwise.

    Python writes a float as ``repr`` — the shortest string that round-trips
    to the same float64 — so a result serialised, sent, and parsed back
    fingerprints **identically** to the arrays the engine produced. Asserted
    against the registry's own ``results_digest``, so the check is the same
    one RFC-003 uses to detect a non-deterministic engine.
    """
    run_id = _finished(app, client)
    payload = client.get(f"/runs/{run_id}/results").json()
    received = {name: np.asarray(values, dtype=np.float64)
                for name, values in payload["results"].items()}
    computed = app.state.store.get(run_id).arrays

    assert all(np.array_equal(computed[name], received[name])
               for name in computed)
    assert fingerprint(received) == fingerprint(computed)
    assert payload["results_digest"] == fingerprint(computed)


def test_rounding_to_fifteen_decimal_places_is_not_enough(app, client):
    """What the fidelity is worth, measured rather than asserted.

    Rounding looks harmless and is not. Fifteen decimal places — far more
    than any actuarial report shows — still moves the numbers, and only 17
    significant digits round-trips. An API that tidied its output would
    break every bitwise claim downstream of it and would look fine doing so.
    """
    run_id = _finished(app, client)
    computed = app.state.store.get(run_id).arrays
    biggest = max(float(np.abs(a).max()) for a in computed.values())
    assert biggest > 1_000.0  # so the decimal places bite

    survived = {}
    for places in (6, 10, 15):
        rounded = {name: np.round(a, places) for name, a in computed.items()}
        survived[places] = all(np.array_equal(computed[n], rounded[n])
                               for n in computed)
    assert survived == {6: False, 10: False, 15: False}

    # Seventeen significant digits does round-trip, which is the standard
    # float64 guarantee and what repr already gives.
    text = {n: [f"{x:.17g}" for x in np.asarray(a).ravel()]
            for n, a in computed.items()}
    back = {n: np.asarray([float(x) for x in v], dtype=np.float64).reshape(
        computed[n].shape) for n, v in text.items()}
    assert all(np.array_equal(computed[n], back[n]) for n in computed)


def test_the_framework_is_already_strict_about_nan():
    """Set out to write a strict response class and found one was not
    needed.

    RFC 8259 has no literal for ``NaN`` or ``Infinity``, and Python's own
    encoder writes them as bare words — but Starlette's ``JSONResponse``
    already renders with ``allow_nan=False``, so invalid JSON never reaches
    a client. What it does instead is raise from inside the encoder *after*
    the handler returned, which arrives as an unexplained 500.

    So the value this module adds is not strictness, it is naming the
    failure — and the wrapper it ships is the **lenient** one, because
    emitting the non-compliant literals is what takes an override.
    """
    from starlette.responses import JSONResponse

    from engine.api.app import LenientJSONResponse

    assert json.dumps([math.nan, math.inf]) == "[NaN, Infinity]"
    with pytest.raises(ValueError, match="not JSON compliant"):
        JSONResponse(content=[math.nan])
    assert JSONResponse(content={"x": 1.5}).body == b'{"x":1.5}'
    assert LenientJSONResponse(content=[math.nan]).body == b"[NaN]"


def test_fastapis_own_encoder_turns_nan_into_null_without_saying_so():
    """The finding that decided the design.

    Starlette refuses ``NaN`` — but a handler returning a ``dict`` never
    reaches Starlette with the float intact, because FastAPI serialises the
    return value through its own encoder first and that encoder writes a
    non-finite float as **``null``**. Valid JSON, a 200, and a silently
    different number: nothing downstream can tell ``null`` from "this
    projection produced a NaN".

    So results are rendered in the handler and returned as a ``Response``,
    which FastAPI passes through untouched — asserted here by showing what
    the other path does.
    """
    from fastapi import FastAPI

    probe = FastAPI()

    @probe.get("/through-the-model")
    def through_the_model() -> dict:
        return {"x": [math.nan, math.inf, 1.5]}

    with TestClient(probe) as client:
        response = client.get("/through-the-model")
    assert response.status_code == 200
    assert response.json() == {"x": [None, None, 1.5]}
    assert "null" in response.text and "NaN" not in response.text


def test_a_non_finite_result_is_a_500_that_says_why():
    """Rather than the bare 500 the encoder would produce two layers down."""
    from engine.api import create_app

    app = create_app(max_workers=1)
    store = app.state.store
    with TestClient(app) as client:
        run = store.submit(REQUEST)
        store.wait(run.run_id, timeout=120)
        run.arrays["claims"] = np.full_like(run.arrays["claims"], np.nan)
        response = client.get(f"/runs/{run.run_id}/results")
        assert response.status_code == 500
        assert "RFC 8259" in response.json()["detail"]
    store.shutdown()


def test_allow_nan_sends_it_anyway_if_a_deployment_insists():
    """Not valid JSON, and opted into explicitly."""
    from engine.api import create_app

    app = create_app(max_workers=1, allow_nan=True)
    store = app.state.store
    with TestClient(app) as client:
        run = store.submit(REQUEST)
        store.wait(run.run_id, timeout=120)
        run.arrays["claims"] = np.full_like(run.arrays["claims"], np.nan)
        response = client.get(f"/runs/{run.run_id}/results")
        assert response.status_code == 200
        assert "NaN" in response.text
    store.shutdown()


# --------------------------------------------------------------------------
# The event stream
# --------------------------------------------------------------------------

def test_the_event_stream_replays_what_is_already_there(app, client):
    """``?timeout=`` bounds the stream so it ends.

    Without it the generator runs until the client disconnects, which is
    right for a sensor and untestable through a synchronous client — and it
    is what a proxy will do to a long-lived connection anyway, so bounding
    it is a deployment feature and not a test affordance.
    """
    run_id = _finished(app, client)
    response = client.get("/events?timeout=0.2")
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ")]
    assert [e["run_id"] for e in events] == [run_id]
    assert events[0]["state"] == "succeeded"
    assert events[0]["results_digest"]


def test_the_webhook_hook_receives_every_state_change():
    """An outbound HTTP call is a dependency this package does not take, so
    the notifier is injected and a deployment supplies its own client."""
    from engine.api import create_app

    delivered = []
    app = create_app(max_workers=1, on_event=delivered.append)
    store = app.state.store
    with TestClient(app) as client:
        run_id = client.post("/runs", json=REQUEST).json()["run_id"]
        store.wait(run_id, timeout=120)
    assert [e["state"] for e in delivered] == ["queued", "running",
                                               "succeeded"]
    store.shutdown()


# --------------------------------------------------------------------------
# The API is the engine, not a copy of it
# --------------------------------------------------------------------------

def test_the_api_produces_the_number_the_engine_produces(app, client):
    """The endpoint is a transport, not a second implementation. Same
    inputs through :func:`engine.core.registry.record_run` directly must
    give the same digest."""
    from engine.core.registry import record_run

    run_id = _finished(app, client)
    served = client.get(f"/runs/{run_id}/results").json()

    result, record = record_run(
        TermLife, from_dicts(REQUEST["modelpoints"]),
        Assumptions(mortality=MortalityTable.flat(0.01), lapse=0.04,
                    interest=0.025),
        REQUEST["proj_len"], outputs=REQUEST["outputs"])
    assert served["results_digest"] == record.results_digest
    direct = {n: np.asarray(result.array(n)) for n in REQUEST["outputs"]}
    received = {n: np.asarray(v, dtype=np.float64)
                for n, v in served["results"].items()}
    assert all(np.array_equal(direct[n], received[n]) for n in direct)


def test_the_registry_collects_every_successful_run(app, client):
    run_id = _finished(app, client)
    _finished(app, client, {**REQUEST, "proj_len": 6})
    registry = app.state.store.registry
    assert len(registry) == 2
    assert any(r.results_digest for r in registry)
    assert registry.find(
        next(r.run_id for r in registry)) is not None
    assert run_id  # the store's id is the request's, not the registry's
