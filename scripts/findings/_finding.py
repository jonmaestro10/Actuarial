"""The shape every finding script shares, and the reason it is a shape.

A finding that lives only in an RFC is an assertion. A finding with a script
beside it that CI runs is a **demonstration**, and the difference matters
twice over: to a reviewer, because they can re-run it against the current
engine rather than trust a paragraph; and to this repo, because a finding
whose demonstration stops reproducing is a finding that has silently
changed, and the build should say so.

So each script exposes exactly two things:

``FINDING``
    metadata — the slug (which must match its page in ``docs/findings/``),
    a one-line claim, and where the finding was first recorded.

``demonstrate()``
    returns the numbers. No printing, no assertions: ``tests/test_findings.py``
    asserts the claim against what this returns, and ``__main__`` prints it
    for a human. A script that asserted its own claim would pass in CI while
    proving nothing about the engine, because it would be checking itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """What a finding says, and where it came from."""

    slug: str
    claim: str
    source: str
    regulation: str = ""


def show(finding: Finding, numbers: dict) -> None:
    """Print a demonstration for a human reading the terminal."""
    print(f"{finding.slug}\n{'=' * len(finding.slug)}")
    print(f"{finding.claim}\n")
    if finding.regulation:
        print(f"source text : {finding.regulation}")
    print(f"recorded in : {finding.source}\n")
    print(json.dumps(numbers, indent=2, default=str))
