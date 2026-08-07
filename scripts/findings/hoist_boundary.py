"""The compiled executor's hoist boundary is not the one RFC-072 measured.

Run: ``python scripts/findings/hoist_boundary.py``
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.core.bitwise import classify
from engine.core.compiled import _trace_once
from engine.data.modelpoints import to_batch
from engine.report.evidence import default_specimens
from scripts.findings._finding import Finding, show

FINDING = Finding(
    slug="hoist-boundary",
    claim=("RFC-072 settled which operations a kernel may contain, against "
           "IEEE-754, and the compiled executor hoists everything else. In "
           "practice almost nothing is hoisted for that reason: the "
           "overwhelming trigger is `untracked-array` — a raw ndarray "
           "arriving mid-expression — which is a limit of the *tracer*, not "
           "of the arithmetic. The published boundary and the operative one "
           "are different boundaries, and the operative one is invisible "
           "because both produce the same correct answer."),
    source="docs/rfc-082-interleaved-prepass.md",
)


def demonstrate() -> dict:
    """Count why each variable is hoisted, and what the standard says about it.

    Asserts nothing — `tests/test_findings.py` does that. What it does is ask
    the tracer, per template, which opaque node forced a variable out of the
    kernel, and put `engine.core.bitwise.classify`'s verdict beside it.
    """
    reasons: dict = defaultdict(set)
    for specimen in default_specimens():
        if specimen.get("scenarios") is not None:
            continue
        name = specimen.get("name") or specimen["model_cls"].__name__
        batch = to_batch(list(specimen["modelpoints"])[:2])
        try:
            dag, _, _ = _trace_once(
                specimen["model_cls"], batch, specimen["assumptions"],
                specimen["proj_len"], (0, 1, 2, 3), frozenset())
        except Exception:                     # pragma: no cover - defensive
            continue
        for node in dag.nodes:
            if node.kind == "opaque":
                _, _, why = node.label.partition(":")
                reasons[why].add(name)

    verdicts = {why: classify(why) for why in reasons}
    forced_by_standard = {
        why: sorted(templates) for why, templates in reasons.items()
        if verdicts[why] in ("hoist", "reduce")
    }
    forced_by_tracer = {
        why: sorted(templates) for why, templates in reasons.items()
        if verdicts[why] not in ("hoist", "reduce")
    }
    return {
        "reasons": {why: sorted(t) for why, t in sorted(reasons.items())},
        "verdicts": verdicts,
        "forced_by_standard": forced_by_standard,
        "forced_by_tracer": forced_by_tracer,
        "n_templates_blocked_by_tracer": max(
            (len(t) for t in forced_by_tracer.values()), default=0),
        "n_templates_blocked_by_standard": max(
            (len(t) for t in forced_by_standard.values()), default=0),
    }


if __name__ == "__main__":
    show(FINDING, demonstrate())
