#!/usr/bin/env python3
r"""The client pilot, executed end to end against synthetic fixtures.

RFC-081. §9's G4 says what this is for in one sentence: **so the pilot has been
run a thousand times before it is run once.** The A-workstream built the tools;
this makes the *process* a rehearsed artifact rather than a document somebody
reads on the morning of a kick-off.

Six stages, in the order a pilot runs them:

1. **Ingest** the client's model point file (RFC-034's reader).
2. **Map** their field names to ours, and report every field — including the
   ones we dropped.
3. **Run** the projection through the registry, so the run is
   content-addressed and idempotent.
4. **Reconcile** against their results extract at 1e-12 (RFC-033's parity core).
5. **Register** the parity report as an artifact, content-addressed.
6. **Hand over** the parity report and the validation evidence pack.

Everything is written into a **content-addressed directory** named by the
digest of what it contains, which is the same discipline the rest of the
repository uses and the reason two dry runs of an unchanged repo produce the
same directory name.

The fixtures are synthetic, and that is a data-handling rule, not a shortcut
-----------------------------------------------------------------------------
`tests/fixtures/prophet/` is hand-authored. This repository holds no
proprietary Prophet data and must not: a client's model points are their
policyholders' data, and the playbook's rule is that **client files never leave
the client's environment**. What we keep are dialect fixtures, invented.

So what this rehearses is the *process* — that each stage's output is the next
stage's input, that the reconciliation bites, that the hand-over is
content-addressed — and not that any particular client's file parses. That
distinction is in `docs/pilot-playbook.md` and it is the honest limit of a dry
run.

The reconciliation has to fail on demand
----------------------------------------
``--prove-it-bites`` perturbs one cell by one part in ten million and asserts
the reconciliation *fails*. A pilot whose parity report is green is worth
exactly what its ability to go red is worth, and that is the first thing a
sceptical client's actuary asks. Running it is cheap; the CI test does both.

Usage::

    python scripts/pilot_dryrun.py --out /tmp/pilot
    python scripts/pilot_dryrun.py --prove-it-bites
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "prophet"

#: External column → engine variable. **Written out, never inferred.** RFC-034's
#: rule and the one a pilot is most tempted to break: a mapping guessed from a
#: name similarity is a mapping that silently reconciles the wrong two columns.
RESULT_MAPPING = {
    "POLS_IF": "pols_if",
    "DEATHS": "pols_death",
    "CLAIMS": "claims",
    "PREMIUMS": "premiums",
}

OUTPUTS = ["pols_if", "pols_death", "claims", "premiums"]
PROJ_LEN = 25

#: The client's basis, as a pilot would receive it. Literal, not generated:
#: the same rule the worked examples follow, because a seeded generator makes
#: the artifact depend on a random stream nobody records.
QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}


class DryRunFailed(RuntimeError):
    """A stage that did not do what the playbook says it does."""


def _assumptions():
    from engine.data.assumptions import Assumptions, MortalityTable
    return Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                       interest=0.03, expense_per_policy=50.0)


def run(out: Path, *, perturb: bool = False, build_pack: bool = True) -> dict:
    """Execute the playbook. Returns the stage record.

    ``perturb`` moves one external cell by one part in ten million, which must
    make stage 4 fail. ``build_pack`` is the only slow stage and the only one a
    caller may reasonably want to skip.
    """
    from engine.core.registry import ArtifactRegistry, record_run
    from engine.library.term_life import TermLife
    from engine.migrate import read_modelpoints, read_results
    from engine.parity import (
        ExternalTable, ParitySpec, Tolerance, TolerancePolicy, diff,
        record_parity,
    )

    stages: dict = {}

    # 1. Ingest -----------------------------------------------------------
    points = read_modelpoints(FIXTURES / "term_life.pro")
    stages["ingest"] = {"n_modelpoints": len(points),
                        "source": "tests/fixtures/prophet/term_life.pro"}

    # 2. Map --------------------------------------------------------------
    mapping = points.mapping
    # Every incumbent field is accounted for, including the ones we do not
    # use. A migration report that lists only what it consumed is a report
    # that cannot answer "what happened to CLIENT_REF?" — which is the first
    # question an incumbent's modeller asks.
    stages["map"] = {
        "n_fields": len(mapping.fields),
        "actions": sorted({field.action for field in mapping.fields}),
    }

    # 3. Run --------------------------------------------------------------
    result, record = record_run(TermLife, list(points), _assumptions(),
                                PROJ_LEN, outputs=OUTPUTS)
    stages["run"] = {"run_id": record.run_id,
                     "results_digest": record.results_digest,
                     "executor": record.executor}

    # 4. Reconcile --------------------------------------------------------
    table = read_results(FIXTURES / "term_life_results.csv",
                         rename={"POL_NUMBER": "modelpoint_id", "T": "t"})
    if perturb:
        columns = {name: list(values) for name, values in table.columns.items()}
        columns["CLAIMS"][3] *= 1.000_000_1
        table = ExternalTable(columns)

    spec = ParitySpec.from_results(
        result, table, RESULT_MAPPING,
        tolerance=TolerancePolicy(Tolerance(relative=1e-12)),
        label="Pilot dry run: TermLife against the incumbent extract",
    )
    report = diff(spec, results_digest=record.results_digest)
    stages["reconcile"] = {
        "ok": report.ok,
        "coverage": report.coverage,
        "n_matched_rows": report.n_matched_rows,
        "max_relative": report.max_relative,
        "unmapped_columns": list(report.unmapped_columns),
    }

    if perturb:
        if report.ok:
            raise DryRunFailed(
                "the reconciliation passed on a cell moved by one part in ten "
                "million. A parity report is worth what its ability to go red "
                "is worth, and this one cannot go red."
            )
        stages["reconcile"]["bit_as_expected"] = True
    elif not report.ok:
        raise DryRunFailed(
            f"the reconciliation failed on unmodified fixtures:\n"
            f"{report.to_markdown()}"
        )

    # 5. Register ---------------------------------------------------------
    out.mkdir(parents=True, exist_ok=True)
    registry = ArtifactRegistry()
    artifact = record_parity(report, registry=registry)
    (out / "parity-report.md").write_text(report.to_markdown(),
                                          encoding="utf-8")
    stages["register"] = {"artifact_id": artifact.artifact_id,
                          "kind": artifact.kind}

    # 6. Hand over --------------------------------------------------------
    if build_pack:
        from engine.report.evidence import build_pack as build_evidence

        pack = build_evidence(root=REPO_ROOT, registry=registry,
                              collect_tests=False)
        pack_dir = pack.write(out / "evidence")
        stages["handover"] = {"pack_digest": pack.digest,
                              "path": str(pack_dir.relative_to(out)),
                              "sections": [s.name for s in pack.sections]}
    else:
        stages["handover"] = {"skipped": "build_pack=False"}

    (out / "dryrun.json").write_text(
        json.dumps(stages, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    return stages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="pilot-dryrun",
                        help="where to write the hand-over (default: ./pilot-dryrun)")
    parser.add_argument("--prove-it-bites", action="store_true",
                        help="perturb one cell and require the reconciliation "
                             "to fail")
    parser.add_argument("--no-pack", action="store_true",
                        help="skip the evidence pack, the one slow stage")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)

    try:
        stages = run(out, perturb=args.prove_it_bites,
                     build_pack=not args.no_pack)
    except DryRunFailed as exc:
        print(f"pilot dry run FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"pilot dry run: {len(stages)} stages, written to {out}")
    for name, detail in stages.items():
        head = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:3])
        print(f"  {name:<10} {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
