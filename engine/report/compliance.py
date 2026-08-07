r"""The audit binder as a build artifact, generated from the control map.

RFC-079. G2's observation is that most of what a SOC 2 auditor asks for
already exists in this repository — an approval log, a chained audit trail,
content-addressed artifacts, a test suite that asserts the calculation
guarantees — and that what is missing is the **joining up**. So this module
does not implement controls. It reads
``docs/compliance/soc2-controls.md``, checks that the evidence each row names
is still real, and emits a section of the validation evidence pack.

Why the document is the source and not the code
-----------------------------------------------
The obvious alternative is a dict of controls in Python that *generates* the
Markdown, which would make drift impossible. It was rejected: the control map
is the artifact an auditor reads and argues with, and a generated file is one
nobody edits. The mapping between a Trust Services criterion and a mechanism
is a **judgement**, and judgements belong in prose that a human signed.

What replaces the generation is narrower and enough: every row names a pytest
node id, and :func:`unresolved_evidence` reports any that the suite no longer
collects. A control whose test was renamed fails the build. Drift is possible
in the direction that matters least — prose going stale about a mechanism that
still exists — and impossible in the direction that matters most, a control
citing evidence that has quietly gone.

The gaps are part of the evidence
---------------------------------
Rows marked *not claimed* are counted and reported rather than filtered out.
A compliance section that showed only satisfied controls would be a section
whose totals were a function of what somebody chose to write down, and the
first question a good auditor asks is what is missing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTROLS = Path("docs/compliance/soc2-controls.md")

#: A row of one of the control tables. Four cells: id, criterion, mechanism,
#: evidence.
_ROW = re.compile(r"^\|\s*(?P<control>[A-Z]{1,3}\d+\.\d+)\s*\|"
                  r"(?P<criterion>[^|]*)\|"
                  r"(?P<mechanism>.*)\|"
                  r"(?P<evidence>[^|]*)\|\s*$")

#: A pytest node id inside the evidence cell.
_NODE = re.compile(r"tests/[A-Za-z0-9_]+\.py::[A-Za-z0-9_]+")

#: A reference to a section of the evidence pack rather than to a test.
_GENERATED = re.compile(r"generated:([a-z_]+)")

#: How a row says it is deliberately unsatisfied.
_NOT_CLAIMED = "not claimed"


class Control:
    """One row: what it claims, and what backs the claim."""

    __slots__ = ("control", "criterion", "mechanism", "evidence",
                 "node_ids", "generated", "claimed")

    def __init__(self, control: str, criterion: str, mechanism: str,
                 evidence: str):
        self.control = control.strip()
        self.criterion = criterion.strip()
        self.mechanism = mechanism.strip()
        self.evidence = evidence.strip()
        self.node_ids = tuple(_NODE.findall(evidence))
        self.generated = tuple(_GENERATED.findall(evidence))
        self.claimed = _NOT_CLAIMED not in mechanism.lower()

    def summary(self) -> dict:
        return {"control": self.control, "criterion": self.criterion,
                "claimed": self.claimed, "tests": list(self.node_ids),
                "generated": list(self.generated)}

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<Control {self.control} claimed={self.claimed}>"


def read_controls(path: Path | str = CONTROLS) -> tuple:
    """Every control row in the map.

    Raises if the file is missing or contains no rows. An empty parse is the
    dangerous outcome here — it would report zero unresolved references and
    read exactly like a clean bill of health, which is the same shape as a
    parametrised test over an empty list.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"no control map at {path}. This module reports on that file and "
            f"has nothing to say without it."
        )
    controls = [Control(**match.groupdict())
                for line in path.read_text(encoding="utf-8").splitlines()
                if (match := _ROW.match(line))]
    if not controls:
        raise ValueError(
            f"{path} parsed to zero controls. A control map that reads as "
            f"empty reports no unresolved evidence and looks identical to one "
            f"that is fully satisfied."
        )
    return tuple(controls)


def unresolved_evidence(controls: Sequence[Control],
                        collected: Sequence[str]) -> dict:
    """Named tests that the suite no longer collects, per control.

    ``collected`` is the node-id list the pack's test inventory already
    gathered from pytest itself, so this asks the same question a developer
    would and does not run its own collection.
    """
    known = {node.split("[", 1)[0] for node in collected}
    missing = {}
    for control in controls:
        gone = [node for node in control.node_ids if node not in known]
        if gone:
            missing[control.control] = gone
    return missing


def _leak_statement() -> str | None:
    """RFC-078's cross-tenant signal, from the code that defines it.

    Imported inside the function on purpose. ``engine.api`` pulls FastAPI at
    package import, and §1.4 keeps ``engine/report`` importable with NumPy
    alone — `tests/test_architecture.py` asserts exactly this, so a top-level
    import here would fail the build on a machine without the `[api]` extra.
    """
    try:
        from engine.api.tenancy import shared_compute_leak
    except Exception:
        return None
    return shared_compute_leak()


def _rate_limit_scope() -> str | None:
    """RFC-079's honest description of what the limiter is not."""
    try:
        from engine.api.hardening import RATE_LIMIT_SCOPE
    except Exception:
        return None
    return RATE_LIMIT_SCOPE


def compliance(collected: Sequence[str] | None = None,
               path: Path | str = CONTROLS) -> Mapping[str, Any]:
    """The compliance section's content.

    Returns a mapping rather than a ``Section`` so :mod:`engine.report.evidence`
    keeps sole ownership of that type and this module stays importable on its
    own.

    ``available`` stays **True** even when every control is unsatisfied. §1.5's
    rule: a section with nothing to report is still a section that ran, and
    ``available=False`` is reserved for "this pack could not look", which is a
    different statement and the one a reader must be able to distinguish.
    """
    controls = read_controls(path)
    # Not checked is not the same as all missing, and computing it anyway
    # would say the second. With an empty inventory every reference is
    # trivially absent, and the section's summary would report "16 controls
    # cite evidence the suite no longer collects" about a suite it never
    # asked. The flag below is what a reader distinguishes them by.
    checked = bool(collected)
    missing = unresolved_evidence(controls, collected) if checked else {}

    claimed = [c for c in controls if c.claimed]
    unclaimed = [c for c in controls if not c.claimed]
    categories: dict[str, int] = {}
    for control in controls:
        categories[re.match(r"[A-Z]+", control.control).group()] = \
            categories.get(re.match(r"[A-Z]+", control.control).group(), 0) + 1

    return {
        "available": True,
        "source": str(path),
        "n_controls": len(controls),
        "n_claimed": len(claimed),
        "n_not_claimed": len(unclaimed),
        "not_claimed": [c.control for c in unclaimed],
        "by_category": dict(sorted(categories.items())),
        "controls": [c.summary() for c in controls],
        # Empty when the evidence still resolves. Present as a key either
        # way, so a reader can tell "checked and clean" from "not checked".
        "unresolved_evidence": missing,
        "evidence_checked": checked,
        "shared_compute_leak": _leak_statement(),
        "rate_limit_scope": _rate_limit_scope(),
    }
