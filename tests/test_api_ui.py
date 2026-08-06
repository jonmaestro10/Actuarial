"""The production surface, and the questions a page had to make the API answer.

RFC-048. The architecture rule is that every pixel is a documented REST
call, so most of this item is endpoints and most of this suite is about
them. What the screens needed and the API could not answer was: filter and
search the runs list, drill a result to one policy's column, say what
changed between two assumption sets, list the artifacts on record, and
serve the evidence pack.

The tests are mostly refusals, because each new route has a way of being
plausibly wrong:

- **a search that matched a digest anywhere** would find the wrong run, so
  digests match by prefix and the test holds a mid-string needle up;
- **a drill-down that returned a subset against the whole run's digest**
  would let a client check the wrong thing, so the response says
  ``partial``;
- **a diff whose verdict came from its own change list** could report "no
  changes" for two bases with different digests, so the verdict comes from
  the digests and the test asserts they cannot disagree;
- **an evidence pack built per request** would report whatever the server
  had time for, so it is served as built and refuses to be built here;
- **an artifacts route that 404'd when empty** would read as "this server
  does not do reconciliations", so it answers with an empty list.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.examples import example  # noqa: E402
from engine.api.ui import UI_FILES, read_asset  # noqa: E402
from engine.core.fingerprint import fingerprint  # noqa: E402
from engine.core.registry import ArtifactRecord, ArtifactRegistry  # noqa: E402
from engine.core.snapshot import (  # noqa: E402
    diff_snapshots,
    snapshot_rows,
)
from engine.data.assumptions import Assumptions, MortalityTable  # noqa: E402
from engine.data.expenses import ExpenseScale, Expenses  # noqa: E402

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}


def _assumptions(**overrides) -> Assumptions:
    kwargs = dict(mortality=MortalityTable(QX), lapse=0.04, interest=0.03,
                  expense_per_policy=50.0)
    kwargs.update(overrides)
    return Assumptions(**kwargs)


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
    request = example("TermLife")["request"]
    accepted = client.post("/runs", json=request)
    run_id = accepted.json()["run_id"]
    app.state.store.wait(run_id, timeout=600)
    return run_id


# --------------------------------------------------------------------------
# The snapshot walk
# --------------------------------------------------------------------------

def test_the_snapshot_root_is_the_digest_the_registry_records():
    """The property everything else rests on. A snapshot whose rows do not
    add up to the run's assumption digest describes something other than
    what ran."""
    basis = _assumptions()
    rows = snapshot_rows(basis)
    assert rows[0]["digest"] == fingerprint(basis)
    assert rows[0]["path"] == "Assumptions"
    assert len({row["path"] for row in rows}) == len(rows)


def test_a_changed_scalar_is_located_at_the_scalar_not_at_the_basis():
    """Deepest-wins. "The basis changed" is what the digests already say;
    the diff exists to say which number moved."""
    diff = diff_snapshots(_assumptions(), _assumptions(lapse=0.05))
    assert not diff.identical
    assert [c.path for c in diff.changes] == ["dynamic_lapse.base"]
    change = diff.changes[0]
    assert (change.left, change.right) == (0.04, 0.05)
    assert change.status == "changed"
    assert not change.summarised


def test_two_changes_are_two_rows_and_an_unchanged_component_is_none():
    diff = diff_snapshots(_assumptions(), _assumptions(lapse=0.05,
                                                       interest=0.04))
    assert sorted(c.path for c in diff.changes) == ["dynamic_lapse.base",
                                                    "interest"]
    # The mortality basis is ninety rates and contributes nothing, because
    # a matching digest prunes the whole subtree.
    assert not any("mortality" in c.path for c in diff.changes)


def test_an_identical_basis_rebuilt_from_scratch_shows_no_change():
    """Content addressing means a re-derived basis is the same basis — the
    same property RFC-044's approval leans on, seen from the other side."""
    diff = diff_snapshots(_assumptions(), _assumptions())
    assert diff.identical
    assert diff.changes == ()
    assert not diff.unlocated


def test_a_change_inside_an_unwalked_component_is_marked_summarised():
    """The walk is bounded, so some changes are located to a component and
    no finer. Saying which is the difference between a bounded report and
    an incomplete one."""
    other = dict(QX)
    other[50] = 0.9
    diff = diff_snapshots(_assumptions(),
                          _assumptions(mortality=MortalityTable(other)))
    assert not diff.identical
    assert [c.path for c in diff.changes] == ["mortality.basis.rates"]
    assert diff.changes[0].summarised
    assert diff.summarised == diff.changes


