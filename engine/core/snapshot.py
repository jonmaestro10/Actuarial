"""An assumption set, flattened — and what changed between two of them.

RFC-048. `fingerprint(assumptions)` answers "is this the same basis?" with
one bit, which is exactly right for an approval (RFC-044) and useless to
somebody who has just been told the answer is no. What a reviewer needs
next is *which component moved*, and that is what this module produces.

:func:`snapshot_rows` walks an assumption set the same way the fingerprint
encoder walks it — ``__fingerprint__()`` where an object states what defines
it, ``vars()`` where it does not — emitting one row per node with the digest
of the subtree beneath it. Two properties follow, and both are load-bearing:

- the **root row's digest is the run's** ``assumptions_digest``, so a
  snapshot cannot describe a basis other than the one it claims to;
- a component that did not change has the **same digest in both
  snapshots**, so :func:`diff_snapshots` is a join over two row sets rather
  than a text diff over two renderings. A text diff would report a
  reordered dict as a change and a changed float as a line; this reports
  neither.

The walk is bounded — ``max_depth``, ``max_items`` — because a mortality
basis has thousands of rates and a diff nobody can read is not a control.
A subtree that is summarised rather than expanded still carries its digest,
so it is still *checkable*: the reader learns that the basis changed inside
`mortality` even where the row for the individual rate is not there. That
is the difference between a bounded report and an incomplete one, and
:attr:`SnapshotDiff.summarised` names every place the bound was hit rather
than leaving the reader to wonder.

This lives in ``engine/core`` — NumPy only — because two surfaces need it:
the RFC-047 workbook's snapshot sheet and RFC-048's assumption-diff route.
One walker, so they cannot disagree about what a basis contains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

from engine.core.fingerprint import fingerprint

#: How deep the walk goes, and how wide a container it will expand, before
#: it summarises rather than enumerates. Chosen so a full ``Assumptions``
#: with a 91-age mortality table lands around ninety rows.
MAX_DEPTH = 4
MAX_ITEMS = 64


def _content(value: Any) -> Any:
    """What is *inside* ``value``, by the fingerprint encoder's own rules.

    Following those rules rather than inventing a walk is the whole
    correctness argument: a snapshot whose rows do not add up to the digest
    printed at the top of them describes something other than what ran.
    """
    if hasattr(value, "__fingerprint__"):
        return value.__fingerprint__()
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return dict(vars(value))
    return None


def _describe(value: Any) -> tuple[str, Any]:
    """``(kind, displayed value)`` for one node.

    Only scalars carry a displayed value. An array's *contents* are its
    digest's business — printing the first few would invite a reader to
    compare the wrong thing.
    """
    if isinstance(value, np.ndarray):
        return f"array {value.dtype}{tuple(value.shape)}", None
    if isinstance(value, (str, bool, np.bool_)):
        return type(value).__name__, value
    if isinstance(value, (int, np.integer)):
        return type(value).__name__, int(value)
    if isinstance(value, (float, np.floating)):
        return type(value).__name__, float(value)
    if value is None:
        return "none", None
    if isinstance(value, Mapping):
        return f"mapping[{len(value)}]", None
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"{type(value).__name__}[{len(value)}]", None
    return type(value).__name__, None


def snapshot_rows(assumptions: Any, *, max_depth: int = MAX_DEPTH,
                  max_items: int = MAX_ITEMS) -> list[dict]:
    """Flatten an assumption set into ``(path, kind, value, digest)`` rows.

    Row zero is the root: its ``digest`` is ``fingerprint(assumptions)``,
    the value the run registry recorded as ``assumptions_digest``. Rows
    below it are dotted paths into the structure.

    ``expanded`` says whether a node's children are in the list. A node with
    children that is not expanded was summarised at ``max_depth`` or
    ``max_items``; it still carries its digest, so a change beneath it is
    still *detected*, merely not *located*.
    """
    rows: list[dict] = []

    def walk(path: str, value: Any, depth: int) -> None:
        kind, shown = _describe(value)
        content = _content(value)
        if content is not None:
            kind = type(value).__name__
        rows.append({
            "path": path,
            "kind": kind,
            "value": shown,
            "digest": fingerprint(value),
            "expanded": False,
        })
        node = rows[-1]

        children: list[tuple[str, Any]] | None = None
        target = content if content is not None else value
        if depth < max_depth:
            if isinstance(target, Mapping):
                children = [(str(key), item) for key, item in target.items()]
            elif isinstance(target, (list, tuple)) and not isinstance(
                    target, (str, bytes)):
                children = [(str(i), item) for i, item in enumerate(target)]
        if children is None or not children or len(children) > max_items:
            return
        node["expanded"] = True
        for key, item in children:
            walk(f"{path}.{key}" if path else key, item, depth + 1)

    walk("", assumptions, 0)
    rows[0]["path"] = type(assumptions).__name__
    return rows


# --------------------------------------------------------------------------
# The diff
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Change:
    """One component that is not the same in both bases."""

    path: str
    kind: str
    #: ``changed`` | ``added`` | ``removed``. A path present on one side
    #: only is not a value change and saying so matters: a basis that grew
    #: a treaty and a basis whose treaty moved are different events.
    status: str
    left: Any = None
    right: Any = None
    left_digest: str | None = None
    right_digest: str | None = None
    #: True where the change was detected at a node whose children were not
    #: walked, so the report locates it to this component and no finer.
    summarised: bool = False

    def __fingerprint__(self):
        return {"path": self.path, "kind": self.kind, "status": self.status,
                "left_digest": self.left_digest,
                "right_digest": self.right_digest,
                "summarised": self.summarised}


@dataclass(frozen=True)
class SnapshotDiff:
    """What changed between two assumption sets, by component.

    ``identical`` is the one-bit answer and is deliberately taken from the
    *digests*, not from the change list: a bounded walk could in principle
    miss a difference below its horizon, and a diff that reported "no
    changes" for two bases with different digests would be the worst
    possible failure of this module. The two can only disagree in the
    direction of "the digests differ and the walk cannot say where", which
    is what :attr:`unlocated` reports.
    """

    left_digest: str
    right_digest: str
    changes: tuple[Change, ...] = ()
    n_left_rows: int = 0
    n_right_rows: int = 0

    @property
    def identical(self) -> bool:
        return self.left_digest == self.right_digest

    @property
    def summarised(self) -> tuple[Change, ...]:
        """Changes located only to a component whose inside was not walked."""
        return tuple(c for c in self.changes if c.summarised)

    @property
    def unlocated(self) -> bool:
        """The bases differ and the walk found no row to blame.

        Reachable only if the difference is entirely below the walk's
        horizon *and* every summarised ancestor somehow matched, which the
        digests make impossible — so this is an assertion about the walker
        rather than an expected outcome. It is reported rather than
        asserted because a UI that says "they differ, here is where" must
        be able to say "they differ, and I cannot tell you where".
        """
        return not self.identical and not self.changes

    def to_dict(self) -> dict:
        return {
            "left_digest": self.left_digest,
            "right_digest": self.right_digest,
            "identical": self.identical,
            "unlocated": self.unlocated,
            "n_changes": len(self.changes),
            "n_summarised": len(self.summarised),
            "changes": [
                {"path": c.path, "kind": c.kind, "status": c.status,
                 "left": c.left, "right": c.right,
                 "left_digest": c.left_digest, "right_digest": c.right_digest,
                 "summarised": c.summarised}
                for c in self.changes
            ],
        }


def diff_rows(left: list[dict], right: list[dict]) -> SnapshotDiff:
    """Join two row sets by path and report the components that differ.

    A node whose digest matches is skipped **with its whole subtree** — an
    unchanged mortality basis contributes nothing rather than ninety
    identical rows. That is what makes the report readable on a real basis,
    and it is sound precisely because the digest covers the subtree.
    """
    left_by_path = {row["path"]: row for row in left}
    right_by_path = {row["path"]: row for row in right}
    # The root stands for the whole basis, so it is every change's ancestor
    # and reporting it would say only what the two digests already say.
    roots = {left[0]["path"], right[0]["path"]}
    paths = sorted((set(left_by_path) | set(right_by_path)) - roots)

    added = {p for p in paths if p not in left_by_path}
    removed = {p for p in paths if p not in right_by_path}
    changed = {p for p in paths
               if p in left_by_path and p in right_by_path
               and left_by_path[p]["digest"] != right_by_path[p]["digest"]}

    def has_descendant_in(path: str, pool) -> bool:
        return any(other.startswith(path + ".") for other in pool)

    def has_ancestor_in(path: str, pool) -> bool:
        parts = path.split(".")
        return any(".".join(parts[:cut]) in pool
                   for cut in range(1, len(parts)))

    # A component that *appeared* is one event, so the shallowest path wins:
    # a new treaty is "reinsurance changed shape", not eleven new fields.
    # A value that *moved* is located as precisely as the walk can manage,
    # so there the deepest path wins: `dynamic_lapse.base` beats
    # `dynamic_lapse`, which beats saying the basis changed.
    keep_added = {p for p in added if not has_ancestor_in(p, added)}
    keep_removed = {p for p in removed if not has_ancestor_in(p, removed)}
    located = keep_added | keep_removed
    keep_changed = {p for p in changed
                    if not has_descendant_in(p, changed | located)}

    changes: list[Change] = []
    for path in sorted(keep_changed | located):
        here = left_by_path.get(path)
        there = right_by_path.get(path)
        if here is None or there is None:
            present = there if here is None else here
            changes.append(Change(
                path=path, kind=present["kind"],
                status="added" if here is None else "removed",
                left=None if here is None else here["value"],
                right=None if there is None else there["value"],
                left_digest=None if here is None else here["digest"],
                right_digest=None if there is None else there["digest"],
            ))
            continue
        # A change at a node nobody looked inside is located to that node
        # and no finer. Saying so is the difference between a bounded
        # report and one that implies the component itself is the change.
        opaque = (not here["expanded"] and not there["expanded"]
                  and here["value"] is None and there["value"] is None)
        changes.append(Change(
            path=path, kind=here["kind"], status="changed",
            left=here["value"], right=there["value"],
            left_digest=here["digest"], right_digest=there["digest"],
            summarised=opaque,
        ))

    return SnapshotDiff(
        left_digest=left[0]["digest"], right_digest=right[0]["digest"],
        changes=tuple(changes),
        n_left_rows=len(left), n_right_rows=len(right),
    )


def diff_snapshots(left: Any, right: Any, *, max_depth: int = MAX_DEPTH,
                   max_items: int = MAX_ITEMS) -> SnapshotDiff:
    """Semantic diff of two assumption sets, by component."""
    return diff_rows(
        snapshot_rows(left, max_depth=max_depth, max_items=max_items),
        snapshot_rows(right, max_depth=max_depth, max_items=max_items),
    )
