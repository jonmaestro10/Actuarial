"""Build the validation evidence pack — RFC-049.

One command, one content-addressed directory::

    python scripts/evidence_pack.py --out evidence

What comes out is an ``index.md`` plus one JSON file per section: the test
inventory collected live from pytest, the closed-form identity list, the
executor-equivalence attestation (run here, not quoted), docstring coverage,
any reconciliations on record, and any benchmark numbers supplied. The
directory is named by the pack's digest, so two builds from the same source
land in the same place with the same bytes.

Reconciliations come from an artifact registry written by
:func:`engine.parity.report.record_parity`; pass ``--registry`` to include
them, and the same file is where this pack's own record is appended.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.core.registry import ArtifactRegistry  # noqa: E402
from engine.report.evidence import build_pack  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="evidence",
                        help="directory to write the pack into")
    parser.add_argument("--root", default=".",
                        help="repository root to collect tests from")
    parser.add_argument("--registry", default=None,
                        help="artifact registry JSON: parity reports are read "
                             "from it and this pack is recorded into it")
    parser.add_argument("--benchmarks", default=None,
                        help="JSON list of benchmark records to include; "
                             "including any makes the pack machine-specific")
    parser.add_argument("--no-tests", action="store_true",
                        help="skip the pytest collection step")
    args = parser.parse_args()

    registry_path = Path(args.registry) if args.registry else None
    registry = (ArtifactRegistry.from_json(registry_path)
                if registry_path and registry_path.is_file()
                else ArtifactRegistry())
    benchmark_records = (json.loads(Path(args.benchmarks).read_text())
                         if args.benchmarks else None)

    pack = build_pack(root=args.root, registry=registry,
                      benchmark_records=benchmark_records,
                      collect_tests=not args.no_tests)
    directory = pack.write(args.out)
    record = pack.record(registry)
    if registry_path is not None:
        registry.to_json(registry_path)

    print(f"pack digest : {pack.digest}")
    print(f"written to  : {directory}")
    state = "complete" if record.ok else "INCOMPLETE — a section is missing"
    print(f"registered  : {record.artifact_id} ({state})")
    print()
    for section in pack.sections:
        print(f"  {section.title:26s} {section.summary}")
    return 0 if record.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
