"""Approval bound to content, not to a name.

RFC-044. Every incumbent's assumption-approval workflow approves a *label*:
somebody signs off "2026 Q1 mortality basis", and from then on the label is
approved. What the label points at can move — a corrected table, a rerun
extraction, a fat finger in a spreadsheet — and the approval does not
notice, because the approval was never about the numbers.

Here an approval names a digest. ``fingerprint(assumptions)`` is the same
value :class:`~engine.core.registry.RunRecord` records as
``assumptions_digest``, so an approval says: *this exact content is
approved*. Two consequences fall out and both are the point:

- an identical assumption set, rebuilt from scratch by a different process
  on a different machine, is **still approved** — content addressing means
  re-derivation is free;
- any change at all, however small, is **not approved** — there is no
  version of "basically the same basis" that the check can be talked into.

The module is deliberately in ``engine/core`` and deliberately knows nothing
about HTTP. Four-eyes is a property of a decision, not of a transport; the
API in :mod:`engine.api.approvals` supplies the identities and this decides.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from engine.core.fingerprint import fingerprint


class ApprovalRequired(PermissionError):
    """A run whose assumption set nobody else has approved.

    Carries the digest, because the first thing the submitter needs is the
    string to hand to an approver.
    """

    def __init__(self, message: str, *, digest: str,
                 approvers: tuple[str, ...] = ()):
        super().__init__(message)
        self.digest = digest
        #: Who *has* approved it — non-empty when the only approval is the
        #: submitter's own, which is the interesting refusal.
        self.approvers = approvers


@dataclass(frozen=True)
class Approval:
    """One signature over one content digest.

    ``revoked_by`` is not a field: a revocation is another entry in the log,
    because an append-only record of who approved what and who took it back
    is the artifact a model-risk function actually asks for. Mutating an
    approval in place would leave the interesting half of the history in
    nobody's hands.
    """

    assumptions_digest: str
    approver: str
    note: str = ""
    action: str = "approve"          # approve | revoke
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self):
        if self.action not in ("approve", "revoke"):
            raise ValueError(f"unknown approval action {self.action!r}")
        if not self.assumptions_digest:
            raise ValueError("an approval needs an assumption digest")
        if not self.approver:
            raise ValueError("an approval needs an approver")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> "Approval":
        return cls(**dict(record))

    def __fingerprint__(self):
        return self.to_dict()


class ApprovalRegistry:
    """An append-only log of approvals and revocations.

    The same shape as the run registry, for the same reason: what happened
    is a sequence of events, and the current state is a *query* over it
    rather than a row somebody overwrote.
    """

    def __init__(self, entries: Iterable[Approval] = ()):
        self._entries: list[Approval] = list(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    @property
    def entries(self) -> list[Approval]:
        return list(self._entries)

    def approve(self, assumptions_digest: str, approver: str,
                note: str = "") -> Approval:
        entry = Approval(assumptions_digest=assumptions_digest,
                         approver=approver, note=note, action="approve")
        self._entries.append(entry)
        return entry

    def revoke(self, assumptions_digest: str, approver: str,
               note: str = "") -> Approval:
        """Withdraw ``approver``'s approval of this digest.

        Anyone may revoke their own; revoking somebody else's is not a
        thing this module can express, because a signature is not
        transferable.
        """
        entry = Approval(assumptions_digest=assumptions_digest,
                         approver=approver, note=note, action="revoke")
        self._entries.append(entry)
        return entry

    def history(self, assumptions_digest: str) -> list[Approval]:
        """Every entry for a digest, in the order they arrived."""
        return [e for e in self._entries
                if e.assumptions_digest == assumptions_digest]

    def approvers(self, assumptions_digest: str) -> tuple[str, ...]:
        """Who currently approves this digest, last action per approver."""
        state: dict[str, str] = {}
        for entry in self.history(assumptions_digest):
            state[entry.approver] = entry.action
        return tuple(sorted(name for name, action in state.items()
                            if action == "approve"))

    def is_approved(self, assumptions_digest: str, *,
                    submitter: str | None = None) -> bool:
        """Is this content approved by somebody who is not the submitter?

        ``submitter=None`` asks the weaker question — is it approved at all
        — which is what a read-only status route wants. The run guard always
        passes a submitter, because an approval by the person submitting is
        one pair of eyes twice.
        """
        approvers = self.approvers(assumptions_digest)
        if submitter is None:
            return bool(approvers)
        return any(name != submitter for name in approvers)

    def to_json(self, path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([e.to_dict() for e in self._entries], handle, indent=2)

    @classmethod
    def from_json(cls, path) -> "ApprovalRegistry":
        with open(path, encoding="utf-8") as handle:
            return cls(Approval.from_dict(row) for row in json.load(handle))


def assumptions_digest(assumptions: Any) -> str:
    """The digest an approval binds to.

    One line, and it is the whole of the design: exactly the value the run
    registry records, so what was approved and what was run are the same
    string or the check fails.
    """
    return fingerprint(assumptions)


def check_approved(assumptions: Any, submitter: str,
                   registry: ApprovalRegistry) -> str:
    """Return the digest, or raise :class:`ApprovalRequired`.

    The refusal distinguishes the two cases, because they need different
    actions from the reader: nobody has approved this content, or the only
    person who has is you.
    """
    digest = assumptions_digest(assumptions)
    approvers = registry.approvers(digest)
    if any(name != submitter for name in approvers):
        return digest
    if approvers:
        raise ApprovalRequired(
            f"assumption set {digest} is approved only by {submitter}, who "
            f"is submitting this run; four-eyes needs somebody else",
            digest=digest, approvers=approvers,
        )
    raise ApprovalRequired(
        f"assumption set {digest} is not approved", digest=digest,
    )
