"""RFC-032 — the demonstration surface: examples, fields, graph, overlay, page.

Skipped whole without FastAPI, as :mod:`tests.test_api` is.

The load-bearing tests here are the ones on :mod:`engine.api.examples`. A
worked example is documentation that runs, and documentation that runs is
only worth anything if something runs it: a template that grows a required
model-point field, or renames one, breaks its example silently otherwise —
and the failure would surface as a demonstration that 500s rather than as a
test that goes red.
"""

import json

import numpy as np
import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.catalogue import (  # noqa: E402
    InvalidRequestError, build_run, builder, catalogue,
)
from engine.api.examples import (  # noqa: E402
    EXAMPLES, UNAVAILABLE, example, unavailable,
)
from engine.api.reports import measure_run  # noqa: E402
from engine.api.store import RunStore  # noqa: E402
from engine.api.ui import UI_FILES, read_asset  # noqa: E402
from engine.core.modeldoc import modelpoint_fields  # noqa: E402
from engine.core.registry import record_run  # noqa: E402
from engine.library.term_life import TermLife  # noqa: E402


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


@pytest.fixture
def finished(app, client):
    """A completed TermLife run, from the worked example."""
    request = example("TermLife")["request"]
    accepted = client.post("/runs", json=request)
    run_id = accepted.json()["run_id"]
    app.state.store.wait(run_id, timeout=600)
    return run_id


# --- the worked examples -------------------------------------------------


def test_every_catalogued_model_is_either_worked_or_explained():
    """No template is offered and then silently unrunnable.

    The catalogue discovers models by walking the library, so a new template
    joins the API by existing. This is the price of that: it also has to
    join one of these two lists, and adding a template without deciding
    which fails here rather than at a caller's first request.
    """
    assert set(EXAMPLES) | set(UNAVAILABLE) == set(catalogue())
    assert not set(EXAMPLES) & set(UNAVAILABLE)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_the_worked_example_runs(name):
    """Runs, and produces finite numbers.

    A NaN would serialise as ``null`` through some encoders and refuse to
    serialise at all through this one — either way a demonstration that
    cannot show its own output.
    """
    result, record = record_run(**build_run(EXAMPLES[name]["request"]))
    outputs = EXAMPLES[name]["request"]["outputs"]
    assert set(record.outputs) == set(outputs)
    for series in outputs:
        values = np.asarray(result.aggregate(series), dtype=np.float64)
        assert np.all(np.isfinite(values)), f"{name}.{series} is not finite"


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_the_worked_example_supplies_every_required_field(name):
    """The static scan and the specimen agree.

    Two independent readings of the same template — one from its source,
    one written by hand — so a field added to a formula and not to the
    example is caught here instead of inside a projection.
    """
    required = set(modelpoint_fields(catalogue()[name]).required)
    for row in EXAMPLES[name]["request"]["modelpoints"]:
        assert not required - set(row), \
            f"{name} model point {row.get('id')} is missing {required - set(row)}"


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_the_worked_example_is_json(name):
    """It goes over the wire *as it stands*.

    Not merely serialisable: unchanged by the round trip. A mortality table
    keyed by integer ages would come back keyed by strings, and since a run
    is identified by a fingerprint of its request, that example would have
    one identifier submitted from Python and another submitted over HTTP —
    the same question, twice, computed twice.
    """
    request = EXAMPLES[name]["request"]
    assert json.loads(json.dumps(request)) == request
    assert EXAMPLES[name]["note"].strip()


def test_an_example_is_a_copy():
    first = example("TermLife")
    first["request"]["proj_len"] = 999
    assert example("TermLife")["request"]["proj_len"] != 999


def test_the_unavailable_templates_really_are_unavailable():
    """The reasons are checked, not asserted.

    A note saying a template needs a transition matrix is worth nothing if
    the template in fact runs; it would be a catalogue lying in the other
    direction. So every one of them is submitted, and every one has to fail.
    """
    models = catalogue()
    for name in UNAVAILABLE:
        request = {
            "model": name, "proj_len": 5,
            "assumptions": {"mortality": 0.01, "interest": 0.03},
            "modelpoints": [{"id": "X", **{
                field: 1 for field in modelpoint_fields(models[name]).required
            }}],
        }
        with pytest.raises(Exception):
            record_run(**build_run(request))


