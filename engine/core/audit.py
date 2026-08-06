"""The audit log, and what a hash chain can and cannot promise.

RFC-045. The run registry says what was computed; the approval log says who
signed for what. Neither says *what happened, in what order* — who submitted
which run at which time, who approved a basis and who took it back. That
sequence is the artifact an operations review asks for, and it is the one
thing in this repo that a person with write access to a JSON file could
previously have edited without leaving a mark.

So each entry carries the digest of the one before it, exactly as the run
registry digests inputs against answers. Changing an old entry changes its
digest, which breaks every link after it, and :meth:`AuditLog.verify` names
the first entry where the chain parts.

**What that does not promise, stated plainly.** A hash chain proves
*content* and *order*. It does not prove *completeness*: anybody who can
rewrite the file can drop the last five entries and re-chain nothing,
because the surviving prefix is still internally consistent. Truncation is
detectable only against a value published somewhere the attacker does not
control — so :attr:`AuditLog.head` is exposed, the evidence pack (RFC-049)
records it, and the honest posture is "the chain catches edits; publishing
the head catches deletions". A log that claimed more than that would be
worse than one that claimed nothing, because it would be trusted more.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from engine.core.fingerprint import fingerprint

#: The digest an empty log's next entry links to.
GENESIS = "0" * 32


class AuditChainError(RuntimeError):
    """The log does not hash to what it says it does."""


@dataclass(frozen=True)
class AuditEvent:
    """One thing that happened, chained to the thing before it."""

    seq: int
    at: str
    actor: str
    action: str
    subject: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    previous: str = GENESIS
    digest: str = ""

    def computed_digest(self) -> str:
        """The digest this entry's content implies.

        Everything is inside it, including the timestamp and the link: an
        audit entry whose *time* could be edited without breaking the chain
        would be an audit entry about a different event.
        """
        return fingerprint({
            "seq": self.seq, "at": self.at, "actor": self.actor,
            "action": self.action, "subject": self.subject,
            "detail": dict(self.detail), "previous": self.previous,
        })

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> "AuditEvent":
        return cls(**dict(record))

    def __fingerprint__(self):
        return self.to_dict()


class AuditLog:
    """An append-only, digest-chained record of what was done.

    Deliberately not a database, per PLAN §2.4: a list, a JSON file, and one
    operation worth having — :meth:`verify`.
    """

    def __init__(self, events: Iterable[AuditEvent] = ()):
        self._events: list[AuditEvent] = list(events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    @property
    def head(self) -> str:
        """The digest of the last entry — the value worth publishing."""
        return self._events[-1].digest if self._events else GENESIS

    def append(self, actor: str, action: str, subject: str = "",
               detail: Mapping[str, Any] | None = None,
               at: str | None = None) -> AuditEvent:
        """Record an event and return it."""
        if not actor:
            raise ValueError("an audit entry needs an actor")
        if not action:
            raise ValueError("an audit entry needs an action")
        event = AuditEvent(
            seq=len(self._events),
            at=at or datetime.now(timezone.utc).isoformat(),
            actor=actor, action=action, subject=subject,
            detail=dict(detail or {}), previous=self.head,
        )
        event = AuditEvent(**{**event.to_dict(),
                              "digest": event.computed_digest()})
        self._events.append(event)
        return event

    def verify(self) -> bool:
        """Recompute every digest and every link, or raise.

        Returns ``True`` so it reads as an assertion at a call site; the
        failure mode is the exception, which names the first entry that does
        not hold, because "the log is corrupt" is not an actionable thing to
        tell somebody.
        """
        previous = GENESIS
        for i, event in enumerate(self._events):
            if event.seq != i:
                raise AuditChainError(
                    f"entry {i} is numbered {event.seq}: the log has been "
                    f"reordered or an entry is missing"
                )
            if event.previous != previous:
                raise AuditChainError(
                    f"entry {i} links to {event.previous} but the previous "
                    f"entry digests to {previous}"
                )
            expected = event.computed_digest()
            if event.digest != expected:
                raise AuditChainError(
                    f"entry {i} ({event.action} by {event.actor}) carries "
                    f"digest {event.digest} and hashes to {expected}: it has "
                    f"been edited"
                )
            previous = event.digest
        return True

    def by_actor(self, actor: str) -> list[AuditEvent]:
        return [e for e in self._events if e.actor == actor]

    def of_action(self, action: str) -> list[AuditEvent]:
        return [e for e in self._events if e.action == action]

    def to_json(self, path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([e.to_dict() for e in self._events], handle, indent=2)

    @classmethod
    def from_json(cls, path) -> "AuditLog":
        """Load a log and verify it before handing it back.

        A log that is loaded without being checked is a log nobody checks:
        the natural moment is the one where somebody is about to rely on it.
        """
        with open(path, encoding="utf-8") as handle:
            log = cls(AuditEvent.from_dict(row) for row in json.load(handle))
        log.verify()
        return log

    def summary(self) -> dict:
        return {"entries": len(self._events), "head": self.head,
                "verified": self.verify()}
