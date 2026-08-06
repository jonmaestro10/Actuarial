"""Four eyes, bound to content.

RFC-044. The failure this exists to prevent is not "an unapproved run got
through" — it is "an approved label stopped pointing at the numbers somebody
approved". So the tests are mostly about the digest:

- the same basis, rebuilt from scratch, is still approved;
- a basis changed by one basis point is not, and no message about it being
  "essentially the same" can be reached;
- an approval by the submitter is not a second pair of eyes;
- a revocation takes effect, and takes effect only for its own signatory.

The core half runs without HTTP, because four-eyes is a property of a
decision rather than of a transport.
"""

import json

import pytest

from engine.core.approvals import (
    Approval,
    ApprovalRegistry,
    ApprovalRequired,
    assumptions_digest,
    check_approved,
)
from engine.core.registry import record_run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.term_life import TermLife

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}


def basis(**overrides):
    fields = dict(mortality=MortalityTable(QX), lapse=0.04, interest=0.03,
                  expense_per_policy=50.0)
    fields.update(overrides)
    return Assumptions(**fields)


# --------------------------------------------------------------------------
# The digest is the whole design
# --------------------------------------------------------------------------

def test_the_digest_is_the_one_the_run_registry_records():
    """What was approved and what ran have to be the same string, or the
    check is a check on something else."""
    points = [ModelPoint(id="T1", age_at_entry=45, term_years=20,
                         sum_assured=250_000.0, annual_premium=1_100.0,
                         init_pols=1)]
    _, record = record_run(TermLife, points, basis(), 10, outputs=["claims"])
    assert assumptions_digest(basis()) == record.assumptions_digest


def test_an_identically_rebuilt_basis_stays_approved():
    """Content addressing means re-derivation is free: a basis rebuilt by
    another process on another machine is the same basis."""
    log = ApprovalRegistry()
    log.approve(assumptions_digest(basis()), "reviewer")
    assert check_approved(basis(), "submitter", log)


def test_a_basis_changed_at_all_is_not_approved():
    log = ApprovalRegistry()
    log.approve(assumptions_digest(basis()), "reviewer")
    with pytest.raises(ApprovalRequired, match="is not approved"):
        check_approved(basis(lapse=0.0401), "submitter", log)
    with pytest.raises(ApprovalRequired):
        check_approved(basis(interest=0.030000000001), "submitter", log)


def test_the_refusal_carries_the_digest_the_submitter_needs():
    log = ApprovalRegistry()
    with pytest.raises(ApprovalRequired) as raised:
        check_approved(basis(), "submitter", log)
    assert raised.value.digest == assumptions_digest(basis())
    assert raised.value.approvers == ()


# --------------------------------------------------------------------------
# Two pairs of eyes
# --------------------------------------------------------------------------

def test_approving_your_own_submission_is_one_pair_of_eyes_twice():
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice")
    with pytest.raises(ApprovalRequired, match="approved only by alice") as e:
        check_approved(basis(), "alice", log)
    assert e.value.approvers == ("alice",)
    # And the same basis submitted by anybody else goes through.
    assert check_approved(basis(), "bob", log) == digest


def test_a_second_approver_rescues_a_self_approved_basis():
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice")
    log.approve(digest, "bob")
    assert check_approved(basis(), "alice", log) == digest
    assert log.approvers(digest) == ("alice", "bob")


def test_is_approved_asks_a_weaker_question_without_a_submitter():
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice")
    assert log.is_approved(digest) is True
    assert log.is_approved(digest, submitter="alice") is False
    assert log.is_approved(digest, submitter="bob") is True


# --------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------

def test_a_revocation_is_an_entry_not_a_deletion():
    """The interesting half of the history is who took an approval back."""
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice", note="reviewed against the pricing basis")
    log.revoke(digest, "alice", note="wrong table vintage")
    assert log.approvers(digest) == ()
    assert not log.is_approved(digest)
    history = log.history(digest)
    assert [e.action for e in history] == ["approve", "revoke"]
    assert history[0].note == "reviewed against the pricing basis"
    assert len(log) == 2