# --- model point fields --------------------------------------------------


def test_the_field_scan_separates_required_from_optional():
    fields = modelpoint_fields(TermLife)
    assert "sum_assured" in fields.required
    assert "term_years" in fields.required
    # ``getattr(self.mp, "duration_in_force", 0)`` says in its own third
    # argument that the field may be absent.
    assert "duration_in_force" in fields.optional
    assert "duration_in_force" not in fields.required
    assert not set(fields.required) & set(fields.optional)


def test_the_field_scan_admits_what_it_cannot_see():
    """``UnitLinkedGMxB`` reads its rider set out of ``self.mp.__dict__``.

    No static scan can name what is in there, and the honest answer is to
    say so rather than to return a list that looks complete.
    """
    from engine.library.unit_linked import UnitLinkedGMxB

    assert modelpoint_fields(UnitLinkedGMxB).reflective
    assert not modelpoint_fields(TermLife).reflective


def test_the_field_scan_finds_a_branch_a_trace_would_miss():
    """The reason it parses rather than runs.

    ``rare`` is read only at ``t == 9``, so a three-period trace never
    evaluates it and a discovery-by-running would report a model that needs
    no such field. The source has the branch in it whether or not anything
    took it.
    """
    from engine.core.model import Model, var

    class Branching(Model):
        @var
        def value(self, t):
            if t == 9:
                return self.mp.rare
            return self.mp.usual * 1.0

    fields = modelpoint_fields(Branching)
    assert "rare" in fields.required
    assert "usual" in fields.required


# --- the endpoints -------------------------------------------------------


def test_the_listing_says_which_models_can_be_run(client):
    listing = {m["name"]: m for m in client.get("/models").json()["models"]}
    assert listing["TermLife"]["example"] is True
    assert listing["TermLife"]["unavailable"] is None
    assert listing["UnitLinkedGMxB"]["example"] is False
    assert "scenario" in listing["UnitLinkedGMxB"]["unavailable"]


def test_the_example_endpoint_serves_a_body_that_submits(client):
    served = client.get("/models/TermLife/example").json()
    accepted = client.post("/runs", json=served["request"])
    assert accepted.status_code == 202


def test_a_template_without_an_example_says_why(client):
    response = client.get("/models/IncomeProtection/example")
    assert response.status_code == 404
    assert "TransitionMatrix" in response.json()["detail"]
    assert client.get("/models/NoSuchModel/example").status_code == 404


def test_describing_a_model_carries_its_model_point_fields(client):
    described = client.get("/models/TermLife").json()
    assert "sum_assured" in described["modelpoint_fields"]["required"]
    assert described["modelpoint_fields"]["reflective"] is False


# --- the graph -----------------------------------------------------------


def test_the_graph_endpoint_returns_the_edges(client):
    request = example("TermLife")["request"]
    graph = client.post("/models/TermLife/graph", json=request).json()
    assert graph["settled"] is True
    assert graph["horizon"] == 1
    assert set(graph["order"]) == set(TermLife.var_names())
    # A projection is a recursion: something has to reach back a period.
    assert any(edge["offset"] < 0 for edge in graph["edges"])
    assert all(edge["from"] in graph["order"] and edge["to"] in graph["order"]
               for edge in graph["edges"])
    assert "graph TD" in graph["mermaid"]


def test_the_graphs_lineage_is_the_transitive_answer(client):
    request = example("TermLife")["request"]
    graph = client.post("/models/TermLife/graph", json=request).json()
    claims = graph["lineage"]["claims"]
    # Claims are lives times a rate times a sum assured, so the mortality
    # variable is upstream of them however many steps away it is.
    assert "q_x" in claims["inputs_of"]
    assert "pols_if" in claims["inputs_of"]
    # And the other direction is the reviewer's question, which has to be
    # the mirror of it.
    assert "claims" in graph["lineage"]["q_x"]["affected_by"]


def test_the_graph_says_how_far_it_looked(client):
    request = example("TermLife")["request"]
    graph = client.post("/models/TermLife/graph?trace_length=5",
                        json=request).json()
    assert graph["trace_length"] == 5
    unchecked = client.post("/models/TermLife/graph?check_settled=false",
                            json=request).json()
    assert unchecked["settled"] is None


