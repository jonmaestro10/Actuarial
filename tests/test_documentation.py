"""The documentation set describes a repo that exists.

Four documents make claims a reader will act on: `CLAUDE.md` (conventions),
`docs/architecture.md` (structure — asserted in `test_architecture.py`),
`docs/developing.md` (commands) and `docs/user-guide.md` (behaviour).

The failure mode they share is that **nothing about a document fails a
build**. A stale command costs a developer an afternoon; a stale refusal
costs an actuary their trust in the rest of it. So each document's claims are
checked against the thing it describes:

- every **script and command** `developing.md` quotes must exist and be
  runnable;
- every **template and refusal** `user-guide.md` names must be real;
- every **extra** either document lists must be declared in `pyproject.toml`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEVELOPING = ROOT / "docs" / "developing.md"
USER_GUIDE = ROOT / "docs" / "user-guide.md"


def _extras() -> set:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
    return set(data["project"]["optional-dependencies"])


# --------------------------------------------------------------------------
# developing.md
# --------------------------------------------------------------------------

def test_every_script_the_developer_guide_quotes_exists():
    """**A stale instruction fails the build rather than an afternoon.**

    A benchmark renamed without touching the document sends a new developer
    to a file that is not there, and the first thing they learn about the
    repo is that its documentation is wrong."""
    quoted = set(re.findall(r"python (scripts/[\w/]+\.py)",
                            DEVELOPING.read_text()))
    assert quoted, "the developer guide quotes no scripts at all"
    missing = sorted(s for s in quoted if not (ROOT / s).exists())
    assert not missing, f"developing.md quotes scripts that do not exist: {missing}"


def test_the_benchmark_family_is_listed_in_full():
    """A benchmark added without a line here is one nobody runs. Derived
    from the directory rather than from a list, so the document cannot fall
    behind by omission."""
    on_disk = {p.name for p in (ROOT / "scripts").glob("benchmark*.py")}
    text = DEVELOPING.read_text()
    missing = sorted(name for name in on_disk if name not in text)
    assert not missing, f"developing.md does not mention {missing}"


def test_every_extra_the_guide_describes_is_declared():
    """The extras table is the first thing a reader installs from. An extra
    that was renamed in `pyproject.toml` and not here produces a confusing
    pip error rather than a useful one."""
    declared = _extras()
    text = DEVELOPING.read_text()
    for extra in sorted(declared):
        assert f"`{extra}`" in text, f"developing.md omits the [{extra}] extra"
    # And nothing invented: every backticked extra-looking token is real.
    mentioned = set(re.findall(r"^\| `(\w+)` \|", text, flags=re.M))
    assert mentioned <= declared, f"invented extras: {mentioned - declared}"


def test_the_pre_push_sequence_matches_the_working_agreement():
    """Two documents give the same four commands. If they drift, one of them
    is wrong and a reader has no way to tell which."""
    developing = DEVELOPING.read_text()
    agreement = (ROOT / "CLAUDE.md").read_text()
    for command in ("python -m pytest -q",
                    "scripts/evidence_pack.py --out /tmp/ev1",
                    "diff -r /tmp/ev1 /tmp/ev2",
                    "-W error::SyntaxWarning"):
        assert command in developing, command
        assert command in agreement, command


# --------------------------------------------------------------------------
# user-guide.md
# --------------------------------------------------------------------------

def test_every_template_the_user_guide_names_is_in_the_catalogue():
    """The catalogue is discovered by walking `engine.library`, so a template
    is exposed by existing. A guide naming one that does not exist — or
    missing one that does — is the same drift in two directions."""
    catalogue = pytest.importorskip(
        "engine.api.catalogue", reason="the catalogue needs the [api] extra")
    models = set(catalogue.catalogue())
    text = USER_GUIDE.read_text()

    named = set(re.findall(r"`([A-Z][A-Za-z]+)`", text)) & models
    missing = sorted(models - named)
    assert not missing, f"user-guide.md does not name {missing}"

    invented = [m for m in re.findall(r"^\| [\w &]+ \| (.+) \|$", text,
                                      flags=re.M)
                for m in re.findall(r"`([A-Z][A-Za-z]+)`", m)
                if m not in models]
    assert not invented, f"user-guide.md names templates that do not exist: {invented}"


def test_the_refusals_it_promises_are_refusals_that_happen():
    """**The section worth reading twice must be true.**

    Each of these is a case where returning a number would have been easy and
    wrong, and a guide that promised a refusal the engine no longer makes
    would be worse than one that promised nothing."""
    import numpy as np

    from engine.core.runner import PooledBlockError, check_per_policy
    from engine.core.dispatch import ArithmeticMismatch
    from engine.report.vm22_prescribed import PrescribedError, fx_factor

    text = USER_GUIDE.read_text()
    assert "What the engine refuses to do" in text

    # A category whose table is not carried, named rather than substituted.
    with pytest.raises(PrescribedError, match="no factor set"):
        fx_factor(70, "F", category="longevity_reinsurance")
    # A contract-year band demanded where there is none.
    with pytest.raises(PrescribedError, match="no contract-year band"):
        fx_factor(70, "F", contract_year=3)
    # An improvement scale outside [0, 1).
    from engine.report.vm22_prescribed import prescribed_mortality_rate
    with pytest.raises(PrescribedError, match=r"in \[0, 1\)"):
        prescribed_mortality_rate(0.01, 1.0, 1.0, 5)
    # Table 6.5 by name.
    from engine.report.vm22_prescribed import base_lapse_rate
    with pytest.raises(PrescribedError, match="interest guarantee period"):
        base_lapse_rate(0, 65, table="fixed")

    # A pooled model one policy at a time.
    from engine.library.with_profits import WithProfitsEndowment
    with pytest.raises(PooledBlockError):
        check_per_policy(WithProfitsEndowment, 5)

    # And the classes the guide says these belong to are real.
    assert issubclass(ArithmeticMismatch, RuntimeError)


def test_the_wrong_readings_it_says_are_computed_on_purpose_exist():
    """"Where a wrong reading is tempting, compute it on purpose so the gap
    is reportable" — asserted, because it is the claim that distinguishes
    this engine's posture from a caveat in a manual."""
    from engine.report import vm22
    from engine.report.incurred_claims import MackResult
    from engine.library import takaful

    assert hasattr(vm22, "floor_outside_reserve")
    assert hasattr(MackResult, "quadrature_total")
    assert any("surplus_if_qard_ignored" in name for name in dir(takaful)) \
        or "surplus_if_qard_ignored" in (takaful.__doc__ or "") \
        or hasattr(getattr(takaful, "FamilyTakaful", object),
                   "surplus_if_qard_ignored")


def test_the_guide_points_at_the_evidence_it_relies_on():
    """It cites the findings catalogue and the Table 6.5 evidence. Both are
    files, and both must be where it says."""
    text = USER_GUIDE.read_text()
    for cited in ("sources/vm22-table-6-5-reading.md", "findings/"):
        assert cited in text
    assert (ROOT / "docs" / "sources" / "vm22-table-6-5-reading.md").exists()
    assert (ROOT / "docs" / "findings" / "README.md").exists()
