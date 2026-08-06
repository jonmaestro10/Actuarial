"""Execute the run calendar — RFC-045.

The scheduler half of D3, deliberately outside the engine: core defines what
is due and this decides to run it, so the operational loop lives where an
operations team can read it::

    python scripts/run_calendar.py --calendar calendar.json --since-file .last
    python scripts/run_calendar.py --calendar calendar.json --now 2026-01-31T23:00

One shot per invocation, no daemon: the thing that wakes this up is
whatever already wakes up the rest of the estate — systemd timer, Airflow,
Kubernetes CronJob — and adding a second scheduler underneath the first is
how a run happens twice.

The window is ``(since, now]``, and ``--since-file`` is where the last
watermark is kept, so a worker that was down for an hour catches up rather
than skipping. Each due entry is checked against its frozen fingerprint
before it runs: the common failure is somebody editing the request in the
calendar file and nobody noticing that the scheduled question changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.api.catalogue import builder, catalogue  # noqa: E402
from engine.core.audit import AuditLog  # noqa: E402
from engine.core.registry import RunRegistry, record_run  # noqa: E402
from engine.core.schedule import RunCalendar  # noqa: E402


def parse_when(text: str | None) -> datetime:
    if text is None:
        return datetime.now(timezone.utc)
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", required=True,
                        help="JSON list of scheduled runs")
    parser.add_argument("--now", default=None,
                        help="pretend it is this time (ISO 8601, UTC)")
    parser.add_argument("--since", default=None,
                        help="start of the window; defaults to one minute "
                             "before --now, or the watermark file")
    parser.add_argument("--since-file", default=None,
                        help="file holding the last watermark, updated after "
                             "a successful pass")
    parser.add_argument("--registry", default=None,
                        help="run registry JSON to append to")
    parser.add_argument("--audit", default=None,
                        help="audit log JSON to append to")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what is due and run nothing")
    args = parser.parse_args()

    calendar = RunCalendar.from_json(args.calendar)
    now = parse_when(args.now)
    watermark = Path(args.since_file) if args.since_file else None
    if args.since:
        since = parse_when(args.since)
    elif watermark is not None and watermark.is_file():
        since = parse_when(watermark.read_text().strip())
    else:
        since = now - timedelta(minutes=1)

    due = calendar.due(since, now)
    print(f"window ({since.isoformat()}, {now.isoformat()}]: "
          f"{len(due)} run(s) due")
    if args.dry_run:
        for when, run in due:
            print(f"  {when.isoformat()}  {run.name}  "
                  f"{run.request_fingerprint}")
        return 0

    registry_path = Path(args.registry) if args.registry else None
    registry = (RunRegistry.from_json(registry_path)
                if registry_path and registry_path.is_file() else RunRegistry())
    audit_path = Path(args.audit) if args.audit else None
    audit = (AuditLog.from_json(audit_path)
             if audit_path and audit_path.is_file() else AuditLog())

    build = builder(catalogue())
    failed = 0
    for when, run in due:
        if not run.verify():
            failed += 1
            print(f"  [DRIFT] {run.name}: the request no longer matches its "
                  f"frozen fingerprint; not run")
            audit.append("calendar", "calendar.drift", run.name,
                         {"scheduled_for": when.isoformat()})
            continue
        try:
            kwargs = build(dict(run.request))
            _, record = record_run(**kwargs)
        except Exception as exc:
            failed += 1
            print(f"  [FAIL ] {run.name}: {type(exc).__name__}: {exc}")
            audit.append("calendar", "calendar.failed", run.name,
                         {"scheduled_for": when.isoformat(),
                          "error": f"{type(exc).__name__}: {exc}"})
            continue
        registry.add(record)
        audit.append("calendar", "calendar.run", run.name,
                     {"scheduled_for": when.isoformat(),
                      "run_id": record.run_id,
                      "results_digest": record.results_digest,
                      "request_fingerprint": run.request_fingerprint})
        print(f"  [OK   ] {run.name}: run {record.run_id} -> "
              f"{record.results_digest}")

    if registry_path is not None:
        registry.to_json(registry_path)
    if audit_path is not None:
        audit.to_json(audit_path)
    if watermark is not None and not failed:
        watermark.write_text(now.isoformat(), encoding="utf-8")
    print(f"audit head: {audit.head}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