def test_a_component_that_appears_is_added_not_changed():
    """A basis that grew a component and a basis whose component moved are
    different events, and a reviewer needs to be told which."""
    diff = diff_snapshots(
        _assumptions(),
        _assumptions(expense_per_policy=0.0,
                     expenses=Expenses(renewal=ExpenseScale(per_policy=50.0),
                                       initial=ExpenseScale(per_policy=9.0))),
    )
    assert not diff.identical
    statuses = {c.status for c in diff.changes}
    assert statuses <= {"changed", "added", "removed"}
    assert diff.changes  # located somewhere under expenses
    assert all(c.path.startswith("expenses") for c in diff.changes)


def test_the_verdict_comes_from_the_digests_not_from_the_change_list():
    """The failure this guards: a bounded walk that found nothing reporting
    two different bases as the same. ``identical`` is the digest comparison
    and nothing else, so the two cannot disagree."""
    left, right = _assumptions(), _assumptions(lapse=0.05)
    diff = diff_snapshots(left, right, max_depth=0)
    assert diff.changes == ()          # nothing walked
    assert not diff.identical          # and it still knows
    assert diff.unlocated


# --------------------------------------------------------------------------
# The runs list
# --------------------------------------------------------------------------

def test_the_runs_list_filters_by_model_and_by_state(client, finished):
    assert client.get("/runs", params={"model": "TermLife"}
                      ).json()["n_matched"] == 1
    assert client.get("/runs", params={"model": "Endowment"}
                      ).json()["n_matched"] == 0
    assert client.get("/runs", params={"state": "succeeded"}
                      ).json()["n_matched"] == 1
    assert client.get("/runs", params={"state": "failed"}
                      ).json()["n_matched"] == 0
    assert client.get("/runs", params={"state": "nonsense"}).status_code == 422


def test_a_digest_search_matches_a_prefix_and_not_the_middle(client, finished):
    """A digest is quoted by its first characters everywhere in this repo.
    A substring match would let a search for one run turn up another whose
    digest merely contains those characters somewhere."""
    assert client.get("/runs", params={"q": finished[:8]}
                      ).json()["n_matched"] == 1
    assert client.get("/runs", params={"q": finished}
                      ).json()["n_matched"] == 1
    middle = finished[8:16]
    assert client.get("/runs", params={"q": middle}).json()["n_matched"] == 0
    assert client.get("/runs", params={"q": "nosuchthing"}
                      ).json()["n_matched"] == 0


def test_the_search_reaches_the_assumption_digest_a_reviewer_holds(
        client, finished):
    """The realistic lookup: somebody has an approval record, which names an
    assumption digest, and wants the runs made on it."""
    row = client.get(f"/runs/{finished}").json()
    assert row["assumptions_digest"]
    found = client.get("/runs", params={"q": row["assumptions_digest"][:10]})
    assert [r["run_id"] for r in found.json()["runs"]] == [finished]


def test_the_list_says_when_it_truncated(client, finished):
    page = client.get("/runs", params={"limit": 1}).json()
    assert page["n_matched"] == 1 and page["truncated"] is False
    assert client.get("/runs", params={"limit": 0}).status_code == 422


def test_the_single_run_route_carries_the_request_and_the_list_does_not(
        client, finished):
    """The whole difference between the two routes, and the reason the diff
    screen can compare two runs it did not submit."""
    one = client.get(f"/runs/{finished}").json()
    assert one["request"]["model"] == "TermLife"
    assert "assumptions" in one["request"]
    listed = client.get("/runs").json()["runs"][0]
    assert "request" not in listed
    assert listed["assumptions_digest"] == one["assumptions_digest"]


# --------------------------------------------------------------------------
# The results explorer
# --------------------------------------------------------------------------

def test_a_result_drills_to_one_variable_and_one_policy(client, finished):
    """Landscape §7.3.5: policy-level drill-down is the feature every vendor
    leads with, and the engine computes per policy anyway."""
    whole = client.get(f"/runs/{finished}/results").json()
    assert whole["partial"] is False
    assert whole["modelpoints"]

    one_var = client.get(f"/runs/{finished}/results",
                         params={"variable": "claims"}).json()
    assert one_var["outputs"] == ["claims"]
    assert one_var["partial"] is True

    mp = whole["modelpoints"][0]
    seriatim = client.get(f"/runs/{finished}/results",
                          params={"variable": "claims", "modelpoint": mp}
                          ).json()
    assert seriatim["modelpoint"] == mp
    column = [row[whole["modelpoints"].index(mp)]
              for row in whole["results"]["claims"]]
    # The drill-down is a selection, not a second calculation: the numbers
    # are the run's own, bit for bit.
    assert seriatim["results"]["claims"] == column


