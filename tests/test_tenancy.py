"""Isolation asserted, not described.

§9's G1 states the rule these tests exist to enforce: **isolation is asserted
by tests, not by a policy document.** So every route that can reach a run is
checked from the wrong tenant, the deduplication guarantee is checked in both
directions, and the one signal that sharing compute genuinely leaks is
asserted to be *stated* rather than assumed absent.

The failure mode throughout is the same and it is quiet: a tenant seeing
something it should not, in a response nobody looked at closely because it
had the right shape.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi", reason="the REST API needs the [api] extra")

from engine.api.auth import (  # noqa: E402
    Principal, Principals, PrincipalsError, Role, mint_token, token_digest,
)
from engine.api.store import RunStore  # noqa: E402
from engine.api.tenancy import (  # noqa: E402
    SINGLE_TENANT, Tenancy, TenancyError, TenantRef, namespace,
    shared_compute_leak, tenant_of, tenants_in, valid_tenant,
)

from fastapi.testclient import TestClient  # noqa: E402


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

#: Two tenants, each with a principal that can both submit and read.
TOKENS = {name: mint_token() for name in ("acme", "beta", "untenanted")}
SPEC = {"principals": [
    {"name": "acme-ops", "token_sha256": token_digest(TOKENS["acme"]),
     "roles": ["viewer", "runner"], "tenant": "acme"},
    {"name": "beta-ops", "token_sha256": token_digest(TOKENS["beta"]),
     "roles": ["viewer", "runner"], "tenant": "beta"},
]}


def auth(name: str) -> dict:
    return {"Authorization": f"Bearer {TOKENS[name]}"}


def _client(**kwargs):
    from engine.api import create_app
    return TestClient(create_app(max_workers=1, principals=SPEC, **kwargs))


@pytest.fixture(scope="module")
def tenanted():
    with _client() as client:
        yield client


def _submit(client, tenant, request=None):
    response = client.post("/runs", json=request or REQUEST, headers=auth(tenant))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    for _ in range(400):
        got = client.get(f"/runs/{run_id}", headers=auth(tenant)).json()
        if got.get("state") in {"succeeded", "failed"}:
            assert got["state"] == "succeeded", got.get("error")
            return run_id, got
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never settled")


# --------------------------------------------------------------------------
# The name is a namespace segment, and the refusals are the point
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../etc", "a/b", "UPPER", "-lead", "trail-", "a", "", "x" * 41,
    "sp ace", "dot.dot", "under_score",
])
def test_a_tenant_name_that_could_escape_its_namespace_is_refused(bad):
    """Guards a tenant name reaching the filesystem before it is checked.

    The name becomes a registry prefix and a warehouse partition directory,
    so ``..`` and ``a/b`` have to be rejected at the point of configuration.
    Sanitising them later means something has already been named after the
    unsanitised string.
    """
    with pytest.raises(TenancyError):
        valid_tenant(bad)


@pytest.mark.parametrize("good", ["ac", "acme", "acme-corp", "a1-2b", "x" * 40])
def test_a_usable_tenant_name_is_accepted(good):
    """The grant, asserted beside the refusals so the rule is not vacuous."""
    assert valid_tenant(good) == good


def test_single_tenant_names_are_left_alone():
    """Guards tenancy renaming what an untenanted deployment already stored.

    Switching tenancy on over an existing registry must not orphan its
    contents, so the single-tenant namespace is the identity function.
    """
    assert namespace(SINGLE_TENANT, "runs/abc") == "runs/abc"
    assert namespace("acme", "runs/abc") == "acme/runs/abc"


# --------------------------------------------------------------------------
# The principals file
# --------------------------------------------------------------------------

def test_a_partly_tenanted_principals_file_is_refused():
    """Guards the reading nobody agrees on.

    A file where some principals carry a tenant and others do not has two
    defensible readings — absent means its own tenant, or absent means sees
    everything — and they differ by exactly the amount that matters. This
    refuses instead of choosing.
    """
    principals = Principals.from_dict({"principals": [
        {"name": "a", "token_sha256": "0" * 64, "roles": ["viewer"],
         "tenant": "acme"},
        {"name": "b", "token_sha256": "1" * 64, "roles": ["viewer"]},
    ]})
    with pytest.raises(TenancyError, match="carry no tenant while others do"):
        tenants_in(principals)


def test_an_untenanted_file_is_single_tenant_and_not_an_error():
    """Guards tenancy becoming mandatory by accident.

    The engine is a library first. A deployment that has not opted in must
    behave exactly as it did, which means no tenants is a valid answer and
    not a misconfiguration.
    """
    principals = Principals.from_dict({"principals": [
        {"name": "a", "token_sha256": "0" * 64, "roles": ["viewer"]},
    ]})
    assert tenants_in(principals) == frozenset()
    assert tenant_of(principals.get("a")).is_single


def test_a_bad_tenant_in_the_principals_file_fails_to_load():
    """Guards a traversal arriving as configuration rather than as a request."""
    with pytest.raises(PrincipalsError, match="namespace segment"):
        Principals.from_dict({"principals": [
            {"name": "a", "token_sha256": "0" * 64, "roles": ["viewer"],
             "tenant": "../other"},
        ]})


def test_the_tenant_shows_up_where_an_auditor_reads_it():
    """Guards a tenant that is enforced but invisible.

    D1's rule is that the access-control list is small enough to read. A
    tenant that scopes every route but appears in no summary is a rule an
    auditor has to take on trust.
    """
    principal = Principal("a", frozenset({Role.VIEWER}), tenant="acme")
    assert principal.summary()["tenant"] == "acme"
    # And absent rather than null on a single-tenant deployment, so an
    # untenanted file's output is byte-identical to what it was before.
    assert "tenant" not in Principal("a", frozenset({Role.VIEWER})).summary()


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------

def test_an_unclaimed_run_is_invisible_rather_than_public():
    """Guards the natural implementation, which is wrong.

    Returning True for a run nobody has claimed reads as permissive-by-
    default and is: every run submitted before tenancy was switched on would
    become readable by every tenant at once.
    """
    scope = Tenancy()
    assert not scope.may_see("never-seen", TenantRef("acme"))


def test_single_tenant_sees_everything_including_unclaimed():
    """The other direction, so the check above cannot be satisfied by
    refusing everyone. A deployment with no tenants has nothing to separate,
    and must keep answering as it did."""
    scope = Tenancy()
    assert scope.may_see("never-seen", TenantRef(SINGLE_TENANT))


def test_two_tenants_that_submit_the_same_work_both_see_it():
    """Guards the second submitter being locked out of its own run.

    Deduplication means the second tenant's submission returns the *first*
    tenant's run object. If visibility were ownership rather than a set, the
    second tenant would receive a run id it is then forbidden to read.
    """
    scope = Tenancy()
    acme, beta = TenantRef("acme"), TenantRef("beta")
    scope.note("shared", acme)
    scope.note("shared", beta)
    assert scope.may_see("shared", acme)
    assert scope.may_see("shared", beta)
    assert scope.tenants_of("shared") == frozenset({"acme", "beta"})
    assert not scope.may_see("shared", TenantRef("gamma"))


# --------------------------------------------------------------------------
# Every route that can reach a run, checked from the wrong tenant
# --------------------------------------------------------------------------

def test_cross_tenant_reads_are_denied_on_every_run_route(tenanted):
    """**The G1 acceptance criterion.** Every route, not a representative one.

    A route added later that forgets the check is the whole risk here, so
    this enumerates them and a new one has to be added to the list
    deliberately. 404 rather than 403 throughout: a 403 confirms the run
    exists, and run ids are request fingerprints, so it would hand one tenant
    an oracle over another's submissions.
    """
    run_id, _ = _submit(tenanted, "acme")

    routes = [
        ("GET", f"/runs/{run_id}", None),
        ("GET", f"/runs/{run_id}/results", None),
        ("POST", f"/runs/{run_id}/reports/ifrs17",
         {"cashflows": {"premiums": "premium_income"}, "discount_rate": 0.03}),
    ]
    for method, path, body in routes:
        response = tenanted.request(method, path, json=body,
                                    headers=auth("beta"))
        assert response.status_code == 404, (
            f"{method} {path} answered {response.status_code} to the wrong "
            f"tenant; anything but 404 confirms the run exists"
        )
        assert run_id not in response.text or "unknown run" in response.text

    # And the owning tenant still gets through, so the assertion above is not
    # satisfied by the routes being broken for everyone.
    assert tenanted.get(f"/runs/{run_id}",
                        headers=auth("acme")).status_code == 200


def test_the_runs_list_shows_only_this_tenants_runs(tenanted):
    """Guards the enumeration path, which leaks without any id being guessed."""
    acme_id, _ = _submit(tenanted, "acme")
    beta_id, _ = _submit(tenanted, "beta", {**REQUEST, "proj_len": 6})

    acme_rows = tenanted.get("/runs", headers=auth("acme")).json()["runs"]
    beta_rows = tenanted.get("/runs", headers=auth("beta")).json()["runs"]
    acme_ids = {row["run_id"] for row in acme_rows}
    beta_ids = {row["run_id"] for row in beta_rows}

    assert acme_id in acme_ids and beta_id not in acme_ids
    assert beta_id in beta_ids and acme_id not in beta_ids


def test_the_event_stream_does_not_replay_another_tenants_runs(tenanted):
    """Guards the live feed, which is worse than the list.

    The runs list can only leak what already exists; the event stream leaks
    every run any tenant submits from now on — a running commentary on
    another customer's activity.
    """
    acme_id, _ = _submit(tenanted, "acme")
    body = tenanted.get("/events", params={"timeout": 0.2},
                        headers=auth("beta")).text
    assert acme_id not in body


# --------------------------------------------------------------------------
# Deduplicate compute, but not visibility
# --------------------------------------------------------------------------

def test_identical_submissions_from_two_tenants_share_one_computation(tenanted):
    """**The other half of the G1 criterion.**

    The fingerprint stays global and content-true, so identical work is
    computed once. A digest that meant something different per tenant would
    be a digest that means nothing, and the registry's provenance story rests
    on it meaning something.
    """
    request = {**REQUEST, "proj_len": 7}
    acme_id, _ = _submit(tenanted, "acme", request)
    beta_id, _ = _submit(tenanted, "beta", request)
    assert acme_id == beta_id, "identical work was computed twice"

    # Same run, and now visible to both — which is the part that must not be
    # confused with the run having been *transferred* to the second tenant.
    assert tenanted.get(f"/runs/{acme_id}",
                        headers=auth("acme")).status_code == 200
    assert tenanted.get(f"/runs/{beta_id}",
                        headers=auth("beta")).status_code == 200


def test_a_deployment_may_refuse_to_share_compute_and_then_ids_differ():
    """Guards the opt-out silently not opting out.

    ``dedupe_across_tenants=False`` has to change the *fingerprint*, or the
    store collides anyway and the flag is decoration. It does, and that moves
    the run id — which is a real consequence and is asserted here rather than
    discovered by a deployment whose ids changed under it.
    """
    request = {**REQUEST, "proj_len": 8}
    with _client(dedupe_across_tenants=False) as client:
        acme_id, _ = _submit(client, "acme", request)
        beta_id, _ = _submit(client, "beta", request)
        assert acme_id != beta_id, (
            "dedupe_across_tenants=False still shared one computation"
        )
        # Each sees its own and not the other's.
        assert client.get(f"/runs/{beta_id}",
                          headers=auth("acme")).status_code == 404
        assert client.get(f"/runs/{acme_id}",
                          headers=auth("beta")).status_code == 404


def test_the_salt_never_reaches_the_recorded_request():
    """Guards the opt-out rewriting history to achieve itself.

    The run must still report the request that was submitted. A salt folded
    into the stored request would make two tenants' records differ by a field
    neither of them sent.
    """
    store = RunStore(build=lambda request: {})
    plain = store.identify(REQUEST)
    salted = store.identify(REQUEST, "acme")
    assert plain != salted, "the salt did not move the fingerprint"

    run = store.submit(REQUEST, "acme")
    assert run.request == REQUEST, "the salt leaked into the stored request"
    store.shutdown()


# --------------------------------------------------------------------------
# The leak that sharing compute genuinely creates
# --------------------------------------------------------------------------

def test_the_residual_leak_is_stated_rather_than_assumed_absent():
    """Guards the claim this system would most like to make and cannot.

    Deduplicating across tenants is a decision with a cost: a tenant learns
    that *somebody* has already run a request it can construct. That is
    small, and it is not nothing, and a security posture is worth exactly
    what its most inconvenient sentence is worth. So the sentence exists, it
    names the signal and its bounds, and it says what to do instead.
    """
    words = shared_compute_leak()
    for required in ("sooner", "does not reveal which tenant",
                     "dedupe_across_tenants=False"):
        assert required in words, f"the leak statement stopped saying {required!r}"


def test_turning_deduplication_off_removes_the_leak_from_the_summary():
    """Guards a caveat outliving the thing it caveats.

    A deployment that paid for recompute should not still be told it has a
    shared-compute leak — a warning that is present when it does not apply is
    a warning that gets ignored when it does.
    """
    assert Tenancy(dedupe_across_tenants=True).summary()["shared_compute_leak"]
    assert Tenancy(dedupe_across_tenants=False).summary()[
        "shared_compute_leak"] is None


def test_health_says_whether_tenancy_is_on_but_not_who_the_tenants_are(tenanted):
    """Guards a customer list on an unauthenticated route.

    ``/health`` is deliberately the one route with no role requirement. That
    makes it the one route where inventory must not appear, and a SaaS
    platform's tenant names are the most valuable inventory it has.
    """
    body = tenanted.get("/health").json()
    assert body["tenancy"] == "enabled"
    assert "acme" not in str(body) and "beta" not in str(body)
