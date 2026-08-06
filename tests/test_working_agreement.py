"""`CLAUDE.md` must name the conventions that are actually enforced.

A working agreement is the first thing a new developer or agent reads and the
last thing anybody updates. This repo has already been bitten twice by a
document drifting from the thing it describes — a provenance string that
claimed two prescribed tables when seven were carried, and a docstring that
stated an exclusion the evidence pack had been contradicting for months. Both
were *enforcing* the error, because a test asserted the stale text.

So the rule this suite applies is the one those failures earned: **derive,
don't restate**.

- Every convention that is enforced somewhere must be **named** in
  `CLAUDE.md`, so the document cannot silently fall behind the code.
- Every path `CLAUDE.md` cites must **exist**, so a rename cannot leave a
  reader chasing a file that moved.
- `CLAUDE.md` must contain **no figure that drifts** — no test count, no
  coverage percentage. Those live in one place and are cited, not copied.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGREEMENT = ROOT / "CLAUDE.md"

#: (phrase that must appear, the artefact that enforces it).
#:
#: The second half is what makes this a derivation rather than a spelling
#: test: a convention may only be listed here if something in the repo
#: actually holds it, and that artefact is asserted to exist. A convention
#: whose enforcement was deleted fails here rather than living on in prose.
ENFORCED = [
    ("bitwise", "engine/core/bitwise.py"),
    ("engine/core/bitwise.py", "engine/core/bitwise.py"),
    ("REQUIRE_COMPILE_EXTRA", "tests/test_bitwise_boundary.py"),
    ("shape, dtype and value separately", "tests/test_slab_binding.py"),
    ("branch on model-point data", "engine/core/compiled.py"),
    ("attestation", "engine/core/dispatch.py"),
    ("REPRODUCIBILITY_SCOPE", "engine/report/evidence.py"),
    ("engine/core/registry.py", "engine/core/registry.py"),
    ("tests/test_published_sources.py", "tests/test_published_sources.py"),
    ("docs/sources/", "docs/sources/README.md"),
    ("conftest.py", "tests/conftest.py"),
    ("evidence_pack.py", "scripts/evidence_pack.py"),
    ("floor_outside_reserve", "engine/report/vm22.py"),
    ("surplus_if_qard_ignored", "engine/library/takaful.py"),
]

#: Figures that must **not** be hard-coded here, with what they would go
#: stale against. Every one of these has drifted somewhere in this repo
#: before, which is why the document cites rather than copies them.
FORBIDDEN_FIGURES = (
    (r"\b\d,\d{3}\s+tests\b", "a test count"),
    (r"\b\d{2,3}\.\d\s*%", "a coverage percentage"),
    (r"\bRFC-0\d\d is the highest\b", "the highest RFC number"),
)


def text() -> str:
    return AGREEMENT.read_text()


def test_the_working_agreement_exists_and_is_short_on_purpose():
    """It is the first thing read and the least likely to be read twice. A
    working agreement nobody finishes is a working agreement nobody follows."""
    assert AGREEMENT.exists(), "CLAUDE.md is missing"
    body = text()
    assert body.startswith("# Working agreement")
    words = len(body.split())
    assert 600 < words < 2_200, (
        f"{words} words: too short to carry the conventions, or long enough "
        f"that it stops being read"
    )


@pytest.mark.parametrize("phrase,enforcer", ENFORCED,
                         ids=[p for p, _ in ENFORCED])
def test_every_enforced_convention_is_named(phrase, enforcer):
    """**The derivation.** A convention may only be listed here if something
    in the repo holds it — so this asserts the enforcer exists *and* that the
    document mentions it. A convention whose enforcement was deleted fails
    here rather than living on as prose nobody is bound by."""
    assert (ROOT / enforcer).exists(), (
        f"{enforcer} no longer exists, so the convention it enforced is now "
        f"only a sentence in CLAUDE.md — remove it from ENFORCED or restore "
        f"the enforcement"
    )
    assert phrase in text(), (
        f"CLAUDE.md does not mention {phrase!r}, which {enforcer} enforces"
    )


def test_every_path_it_cites_exists():
    """A rename that left the document pointing at a moved file would send a
    reader looking for something that is not there, which is worse than not
    citing it at all."""
    cited = set(re.findall(r"[`(](engine/[\w/]+\.py|tests/[\w/]+\.py|"
                           r"docs/[\w/.-]+\.md|scripts/[\w/]+\.py)[`)]",
                           text()))
    assert cited, "the document cites no paths at all"
    missing = sorted(p for p in cited if not (ROOT / p).exists())
    assert not missing, f"CLAUDE.md cites paths that do not exist: {missing}"


@pytest.mark.parametrize("pattern,what", FORBIDDEN_FIGURES,
                         ids=[w for _, w in FORBIDDEN_FIGURES])
def test_it_contains_no_figure_that_will_drift(pattern, what):
    """**Derive, don't restate.** Every figure in this list has gone stale
    somewhere in this repo already. A working agreement quoting a test count
    is a working agreement that is wrong by the next commit, and the reader
    who notices it is right to trust the rest of it less."""
    found = re.findall(pattern, text())
    assert not found, (
        f"CLAUDE.md hard-codes {what}: {found}. Cite where it lives instead "
        f"— that number changes and this document will not change with it."
    )


def test_it_points_at_the_documents_that_go_deeper():
    """It is deliberately short, which only works if it says where the rest
    is. A short document with no onward links is not concise, it is
    incomplete."""
    body = text()
    for onward in ("docs/architecture.md", "docs/developing.md",
                   "docs/user-guide.md",
                   "docs/competitive-execution-plan.md"):
        assert onward in body, onward


def test_the_verification_sequence_is_the_one_that_is_actually_run():
    """The three commands before a push. Asserted because a stale command
    here costs a developer an afternoon and a reviewer their confidence."""
    body = text()
    assert "python -m pytest -q" in body
    assert "scripts/evidence_pack.py" in body
    assert "diff -r" in body
    assert "-W error::SyntaxWarning" in body
    assert "__pycache__" in body