def test_a_revocation_takes_back_only_its_own_signature():
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice")
    log.approve(digest, "bob")
    log.revoke(digest, "alice")
    assert log.approvers(digest) == ("bob",)
    assert log.is_approved(digest, submitter="alice")


def test_re_approving_after_a_revocation_works():
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice")
    log.revoke(digest, "alice")
    log.approve(digest, "alice")
    assert log.approvers(digest) == ("alice",)


def test_the_log_round_trips_through_json(tmp_path):
    log = ApprovalRegistry()
    digest = assumptions_digest(basis())
    log.approve(digest, "alice", note="ok")
    log.revoke(digest, "alice")
    path = tmp_path / "approvals.json"
    log.to_json(path)
    back = ApprovalRegistry.from_json(path)
    assert back.entries == log.entries
    assert json.loads(path.read_text())[0]["approver"] == "alice"


def test_an_approval_of_nothing_is_refused():
    with pytest.raises(ValueError, match="assumption digest"):
        Approval(assumptions_digest="", approver="alice")
    with pytest.raises(ValueError, match="approver"):
        Approval(assumptions_digest="abc", approver="")
    with pytest.raises(ValueError, match="unknown approval action"):
        Approval(assumptions_digest="abc", approver="a", action="maybe")


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.auth import mint_token, token_digest  # noqa: E402

TOKENS = {name: mint_token() for name in ("submitter", "approver", "both")}
SPEC = {"principals": [
    {"name": "submitter", "token_sha256": token_digest(TOKENS["submitter"]),
     "roles": ["viewer", "runner"]},
    {"name": "approver", "token_sha256": token_digest(TOKENS["approver"]),
     "roles": ["viewer", "approver"]},
    {"name": "both", "token_sha256": token_digest(TOKENS["both"]),
     "roles": ["viewer", "runner", "approver"]},
]}
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


def head(name: str) -> dict:
    return {"Authorization": f"Bearer {TOKENS[name]}"}


@pytest.fixture
def approved_app():
    from engine.api import create_app

    with TestClient(create_app(max_workers=1, principals=SPEC,
                               require_approval=True)) as client:
        yield client


def digest_of(client, request=REQUEST) -> str:
    return client.post("/assumptions/digest", json=request["assumptions"],
                       headers=head("submitter")).json()["assumptions_digest"]


def test_approved_mode_refuses_an_unapproved_basis_and_names_it(approved_app):
    response = approved_app.post("/runs", json=REQUEST, headers=head("submitter"))
    assert response.status_code == 403
    digest = digest_of(approved_app)
    assert digest in response.json()["detail"]
    assert "not approved" in response.json()["detail"]


def test_the_digest_route_is_how_a_submitter_gets_unstuck(approved_app):
    body = approved_app.post("/assumptions/digest", json=REQUEST["assumptions"],
                             headers=head("submitter")).json()
    assert body["approved"] is False
    assert body["approvers"] == []
    # The same basis written with the keys in another order is the same
    # basis — the digest is structural.
    reordered = dict(reversed(list(REQUEST["assumptions"].items())))
    other = approved_app.post("/assumptions/digest", json=reordered,
                              headers=head("submitter")).json()
    assert other["assumptions_digest"] == body["assumptions_digest"]


def test_an_approval_lets_the_run_through_and_says_who_signed(approved_app):
    digest = digest_of(approved_app)
    created = approved_app.post(f"/approvals/{digest}", json={"note": "checked"},
                                headers=head("approver"))
    assert created.status_code == 201
    assert created.json()["approver"] == "approver"

    submitted = approved_app.post("/runs", json=REQUEST,
                                  headers=head("submitter"))
    assert submitted.status_code == 202
    assert submitted.json()["approved_by"] == ["approver"]


def test_the_approver_cannot_be_supplied_in_the_body(approved_app):
    """An approval whose signatory is a request field is forgeable."""
    digest = digest_of(approved_app)
    created = approved_app.post(f"/approvals/{digest}",
                                json={"approver": "somebody else"},
                                headers=head("approver"))
    assert created.json()["approver"] == "approver"


