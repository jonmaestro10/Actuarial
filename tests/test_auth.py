"""Access control: allowed and denied, route by route.

RFC-043. An access-control test suite that only checks the happy path is a
suite that would pass against a system with no access control at all, so
every route is exercised twice — once with a principal that carries the role
and once with one that does not — and the default-off behaviour is pinned
separately, because "the library is unchanged" is the load-bearing claim
that lets this exist at all.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.auth import (  # noqa: E402
    Principal,
    Principals,
    PrincipalsError,
    Role,
    bearer_token,
    mint_token,
    token_digest,
)

REQUEST = {
    "model": "TermLife",
    "proj_len": 5,
    "outputs": ["claims", "pols_if"],
    "assumptions": {"mortality": 0.01, "lapse": 0.04, "interest": 0.025},
    "modelpoints": [
        {"id": "T1", "age_at_entry": 40, "term_years": 20,
         "sum_assured": 250_000.0, "annual_premium": 900.0, "init_pols": 1},
    ],
}

#: One principal per role, plus one carrying everything a run needs.
TOKENS = {name: mint_token() for name in
          ("viewer", "runner", "approver", "admin", "nobody", "operator")}
ROLES = {
    "viewer": ["viewer"],
    "runner": ["runner"],
    "approver": ["approver"],
    "admin": ["admin"],
    "nobody": [],           # invalid in a file; used through Principal directly
    "operator": ["viewer", "runner"],
}
SPEC = {"principals": [
    {"name": name, "token_sha256": token_digest(TOKENS[name]),
     "roles": roles}
    for name, roles in ROLES.items() if roles
]}


def auth(name: str) -> dict:
    return {"Authorization": f"Bearer {TOKENS[name]}"}


@pytest.fixture(scope="module")
def secured():
    from engine.api import create_app

    with TestClient(create_app(max_workers=1, principals=SPEC)) as client:
        yield client


@pytest.fixture(scope="module")
def open_app():
    from engine.api import create_app

    with TestClient(create_app(max_workers=1)) as client:
        yield client


# --------------------------------------------------------------------------
# Off by default
# --------------------------------------------------------------------------

def test_with_no_principals_the_api_is_the_api_it_was(open_app):
    """The claim that lets authentication exist in a library at all."""
    assert open_app.get("/health").json()["auth"] == "disabled"
    assert open_app.get("/models").status_code == 200
    assert open_app.get("/runs").status_code == 200
    assert open_app.post("/runs", json=REQUEST).status_code == 202
    # And there is no principal list to read, rather than an empty one.
    assert open_app.get("/principals").status_code == 404


def test_health_stays_reachable_and_says_less(secured):
    """A load balancer has no token, and inventory is not its business."""
    body = secured.get("/health").json()
    assert body["auth"] == "required"
    assert "models" not in body and "runs" not in body


# --------------------------------------------------------------------------
# Every route, allowed and denied
# --------------------------------------------------------------------------

READ_ROUTES = [
    ("GET", "/models", None),
    ("GET", "/models/TermLife", None),
    ("GET", "/models/TermLife/example", None),
    ("GET", "/models/TermLife/documentation", None),
    ("GET", "/runs", None),
]
RUN_ROUTES = [
    ("POST", "/runs", REQUEST),
    ("POST", "/models/TermLife/graph", REQUEST),
]


def call(client, method, path, body, headers):
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body", READ_ROUTES)
def test_a_read_route_needs_the_viewer_role(secured, method, path, body):
    assert call(secured, method, path, body, auth("viewer")).status_code < 400
    denied = call(secured, method, path, body, auth("runner"))
    assert denied.status_code == 403
    assert "needs ['viewer']" in denied.json()["detail"]


@pytest.mark.parametrize("method,path,body", RUN_ROUTES)
def test_a_route_that_executes_needs_the_runner_role(secured, method, path,
                                                     body):
    """Anything that runs client-supplied model code is a runner route —
    including the graph endpoint, which traces a model rather than merely
    describing one."""
    assert call(secured, method, path, body, auth("runner")).status_code < 400
    denied = call(secured, method, path, body, auth("viewer"))
    assert denied.status_code == 403
    assert "needs ['runner']" in denied.json()["detail"]


@pytest.mark.parametrize("method,path,body", READ_ROUTES + RUN_ROUTES)
def test_no_token_is_a_401_everywhere(secured, method, path, body):
    response = call(secured, method, path, body, None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method,path,body", READ_ROUTES + RUN_ROUTES)
def test_an_unknown_token_is_a_401_not_a_403(secured, method, path, body):
    response = call(secured, method, path, body,
                    {"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert "unknown token" in response.json()["detail"]


def test_results_and_events_are_viewer_routes(secured):
    submitted = secured.post("/runs", json=REQUEST, headers=auth("operator"))
    run_id = submitted.json()["run_id"]
    for _ in range(200):
        state = secured.get(f"/runs/{run_id}", headers=auth("viewer")).json()
        if state["state"] in ("succeeded", "failed"):
            break
    assert state["state"] == "succeeded"

    assert secured.get(f"/runs/{run_id}/results",
                       headers=auth("viewer")).status_code == 200
    assert secured.get(f"/runs/{run_id}/results",
                       headers=auth("runner")).status_code == 403
    assert secured.get("/events?timeout=0.1",
                       headers=auth("runner")).status_code == 403
    assert secured.post(f"/runs/{run_id}/reports/ifrs17", json={},
                        headers=auth("viewer")).status_code == 403


def test_the_ui_is_public_and_carries_no_data(secured):
    """The page is HTML and JavaScript; every number on it comes from a
    call the browser has to authenticate for itself."""
    assert secured.get("/ui").status_code == 200
    assert secured.get("/ui/app.js").status_code == 200


# --------------------------------------------------------------------------
# The roles themselves
# --------------------------------------------------------------------------

def test_no_role_implies_another(secured):
    """A ladder is a convenience that becomes an escalation."""
    assert secured.get("/models", headers=auth("admin")).status_code == 403
    assert secured.get("/principals", headers=auth("viewer")).status_code == 403
    assert secured.post("/runs", json=REQUEST,
                        headers=auth("admin")).status_code == 403
    assert secured.get("/models",
                       headers=auth("operator")).status_code == 200
    assert secured.post("/runs", json=REQUEST,
                        headers=auth("operator")).status_code == 202


def test_the_approver_role_grants_nothing_yet(secured):
    """Stated rather than implied: RFC-044 brings the routes it exists for,
    and until then a principals file can name it without it doing anything."""
    for method, path, body in READ_ROUTES + RUN_ROUTES:
        assert call(secured, method, path, body,
                    auth("approver")).status_code == 403


def test_the_principal_list_shows_roles_and_never_tokens(secured):
    body = secured.get("/principals", headers=auth("admin")).json()
    names = {row["name"] for row in body["principals"]}
    assert names == {name for name, roles in ROLES.items() if roles}
    assert body["you"]["name"] == "admin"
    assert "token" not in json.dumps(body)
    assert TOKENS["admin"] not in json.dumps(body)


def test_there_is_no_route_that_edits_the_principal_list(secured):
    """Identity is deployed, not edited over HTTP."""
    assert secured.post("/principals", json={}, headers=auth("admin")
                        ).status_code == 405
    assert secured.delete("/principals", headers=auth("admin")
                          ).status_code == 405


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------

def test_a_token_is_stored_as_a_digest_and_recovered_by_nobody():
    token = mint_token()
    principals = Principals.from_dict({"principals": [
        {"name": "a", "token_sha256": token_digest(token), "roles": ["viewer"]}
    ]})
    assert principals.authenticate(token).name == "a"
    assert principals.authenticate(token + "x") is None
    assert principals.authenticate("") is None
    assert token not in json.dumps(principals.summary())


def test_minted_tokens_do_not_repeat():
    assert len({mint_token() for _ in range(50)}) == 50


@pytest.mark.parametrize("payload,message", [
    ({}, "non-empty 'principals' list"),
    ({"principals": []}, "non-empty 'principals' list"),
    ({"principals": ["nope"]}, "not an object"),
    ({"principals": [{"token_sha256": "0" * 64, "roles": ["viewer"]}]},
     "has no name"),
    ({"principals": [{"name": "a", "roles": ["viewer"]}]}, "no token_sha256"),
    ({"principals": [{"name": "a", "token_sha256": "short",
                      "roles": ["viewer"]}]}, "not a SHA-256"),
    ({"principals": [{"name": "a", "token_sha256": "0" * 64, "roles": []}]},
     "has no roles"),
    ({"principals": [{"name": "a", "token_sha256": "0" * 64,
                      "roles": ["superuser"]}]}, "unknown role 'superuser'"),
    ({"principals": [{"name": "a", "token_sha256": "0" * 64,
                      "roles": ["viewer"]},
                     {"name": "b", "token_sha256": "0" * 64,
                      "roles": ["viewer"]}]}, "share a token"),
    ({"principals": [{"name": "a", "token_sha256": "0" * 64,
                      "roles": ["viewer"]},
                     {"name": "a", "token_sha256": "1" * 64,
                      "roles": ["viewer"]}]}, "duplicate principal"),
])
def test_a_principals_file_that_cannot_be_trusted_refuses_to_load(payload,
                                                                  message):
    """Every one of these would leave somebody believing an access grant
    exists that does not, or the other way round."""
    with pytest.raises(PrincipalsError, match=message):
        Principals.from_dict(payload)


def test_a_principals_file_loads_from_disk(tmp_path):
    token = mint_token()
    path = tmp_path / "principals.json"
    path.write_text(json.dumps({"principals": [
        {"name": "a", "token_sha256": token_digest(token),
         "roles": ["viewer", "runner"]}
    ]}), encoding="utf-8")
    principals = Principals.load(path)
    assert len(principals) == 1
    assert principals.authenticate(token).has(Role.VIEWER, Role.RUNNER)

    from engine.api import create_app

    with TestClient(create_app(principals=path)) as client:
        assert client.get("/health").json()["auth"] == "required"
        assert client.get(
            "/models", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200


def test_a_missing_or_unreadable_file_raises(tmp_path):
    with pytest.raises(PrincipalsError, match="No such file"):
        Principals.load(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(PrincipalsError, match="not JSON"):
        Principals.load(bad)


@pytest.mark.parametrize("header,expected", [
    (None, None),
    ("", None),
    ("Bearer abc", "abc"),
    ("bearer abc", "abc"),
    ("Bearer  abc ", "abc"),
    ("Basic abc", None),
    ("Bearer", None),
    ("Bearer  ", None),
])
def test_the_authorization_header_is_parsed_strictly(header, expected):
    assert bearer_token(header) == expected


def test_a_principal_needs_every_role_a_route_asks_for():
    both = Principal("a", frozenset({Role.VIEWER, Role.RUNNER}))
    assert both.has(Role.VIEWER, Role.RUNNER)
    assert not both.has(Role.VIEWER, Role.ADMIN)
    assert Principal("b", frozenset()).has() is True
