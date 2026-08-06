"""The run calendar: scheduled runs as frozen questions.

RFC-045. A production actuarial function runs the same things every month
end, and every incumbent's scheduler expresses that as "run job X on this
cron". The trouble with a job is that what it *does* is wherever it points —
a model version, a basis, a data pull — so "the month-end job" is a name for
something that changes without the schedule changing.

A :class:`ScheduledRun` carries the request itself and its fingerprint. The
schedule therefore names an exact question rather than a job: if anything
about the run moves, the fingerprint moves with it, and the calendar entry
is visibly a different entry rather than the same one behaving differently.
:meth:`ScheduledRun.verify` is the check, and the worker refuses an entry
whose frozen fingerprint no longer matches the request beside it — not
because a determined editor could not update both, but because the common
failure is somebody editing the request and nobody noticing.

**Cron parsing, and why it is here.** Five fields, ``*``, lists, ranges and
steps: forty lines, no dependency, and every deployment already knows the
syntax. Anything richer — timezones with a DST rule, ``@reboot``, seconds —
is a scheduler, and a scheduler is a thing to install rather than a thing to
write. Times are UTC, stated once here rather than discovered at the March
clock change.

**Core defines it; core does not run it.** There is no thread here. The
calendar answers "what is due between these two instants", and
``scripts/run_calendar.py`` is the worker that asks — which keeps the engine
a library and keeps the operational half where an operations team can see
it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from engine.core.fingerprint import fingerprint

FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
FIELD_NAMES = ("minute", "hour", "day of month", "month", "day of week")


class CronError(ValueError):
    """A cron expression this parser will not guess at."""


def parse_cron(expression: str) -> tuple[frozenset[int], ...]:
    """Five fields to five sets of permitted values.

    Raises rather than accepting anything it does not fully understand: a
    schedule that silently means something other than what it says is a
    production run that happens on the wrong day.
    """
    parts = expression.split()
    if len(parts) != 5:
        raise CronError(
            f"cron needs five fields (minute hour day month weekday), got "
            f"{len(parts)}: {expression!r}"
        )
    out = []
    for part, (low, high), name in zip(parts, FIELD_RANGES, FIELD_NAMES):
        values: set[int] = set()
        for piece in part.split(","):
            step = 1
            if "/" in piece:
                piece, _, raw_step = piece.partition("/")
                if not raw_step.isdigit() or int(raw_step) < 1:
                    raise CronError(f"{name}: bad step in {part!r}")
                step = int(raw_step)
            if piece == "*":
                start, stop = low, high
            elif "-" in piece.lstrip("-"):
                start_text, _, stop_text = piece.partition("-")
                if not (start_text.isdigit() and stop_text.isdigit()):
                    raise CronError(f"{name}: bad range in {part!r}")
                start, stop = int(start_text), int(stop_text)
            elif piece.isdigit():
                start = stop = int(piece)
            else:
                raise CronError(f"{name}: cannot read {piece!r} in {part!r}")
            if not (low <= start <= high and low <= stop <= high):
                raise CronError(
                    f"{name}: {piece!r} is outside {low}..{high}"
                )
            if stop < start:
                raise CronError(f"{name}: range {piece!r} runs backwards")
            values.update(range(start, stop + 1, step))
        out.append(frozenset(values))
    return tuple(out)


def cron_matches(expression: str, when: datetime) -> bool:
    """Does ``when`` (UTC, to the minute) satisfy the expression?

    Day-of-month and day-of-week are ORed when both are restricted, which is
    the convention every cron implementation follows and the one nobody
    expects until they read it.
    """
    minutes, hours, doms, months, dows = parse_cron(expression)
    if when.minute not in minutes or when.hour not in hours:
        return False
    if when.month not in months:
        return False
    dom_restricted = len(doms) < 31
    dow_restricted = len(dows) < 7
    # cron numbers Sunday 0; Python numbers Monday 0.
    weekday = (when.weekday() + 1) % 7
    if dom_restricted and dow_restricted:
        return when.day in doms or weekday in dows
    if dom_restricted:
        return when.day in doms
    if dow_restricted:
        return weekday in dows
    return True


@dataclass(frozen=True)
class ScheduledRun:
    """One recurring question, frozen.

    ``request_fingerprint`` is computed from ``request`` when the entry is
    built and stored beside it, so a calendar file is self-checking.
    """

    name: str
    cron: str
    request: Mapping[str, Any]
    request_fingerprint: str = ""
    enabled: bool = True
    note: str = ""

    def __post_init__(self):
        if not self.name:
            raise ValueError("a scheduled run needs a name")
        parse_cron(self.cron)          # refuse an unreadable schedule here
        if not self.request:
            raise ValueError(f"{self.name}: a scheduled run needs a request")
        if not self.request_fingerprint:
            object.__setattr__(self, "request_fingerprint",
                               fingerprint(dict(self.request)))

    def verify(self) -> bool:
        """Is the request still the one somebody scheduled?"""
        return fingerprint(dict(self.request)) == self.request_fingerprint

    def matches(self, when: datetime) -> bool:
        return self.enabled and cron_matches(self.cron, when)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> "ScheduledRun":
        return cls(**dict(record))

    def __fingerprint__(self):
        return self.to_dict()


class DriftedScheduleError(ValueError):
    """A calendar entry whose request no longer matches its fingerprint."""


@dataclass
class RunCalendar:
    """The scheduled runs a deployment carries."""

    runs: list[ScheduledRun] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.runs)

    def __iter__(self):
        return iter(self.runs)

    def add(self, run: ScheduledRun) -> ScheduledRun:
        if any(existing.name == run.name for existing in self.runs):
            raise ValueError(f"duplicate scheduled run {run.name!r}")
        self.runs.append(run)
        return run

    def get(self, name: str) -> ScheduledRun | None:
        for run in self.runs:
            if run.name == name:
                return run
        return None

    def due(self, start: datetime, end: datetime) -> list[tuple[datetime,
                                                                ScheduledRun]]:
        """Every (minute, run) due in ``(start, end]``, in time order.

        A half-open window on the left so a worker can pass its previous
        wake-up time and neither miss a minute nor run one twice — the two
        failure modes of every polling scheduler ever written.
        """
        if end < start:
            raise ValueError("the window ends before it starts")
        fired: list[tuple[datetime, ScheduledRun]] = []
        when = (start.replace(second=0, microsecond=0, tzinfo=timezone.utc)
                + timedelta(minutes=1))
        end = end.replace(second=0, microsecond=0, tzinfo=timezone.utc)
        while when <= end:
            for run in self.runs:
                if run.matches(when):
                    fired.append((when, run))
            when += timedelta(minutes=1)
        return fired

    def verify(self) -> bool:
        """Every entry still asks the question it was written to ask."""
        drifted = [run.name for run in self.runs if not run.verify()]
        if drifted:
            raise DriftedScheduleError(
                f"scheduled run(s) {drifted} carry a request that no longer "
                f"matches their frozen fingerprint"
            )
        return True

    def to_json(self, path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([run.to_dict() for run in self.runs], handle, indent=2)

    @classmethod
    def from_json(cls, path) -> "RunCalendar":
        with open(path, encoding="utf-8") as handle:
            calendar = cls([ScheduledRun.from_dict(row)
                            for row in json.load(handle)])
        calendar.verify()
        return calendar

    @classmethod
    def from_entries(cls, entries: Iterable[Mapping[str, Any]]) -> "RunCalendar":
        return cls([ScheduledRun.from_dict(dict(entry)) for entry in entries])