def test_a_selection_says_it_is_partial_so_nothing_checks_it_against_the_digest(
        client, finished):
    """The digest covers the whole run. A client that checked a policy's
    column against it would find a mismatch and blame the engine."""
    seriatim = client.get(f"/runs/{finished}/results",
                          params={"variable": "claims"}).json()
    assert seriatim["partial"] is True
    assert seriatim["results_digest"]
    whole = client.get(f"/runs/{finished}/results").json()
    assert whole["results_digest"] == seriatim["results_digest"]
    assert whole["partial"] is False


def test_the_drill_down_refuses_what_it_cannot_answer(client, finished):
    assert client.get(f"/runs/{finished}/results",
                      params={"variable": "surrenders"}).status_code == 422
    assert client.get(f"/runs/{finished}/results",
                      params={"modelpoint": "nobody"}).status_code == 404
    # A block total has no model points left in it to select.
    clash = client.get(f"/runs/{finished}/results",
                       params={"modelpoint": "T1", "aggregate": "true"})
    assert clash.status_code == 422
    assert "no model point to select" in clash.json()["detail"]


# --------------------------------------------------------------------------
# The diff route
# --------------------------------------------------------------------------

def test_the_diff_route_names_the_component_that_moved(client, finished):
    spec = client.get(f"/runs/{finished}").json()["request"]["assumptions"]
    other = dict(spec)
    other["interest"] = float(spec["interest"]) + 0.01
    body = client.post("/assumptions/diff",
                       json={"left": spec, "right": other}).json()
    assert body["identical"] is False
    assert [c["path"] for c in body["changes"]] == ["interest"]
    assert body["changes"][0]["right"] == other["interest"]


def test_the_diff_route_calls_a_reordered_spec_the_same_basis(client,
                                                              finished):
    """A text diff would report a reordered mapping as a change. This is a
    diff by component, so key order is not one."""
    spec = client.get(f"/runs/{finished}").json()["request"]["assumptions"]
    reordered = dict(reversed(list(spec.items())))
    body = client.post("/assumptions/diff",
                       json={"left": spec, "right": reordered}).json()
    assert body["identical"] is True
    assert body["n_changes"] == 0


def test_the_diff_route_refuses_a_half_stated_comparison(client, finished):
    spec = client.get(f"/runs/{finished}").json()["request"]["assumptions"]
    assert client.post("/assumptions/diff", json={"left": spec}
                       ).status_code == 422
    assert client.post("/assumptions/diff", json={}).status_code == 422
    bad = client.post("/assumptions/diff",
                      json={"left": spec, "right": {"mortality": "nonsense"}})
    assert bad.status_code == 422


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------

def test_an_empty_artifact_registry_answers_with_a_list(client):
    """A 404 here would read as "this server does not do reconciliations",
    which is a different and wrong statement."""
    body = client.get("/artifacts").json()
    assert body == {"artifacts": [], "n_artifacts": 0, "kinds": []}


def test_recorded_artifacts_are_listed_with_their_verdict():
    from engine.api import create_app

    registry = ArtifactRegistry()
    registry.add(ArtifactRecord(
        artifact_id="a" * 32, kind="parity", content_digest="b" * 32,
        inputs={"results_digest": "c" * 32}, label="Prophet Q1", ok=False,
    ))
    application = create_app(max_workers=1, artifacts=registry)
    try:
        with TestClient(application) as c:
            body = c.get("/artifacts").json()
            assert body["n_artifacts"] == 1
            assert body["kinds"] == ["parity"]
            assert body["artifacts"][0]["ok"] is False
            assert body["artifacts"][0]["label"] == "Prophet Q1"
            assert c.get("/artifacts", params={"kind": "workbook"}
                         ).json()["n_artifacts"] == 0
            assert c.get("/artifacts/" + "a" * 32).json()["kind"] == "parity"
            assert c.get("/artifacts/nope").status_code == 404
    finally:
        application.state.store.shutdown(wait=True)


# --------------------------------------------------------------------------
# The evidence pack
# --------------------------------------------------------------------------

def _pack(root, digest="d" * 32, sections=("coverage",), available=True):
    pack = root / digest
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(json.dumps({
        "pack_digest": digest, "code_version": "e" * 40,
        "machine_specific": False, "environment_in_digest": False,
        "sections": {name: "f" * 32 for name in sections},
    }))
    for name in sections:
        (pack / f"{name}.json").write_text(
            json.dumps({"available": available, "documented": 233})
        )
    return pack