def test_the_graph_endpoint_validates_the_request(client):
    assert client.post("/models/TermLife/graph", json={}).status_code == 422
    assert client.post("/models/Nope/graph", json={}).status_code == 404


# --- aggregated results --------------------------------------------------


def test_aggregate_returns_the_engines_own_totals(app, client, finished):
    """Not a convenience: the digit that differs is a real one.

    The interpreted and vectorized executors reduce differently, so a
    client summing the per-model-point arrays itself is doing a *different*
    sum. Here the flag has to give back exactly what the result object
    gives back.
    """
    payload = client.get(f"/runs/{finished}/results?aggregate=true").json()
    assert payload["aggregated"] is True
    result = app.state.store.get(finished).result
    for name, series in payload["results"].items():
        assert series == result.aggregate(name)

    plain = client.get(f"/runs/{finished}/results").json()
    assert plain["aggregated"] is False
    # Unaggregated is still one row per period and one column per model
    # point, and the digest goes on describing that rather than the totals.
    assert len(plain["results"]["claims"][0]) == 2
    assert plain["results_digest"] == payload["results_digest"]


# --- the IFRS 17 overlay -------------------------------------------------


SPEC = {
    "inflows": "premiums",
    "outflows": ["claims", "expenses"],
    "acquisition": {"series": "initial_expenses"},
    "coverage": {"units": "pols_if"},
    "risk_adjustment": {"percent_of": "claims", "margin": 0.05},
    "discount_rate": 0.03,
}


def test_the_overlay_reconciles_to_the_groups_net_cash(client, finished):
    """The one number that says whether to believe the rest.

    Accounting decides which periods report the money, not how much of it
    there is, so total profit over a run-off is the group's undiscounted
    net cash.

    Relative, not absolute: the residual is float noise on a total of a few
    million, and how big that noise is scales with the group. An absolute
    bound would pass here and fail on a larger block for no reason worth a
    red build.
    """
    report = client.post(f"/runs/{finished}/reports/ifrs17", json=SPEC).json()
    reconciliation = report["reconciliation"]
    assert reconciliation["difference"] == pytest.approx(
        0.0, abs=1e-9 * abs(reconciliation["net_cash"]))
    assert reconciliation["closing_csm"] == 0.0
    assert report["onerous"] is False
    assert report["statement"]["csm"][0] > 0.0
    assert report["run_id"] == finished


def test_the_overlay_prices_the_group_the_form_shows(client, finished):
    """Every array in the statement is the length the client will index."""
    report = client.post(f"/runs/{finished}/reports/ifrs17", json=SPEC).json()
    n = report["periods"]
    statement = report["statement"]
    for name in ("csm", "loss_component", "risk_adjustment", "liability",
                 "fulfilment_cashflows"):
        assert len(statement[name]) == n + 1, name
    for name in ("csm_release", "insurance_revenue", "profit",
                 "insurance_service_result"):
        assert len(statement[name]) == n, name


def test_a_premium_cut_makes_the_group_onerous(app, client):
    """The asymmetry, over HTTP.

    A group worth less than it costs has no negative CSM to hold the loss:
    it goes to profit and loss on day one, in full. Same request, one
    number changed.
    """
    request = example("TermLife")["request"]
    for row in request["modelpoints"]:
        row["annual_premium"] = 50.0
    accepted = client.post("/runs", json=request)
    run_id = accepted.json()["run_id"]
    app.state.store.wait(run_id, timeout=600)
    report = client.post(f"/runs/{run_id}/reports/ifrs17", json=SPEC).json()
    assert report["onerous"] is True
    assert report["statement"]["csm"][0] == 0.0
    assert report["statement"]["loss_recognised"][0] > 0.0
    # Still reconciles: an onerous group reports its loss earlier, not a
    # different loss.
    assert report["reconciliation"]["difference"] == pytest.approx(
        0.0, abs=1e-6)


def test_the_overlay_refuses_a_series_the_run_does_not_hold(client, finished):
    response = client.post(f"/runs/{finished}/reports/ifrs17",
                           json={**SPEC, "inflows": "reinsurance_premium"})
    assert response.status_code == 422
    # ``reinsurance_premium`` is a variable TermLife has; this run did not
    # keep it, and saying so is the difference between a usable error and a
    # KeyError from inside an aggregation.
    assert "run holds no series" in response.json()["detail"]


