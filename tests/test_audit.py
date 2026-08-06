"""The audit chain and the run calendar.

RFC-045. Two halves, and both are tested against the claim they make rather
than the feature they are:

- **The chain catches an edit and cannot catch a deletion.** Both are
  asserted, the second one deliberately — a log that was believed to be
  tamper-proof would be trusted further than it can carry, so the test suite
  says out loud that a truncated log still verifies and that the head digest
  is the only thing that catches it.
- **A scheduled run is a frozen question.** The calendar carries the request
  and its fingerprint, so an edited request is detectable; and the due
  window is half-open, because a polling scheduler's two failure modes are
  missing a minute and running it twice.
"""

import json
from datetime import datetime, timezone

import pytest

from engine.core.audit import GENESIS, AuditChainError, AuditEvent, AuditLog
from engine.core.schedule import (
    CronError,
    DriftedScheduleError,
    RunCalendar,
    ScheduledRun,
    cron_matches,
    parse_cron,
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


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------

def test_the_chain_links_every_entry_to_the_one_before():
    log = AuditLog()
    assert log.head == GENESIS
    first = log.append("alice", "run.submit", "run-1", {"model": "TermLife"})
    second = log.append("bob", "assumptions.approve", "abc")
    assert first.previous == GENESIS
    assert second.previous == first.digest
    assert log.head == second.digest
    assert log.verify()


def test_an_edited_entry_breaks_the_chain_and_is_named():
    log = AuditLog()
    log.append("alice", "run.submit", "run-1")
    log.append("bob", "assumptions.approve", "abc")
    log.append("alice", "run.submit", "run-2")

    forged = AuditEvent(**{**log.events[1].to_dict(), "actor": "mallory"})
    tampered = AuditLog([log.events[0], forged, log.events[2]])
    with pytest.raises(AuditChainError, match="entry 1 .* has been edited"):
        tampered.verify()


def test_an_edited_timestamp_breaks_the_chain_too():
    """An audit entry whose time could move is an entry about a different
    event."""
    log = AuditLog()
    log.append("alice", "run.submit", "run-1")
    moved = AuditEvent(**{**log.events[0].to_dict(),
                          "at": "2020-01-01T00:00:00+00:00"})
    with pytest.raises(AuditChainError, match="has been edited"):
        AuditLog([moved]).verify()


def test_a_reordered_log_is_caught():
    log = AuditLog()
    log.append("alice", "a")
    log.append("bob", "b")
    with pytest.raises(AuditChainError, match="reordered"):
        AuditLog(list(reversed(log.events))).verify()


def test_a_relinked_entry_is_caught():
    log = AuditLog()
    log.append("alice", "a")
    log.append("bob", "b")
    relinked = AuditEvent(**{**log.events[1].to_dict(), "previous": GENESIS})
    with pytest.raises(AuditChainError, match="links to"):
        AuditLog([log.events[0], relinked]).verify()


def test_a_truncated_log_still_verifies_and_that_is_the_honest_limit():
    """Stated rather than papered over: a hash chain proves content and
    order, not completeness. Dropping the tail leaves a consistent prefix,
    and only a head digest published elsewhere catches it."""
    log = AuditLog()
    for i in range(5):
        log.append("alice", "run.submit", f"run-{i}")
    published_head = log.head

    truncated = AuditLog(log.events[:3])
    assert truncated.verify()                 # the prefix is internally sound
    assert truncated.head != published_head   # and the anchor is what tells


def test_the_log_round_trips_and_verifies_on_load(tmp_path):
    log = AuditLog()
    log.append("alice", "run.submit", "run-1", {"model": "TermLife"})
    log.append("bob", "assumptions.approve", "abc", {"note": "checked"})
    path = tmp_path / "audit.json"
    log.to_json(path)
    back = AuditLog.from_json(path)
    assert back.events == log.events
    assert back.head == log.head

    rows = json.loads(path.read_text())
    rows[0]["actor"] = "mallory"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(AuditChainError):
        AuditLog.from_json(path)


def test_an_entry_with_no_actor_or_action_is_refused():
    log = AuditLog()
    with pytest.raises(ValueError, match="actor"):
        log.append("", "run.submit")
    with pytest.raises(ValueError, match="action"):
        log.append("alice", "")


def test_queries_by_actor_and_action():
    log = AuditLog()
    log.append("alice", "run.submit", "1")
    log.append("bob", "assumptions.approve", "abc")
    log.append("alice", "run.submit", "2")
    assert len(log.by_actor("alice")) == 2
    assert len(log.of_action("assumptions.approve")) == 1
    assert log.summary()["entries"] == 3


# --------------------------------------------------------------------------
# Cron
# --------------------------------------------------------------------------

@pytest.mark.parametrize("expression,when,expected", [
    ("0 3 * * *", utc(2026, 1, 15, 3, 0), True),
    ("0 3 * * *", utc(2026, 1, 15, 3, 1), False),
    ("*/15 * * * *", utc(2026, 1, 15, 9, 30), True),
    ("*/15 * * * *", utc(2026, 1, 15, 9, 31), False),
    ("30 2 1 * *", utc(2026, 3, 1, 2, 30), True),
    ("30 2 1 * *", utc(2026, 3, 2, 2, 30), False),
    ("0 0 * * 1", utc(2026, 1, 5, 0, 0), True),      # a Monday
    ("0 0 * * 1", utc(2026, 1, 6, 0, 0), False),
    ("0 0 * * 0", utc(2026, 1, 4, 0, 0), True),      # cron's Sunday is 0
    ("0 9-17 * * 1-5", utc(2026, 1, 6, 12, 0), True),
    ("0 9-17 * * 1-5", utc(2026, 1, 6, 18, 0), False),
    ("0 0 1,15 * *", utc(2026, 1, 15, 0, 0), True),
    ("0 0 1,15 * *", utc(2026, 1, 16, 0, 0), False),
])
def test_cron_matches(expression, when, expected):
    assert cron_matches(expression, when) is expected


def test_day_of_month_and_weekday_are_ored():
    """The convention every implementation follows and nobody expects."""
    assert cron_matches("0 0 1 * 1", utc(2026, 2, 1, 0, 0))   # the 1st, Sunday
    assert cron_matches("0 0 1 * 1", utc(2026, 2, 2, 0, 0))   # a Monday
    assert not cron_matches("0 0 1 * 1", utc(2026, 2, 3, 0, 0))


@pytest.mark.parametrize("expression,message", [
    ("0 3 * *", "five fields"),
    ("0 3 * * * *", "five fields"),
    ("60 3 * * *", "outside 0..59"),
    ("0 24 * * *", "outside 0..23"),
    ("0 3 * * MON", "cannot read"),
    ("0 3 * * *,", "cannot read"),
    ("*/0 3 * * *", "bad step"),
    ("0 5-3 * * *", "runs backwards"),
])
def test_an_unreadable_cron_expression_raises(expression, message):
    """A schedule that silently means something else is a run on the wrong
    day."""
    with pytest.raises(CronError, match=message):
        parse_cron(expression)


# --------------------------------------------------------------------------
# The calendar
# --------------------------------------------------------------------------

def test_a_scheduled_run_freezes_its_request():
    run = ScheduledRun(name="month-end", cron="0 3 1 * *", request=REQUEST)
    assert run.request_fingerprint
    assert run.verify()

    edited = ScheduledRun(name="month-end", cron="0 3 1 * *",
                          request={**REQUEST, "proj_len": 6},
                          request_fingerprint=run.request_fingerprint)
    assert not edited.verify()
    with pytest.raises(DriftedScheduleError, match="month-end"):
        RunCalendar([edited]).verify()


def test_a_scheduled_run_refuses_an_unreadable_schedule():
    with pytest.raises(CronError):
        ScheduledRun(name="bad", cron="every friday", request=REQUEST)
    with pytest.raises(ValueError, match="needs a name"):
        ScheduledRun(name="", cron="0 3 * * *", request=REQUEST)
    with pytest.raises(ValueError, match="needs a request"):
        ScheduledRun(name="empty", cron="0 3 * * *", request={})


def test_the_due_window_is_half_open_on_the_left():
    """A worker passes its last wake-up time, so a minute is neither missed
    nor run twice."""
    calendar = RunCalendar([ScheduledRun(name="hourly", cron="0 * * * *",
                                         request=REQUEST)])
    at_ten = utc(2026, 1, 15, 10, 0)
    assert calendar.due(at_ten, at_ten) == []               # already ran
    fired = calendar.due(utc(2026, 1, 15, 9, 0), at_ten)
    assert [when for when, _ in fired] == [at_ten]
    caught_up = calendar.due(utc(2026, 1, 15, 7, 0), at_ten)
    assert len(caught_up) == 3                              # 8, 9 and 10


def test_a_disabled_entry_does_not_fire():
    calendar = RunCalendar([ScheduledRun(name="off", cron="* * * * *",
                                         request=REQUEST, enabled=False)])
    assert calendar.due(utc(2026, 1, 15, 9, 0),
                        utc(2026, 1, 15, 9, 5)) == []


def test_the_calendar_round_trips_and_checks_itself(tmp_path):
    calendar = RunCalendar()
    calendar.add(ScheduledRun(name="month-end", cron="0 3 1 * *",
                              request=REQUEST, note="statutory close"))
    with pytest.raises(ValueError, match="duplicate"):
        calendar.add(ScheduledRun(name="month-end", cron="0 4 1 * *",
                                  request=REQUEST))
    path = tmp_path / "calendar.json"
    calendar.to_json(path)
    assert RunCalendar.from_json(path).get("month-end").note == \
        "statutory close"

    rows = json.loads(path.read_text())
    rows[0]["request"]["proj_len"] = 99
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(DriftedScheduleError):
        RunCalendar.from_json(path)


def test_a_backwards_window_raises():
    calendar = RunCalendar()
    with pytest.raises(ValueError, match="ends before it starts"):
        calendar.due(utc(2026, 1, 2), utc(2026, 1, 1))


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.auth import mint_token, token_digest  # noqa: E402

TOKENS = {name: mint_token() for name in ("operator", "approver")}
SPEC = {"principals": [
    {"name": "operator", "token_sha256": token_digest(TOKENS["operator"]),
     "roles": ["viewer", "runner", "admin"]},
    {"name": "approver", "token_sha256": token_digest(TOKENS["approver"]),
     "roles": ["viewer", "approver"]},
]}


def head(name: str) -> dict:
    return {"Authorization": f"Bearer {TOKENS[name]}"}


@pytest.fixture
def audited():
    from engine.api import create_app

    log = AuditLog()
    with TestClient(create_app(max_workers=1, principals=SPEC, audit=log,
                               require_approval=True)) as client:
        yield client, log


def test_every_mutation_is_recorded_and_reads_are_not(audited):
    client, log = audited
    digest = client.post("/assumptions/digest", json=REQUEST["assumptions"],
                         headers=head("operator")).json()["assumptions_digest"]
    client.get("/models", headers=head("operator"))
    client.get("/runs", headers=head("operator"))
    assert len(log) == 0                       # reads leave no trace

    client.post(f"/approvals/{digest}", json={"note": "checked"},
                headers=head("approver"))
    client.post("/runs", json=REQUEST, headers=head("operator"))
    client.delete(f"/approvals/{digest}", headers=head("approver"))

    actions = [e.action for e in log]
    assert actions == ["assumptions.approve", "run.submit",
                       "assumptions.revoke"]
    assert [e.actor for e in log] == ["approver", "operator", "approver"]
    assert log.events[1].detail["assumptions_digest"] == digest
    assert log.events[1].detail["approved_by"] == ["approver"]
    assert log.verify()


def test_the_audit_route_publishes_the_head_and_needs_admin(audited):
    client, log = audited
    log.append("someone", "run.submit", "run-1")
    body = client.get("/audit", headers=head("operator")).json()
    assert body["total"] == 1
    assert body["head"] == log.head
    assert body["verified"] is True
    assert client.get("/audit", headers=head("approver")).status_code == 403


def test_a_deployment_with_no_audit_log_says_so():
    from engine.api import create_app

    with TestClient(create_app(max_workers=1)) as client:
        assert client.get("/audit").status_code == 404


def test_the_audit_log_survives_a_restart(tmp_path):
    from engine.api import create_app

    path = tmp_path / "audit.json"
    with TestClient(create_app(max_workers=1, principals=SPEC,
                               audit=path)) as client:
        client.post("/runs", json=REQUEST, headers=head("operator"))
    assert path.is_file()

    with TestClient(create_app(max_workers=1, principals=SPEC,
                               audit=path)) as client:
        client.post("/runs", json={**REQUEST, "proj_len": 6},
                    headers=head("operator"))
        body = client.get("/audit", headers=head("operator")).json()
    assert body["total"] == 2
    assert body["verified"] is True
    assert AuditLog.from_json(path).verify()


def test_the_evidence_pack_anchors_the_head():
    """RFC-049 is where a head digest gets published, which is what makes
    the deletion case detectable at all."""
    from engine.report.evidence import build_pack

    log = AuditLog()
    log.append("alice", "run.submit", "run-1")
    section = build_pack(specimens=[], collect_tests=False,
                         audit_log=log).section("audit")
    assert section.content["head"] == log.head
    assert section.content["verified"] is True
    assert section.content["anchored"] is True
    assert log.head in section.summary

    from engine.report.evidence import audit_chain

    unanchored = audit_chain(None)
    # Nothing to anchor is not the same as a section that could not be
    # built: a deployment with no audit log still gets a complete pack.
    assert unanchored.content["available"] is True
    assert unanchored.content["anchored"] is False
