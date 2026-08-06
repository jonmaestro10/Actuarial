# RFC-045: The chain that catches an edit, and the deletion it cannot

Status: **implemented** — `engine/core/audit.py`, `engine/core/schedule.py`,
`engine/api/app.py`, `scripts/run_calendar.py`, `tests/test_audit.py`

## Summary

Execution plan §6, item D3, completing the governance workstream:

> Append-only, digest-chained audit log of API mutations (submit, approve,
> principal change) — same tamper-evidence discipline as the registry. A
> production run calendar: scheduled runs defined declaratively (cron
> expression + frozen request fingerprint) executed by a worker script, not
> by core.

`create_app(audit=...)` records every mutation; `GET /audit` (admin) returns
the entries, the head digest and a verification result. `RunCalendar` holds
scheduled runs and `scripts/run_calendar.py` executes what is due.

## What the chain promises, and the sentence that has to be in the docs

Each entry carries the digest of the one before it, and its own digest
covers everything including the timestamp and the link. Edit an old entry —
the actor, the note, the time — and its digest changes, every link after it
breaks, and `verify()` names the first entry that does not hold. That is the
same discipline the run registry uses, applied to a sequence instead of to a
pair.

It does not prove completeness. Anybody who can rewrite the file can delete
the last five entries, and the surviving prefix verifies perfectly, because
a prefix of a valid chain is a valid chain. There is no clever fix inside
the file: detecting truncation requires a value recorded somewhere the
editor does not control.

So the limit is stated in the module docstring, asserted in a test that
truncates a log and checks that it *still verifies*, and answered where it
can be: `AuditLog.head` is exposed, `GET /audit` returns it, and the
evidence pack (RFC-049) grew an audit section that publishes it. A pack is
content-addressed and dated, so a head recorded in one is a head a later log
can be held against. "The chain catches edits; publishing the head catches
deletions" is the whole claim, and a log that implied more would be trusted
further than it can carry — which is worse than one that claimed nothing.

Two smaller decisions follow the same instinct. **Reads are not recorded**:
a log that records everything is a log nobody reads, and the questions an
operations review asks are about changes. And the actor on a deployment with
no principals is `anonymous` — honest rather than useful, and the reason
this RFC says an audit log without RFC-043 records what happened but not who
did it.

## A scheduled job is a name for something that moves

Every incumbent scheduler runs "the month-end job" on a cron. What that job
*does* is wherever it points — a model version, a basis, a data pull — so
the schedule can stay untouched for a year while what it runs changes
underneath.

A `ScheduledRun` carries the request itself and `fingerprint(request)`
beside it. The calendar therefore names an exact question: change anything
about it and the fingerprint no longer matches, `verify()` says so, and the
worker refuses the entry rather than running it. A determined editor can of
course update both — the guard is not against an adversary, it is against
the ordinary case where somebody edits a request and nobody notices that the
scheduled question changed. The refusal is recorded as `calendar.drift` in
the audit log, so the missed run is visible rather than silent.

The cron parser is five fields, `*`, lists, ranges and steps, forty lines
and no dependency, and it *raises* on anything it does not fully understand
— `MON` for Monday, a backwards range, a zero step. A schedule that silently
means something other than what it says is a production run on the wrong
day. Times are UTC, stated once here rather than discovered at the March
clock change; day-of-month and day-of-week are ORed when both are
restricted, which is what every cron does and what nobody expects.

The due window is **half-open on the left**: `due(since, now]`. A worker
passes its own last wake-up time, so the two failure modes of every polling
scheduler — missing a minute at the boundary and running one twice — are
both closed, and a worker that was down for an hour catches up rather than
skipping. The watermark is a file the script updates only after a clean
pass.

There is no thread in `engine/core`. The calendar answers what is due; the
script decides to run it. That keeps the engine a library, and it keeps the
operational loop somewhere an operations team can read it — under whatever
already wakes things up in that estate, because adding a second scheduler
underneath the first is how a run happens twice.

## What is next

Milestone M3 needs E1, the results warehouse. G2's SOC 2 substrate is now
mostly a matter of joining up what exists: the registry for integrity, D1
for access, this log for audit, and F1 to generate the binder.