def _app_with(**kwargs):
    from engine.api import create_app

    return create_app(max_workers=1, **kwargs)


def test_no_configured_pack_is_a_refusal_that_says_how_to_build_one(client):
    """Not a thinner pack computed on the spot: an evidence pack that
    quietly reports less than the real one is what RFC-049 exists to
    prevent."""
    response = client.get("/evidence")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "scripts/evidence_pack.py" in detail
    assert "runs the test suite" in detail


def test_a_built_pack_is_served_with_its_digests(tmp_path):
    _pack(tmp_path / "out")
    application = _app_with(evidence=tmp_path / "out")
    try:
        with TestClient(application) as c:
            body = c.get("/evidence").json()
            assert body["pack_digest"] == "d" * 32
            assert body["complete"] is True
            assert [s["name"] for s in body["sections"]] == ["coverage"]
            section = c.get("/evidence/coverage").json()
            assert section["content"]["documented"] == 233
            assert c.get("/evidence/nosuchsection").status_code == 404
    finally:
        application.state.store.shutdown(wait=True)


def test_a_pack_directory_is_accepted_directly_as_well_as_its_parent(tmp_path):
    pack = _pack(tmp_path / "out")
    application = _app_with(evidence=pack)
    try:
        with TestClient(application) as c:
            assert c.get("/evidence").json()["pack_digest"] == "d" * 32
    finally:
        application.state.store.shutdown(wait=True)


def test_an_unavailable_section_makes_the_pack_incomplete(tmp_path):
    """RFC-049's rule, carried onto the page: a section with nothing to
    report stays available, so ``available: false`` means the pack could not
    be collected — and the page must not show that as a complete pack."""
    _pack(tmp_path / "out", available=False)
    application = _app_with(evidence=tmp_path / "out")
    try:
        with TestClient(application) as c:
            body = c.get("/evidence").json()
            assert body["complete"] is False
            assert body["sections"][0]["available"] is False
    finally:
        application.state.store.shutdown(wait=True)


def test_two_packs_under_one_root_are_refused_rather_than_picked(tmp_path):
    """Picking one would mean the page reports a pack nobody chose, and
    which one it picked would depend on the filesystem."""
    _pack(tmp_path / "out", digest="1" * 32)
    _pack(tmp_path / "out", digest="2" * 32)
    application = _app_with(evidence=tmp_path / "out")
    try:
        with TestClient(application) as c:
            response = c.get("/evidence")
            assert response.status_code == 409
            assert "holds 2 packs" in response.json()["detail"]
    finally:
        application.state.store.shutdown(wait=True)


def test_a_directory_that_is_not_a_pack_says_so(tmp_path):
    (tmp_path / "empty").mkdir()
    application = _app_with(evidence=tmp_path / "empty")
    try:
        with TestClient(application) as c:
            response = c.get("/evidence")
            assert response.status_code == 404
            assert "no evidence pack" in response.json()["detail"]
    finally:
        application.state.store.shutdown(wait=True)


# --------------------------------------------------------------------------
# The page itself
# --------------------------------------------------------------------------

def test_the_page_offers_every_view_the_item_asks_for(client):
    page = read_asset("index.html")
    for tab in ("runs", "results", "assumptions", "evidence"):
        assert f'data-tab="{tab}"' in page
        assert f'id="tab-{tab}"' in page
    assert client.get("/ui").status_code == 200


def test_every_endpoint_the_page_calls_exists(client):
    """RFC-032's architecture rule, kept honest. The page has no privileged
    channel, so a path it fetches that the API does not serve is a broken
    screen — and this is the test that notices before a reviewer does."""
    import re

    script = read_asset("app.js")
    paths = set(re.findall(r'api\(`(/[a-z/]+)', script))
    paths |= set(re.findall(r'api\("(/[a-z/]+)"', script))
    paths |= set(re.findall(r'postJSON\("(/[a-z/]+)"', script))
    documented = {route.path for route in client.app.routes}
    for path in paths:
        # Template segments are written as ${...} in the script and as
        # {name} in the route table; compare the fixed prefix.
        assert any(route.startswith(path) or path.startswith(route.rstrip("/"))
                   for route in documented), f"{path} is not a route"
    assert "/assumptions/diff" in paths
    assert "/artifacts" in paths
    assert "/evidence" in paths


def test_the_assets_are_still_the_three_the_server_will_serve():
    assert set(UI_FILES) == {"index.html", "app.js", "styles.css"}
    for asset in UI_FILES:
        assert read_asset(asset).strip()