def test_approving_needs_the_approver_role(approved_app):
    digest = digest_of(approved_app)
    assert approved_app.post(f"/approvals/{digest}", json={},
                             headers=head("submitter")).status_code == 403
    assert approved_app.post(f"/approvals/{digest}", json={},
                             headers=None).status_code == 401


def test_a_principal_who_can_do_both_still_cannot_be_both(approved_app):
    """The whole point: the roles are separable, and holding both does not
    let one person close the loop alone."""
    digest = digest_of(approved_app)
    approved_app.post(f"/approvals/{digest}", json={}, headers=head("both"))
    refused = approved_app.post("/runs", json=REQUEST, headers=head("both"))
    assert refused.status_code == 403
    assert "approved only by both" in refused.json()["detail"]
    # Somebody else submitting the same basis is fine.
    assert approved_app.post("/runs", json=REQUEST,
                             headers=head("submitter")).status_code == 202


def test_a_revoked_approval_stops_the_next_run(approved_app):
    digest = digest_of(approved_app)
    approved_app.post(f"/approvals/{digest}", json={}, headers=head("approver"))
    assert approved_app.post("/runs", json=REQUEST,
                             headers=head("submitter")).status_code == 202
    assert approved_app.delete(f"/approvals/{digest}",
                               headers=head("approver")).status_code == 200
    refused = approved_app.post("/runs", json=REQUEST, headers=head("submitter"))
    assert refused.status_code == 403
    assert approved_app.get(f"/approvals/{digest}",
                            headers=head("submitter")).json()["approved"] is False


def test_you_cannot_revoke_an_approval_you_do_not_have(approved_app):
    digest = digest_of(approved_app)
    assert approved_app.delete(f"/approvals/{digest}",
                               headers=head("approver")).status_code == 404


def test_the_history_shows_both_actions(approved_app):
    digest = digest_of(approved_app)
    approved_app.post(f"/approvals/{digest}", json={"note": "first"},
                      headers=head("approver"))
    approved_app.delete(f"/approvals/{digest}", headers=head("approver"))
    body = approved_app.get(f"/approvals/{digest}",
                            headers=head("submitter")).json()
    assert [entry["action"] for entry in body["history"]] == ["approve",
                                                              "revoke"]
    assert body["history"][0]["note"] == "first"
    listing = approved_app.get("/approvals", headers=head("submitter")).json()
    assert listing["require_approval"] is True
    assert listing["approvals"][0]["assumptions_digest"] == digest


def test_approved_mode_without_identity_refuses_to_start():
    """Four-eyes over anonymous callers is one pair of eyes with extra
    steps, and a deployment that asked for it has misconfigured something."""
    from engine.api import create_app

    with pytest.raises(ValueError, match="needs principals"):
        create_app(require_approval=True)


def test_without_approved_mode_nothing_changes():
    from engine.api import create_app

    with TestClient(create_app(max_workers=1)) as client:
        assert client.post("/runs", json=REQUEST).status_code == 202
        assert "approved_by" not in client.post("/runs", json=REQUEST).json()
        # And a deployment that records no approvals says so rather than
        # answering with an empty list.
        assert client.get("/approvals").status_code == 404


def test_an_approval_log_can_be_a_file(tmp_path):
    from engine.api import create_app

    path = tmp_path / "approvals.json"
    with TestClient(create_app(max_workers=1, principals=SPEC,
                               approvals=path, require_approval=True)) as client:
        digest = digest_of(client)
        client.post(f"/approvals/{digest}", json={"note": "on disk"},
                    headers=head("approver"))
        assert path.is_file()
        assert json.loads(path.read_text())[0]["note"] == "on disk"

    # Reloaded, the approval is still in force.
    with TestClient(create_app(max_workers=1, principals=SPEC,
                               approvals=path, require_approval=True)) as client:
        assert client.post("/runs", json=REQUEST,
                           headers=head("submitter")).status_code == 202