def test_the_overlay_refuses_an_acquisition_that_is_not_one(client, finished):
    """A cost the projection puts in period four is not an acquisition
    cashflow, and summing the series anyway would finance it for four
    periods it was never outstanding."""
    response = client.post(f"/runs/{finished}/reports/ifrs17",
                           json={**SPEC, "acquisition": {"series": "expenses"}})
    assert response.status_code == 422
    assert "not an initial cost" in response.json()["detail"]


def test_the_overlay_needs_a_finished_run(app, client):
    request = dict(example("TermLife")["request"], proj_len=3)
    run_id = client.post("/runs", json=request).json()["run_id"]
    missing = client.post("/runs/nosuchrun/reports/ifrs17", json=SPEC)
    assert missing.status_code == 404
    app.state.store.wait(run_id, timeout=600)


def test_the_overlay_builds_its_curve_at_the_runs_frequency(app, client):
    """A yield curve defaults to twelve periods a year and an assumption
    set to one. Building the curve without looking at the run accretes a
    month of interest per annual period, and nothing in the output says
    so — the roll-forward still balances and every number in it is wrong.
    """
    store = RunStore(builder(), max_workers=1)
    try:
        request = example("TermLife")["request"]
        run = store.submit(request)
        store.wait(run.run_id, timeout=600)
        report = measure_run(run, SPEC)
        assert report["freq"] == 1

        monthly = dict(request)
        monthly["assumptions"] = dict(request["assumptions"], freq=12)
        monthly["proj_len"] = 24
        other = store.submit(monthly)
        store.wait(other.run_id, timeout=600)
        assert measure_run(other, SPEC)["freq"] == 12
    finally:
        store.shutdown()


def test_the_overlay_rejects_a_coverage_basis_it_cannot_release_over():
    """A group that provides no service has nothing to release a CSM over,
    and that is a bad request rather than a failed run — the projection
    succeeded."""
    store = RunStore(builder(), max_workers=1)
    try:
        run = store.submit(example("TermLife")["request"])
        store.wait(run.run_id, timeout=600)
        with pytest.raises(InvalidRequestError):
            measure_run(run, {**SPEC, "coverage": {"units": "pols_death",
                                                   "discount": "yes"}})
        with pytest.raises(InvalidRequestError):
            measure_run(run, {**SPEC, "periods": 0})
        with pytest.raises(InvalidRequestError):
            measure_run(run, {**SPEC, "nonsense": 1})
    finally:
        store.shutdown()


# --- the page ------------------------------------------------------------


def test_the_page_and_its_assets_are_served(client):
    page = client.get("/ui")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    for asset in UI_FILES:
        response = client.get(f"/ui/{asset}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            UI_FILES[asset].split(";")[0])


def test_the_page_serves_no_file_it_was_not_asked_to(client):
    """A whitelist rather than a directory: a static mount over a package
    directory is one traversal away from serving source."""
    for path in ("store.py", "..%2Fstore.py", "__init__.py"):
        assert client.get(f"/ui/{path}").status_code == 404
    with pytest.raises(KeyError):
        read_asset("../store.py")


def test_the_page_can_be_turned_off():
    from engine.api import create_app

    application = create_app(max_workers=1, ui=False)
    try:
        with TestClient(application) as c:
            assert c.get("/ui").status_code == 404
            assert c.get("/health").status_code == 200
    finally:
        application.state.store.shutdown(wait=True)


def test_the_page_asks_for_nothing_it_cannot_be_given(client):
    """Every path the page fetches is a route this app serves.

    It is a demonstration of the API, so a URL in it that the API does not
    answer is the demonstration lying about the API. Read out of the source
    rather than trusted.
    """
    import re

    source = read_asset("app.js")
    paths = set(re.findall(r'["`](/(?:health|models|runs|events)[^"`?]*)', source))
    assert "/health" in paths and "/models" in paths and "/events" in paths
    routes = {getattr(route, "path", "") for route in client.app.routes}
    for path in paths:
        # Template holes (${...}) stand for a path parameter.
        pattern = re.sub(r"\$\{[^}]*\}", "{p}", path)
        shape = re.sub(r"\{[^}]*\}", "{}", pattern)
        assert any(re.sub(r"\{[^}]*\}", "{}", route) == shape
                   for route in routes), f"{path} is not a route"
