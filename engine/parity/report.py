"""The parity report, and what a reconciliation owes a sceptic.

A reconciliation is the document a replatforming decision rests on, so it
has to be two things a spreadsheet of differences is not.

**Readable.** :func:`render_markdown` writes the verdict first, then the
per-variable table, then the worst cells by name — model point and time
step, both sides' numbers — so the reader's next action is to open that
model point rather than to ask for a better report.

**Checkable.** :func:`record_parity` puts it in the artifact registry keyed
by *both* digests: the engine ``results_digest`` and the external file's
content digest. A recorded reconciliation therefore names the exact two
things it compared, and re-running it on the same pair must produce the same
report digest — the registry refuses a second record that says otherwise, in
the same way :class:`~engine.core.registry.RunRegistry` refuses a
non-deterministic run.
"""

from __future__ import annotations

from typing import Any

from engine.core.fingerprint import fingerprint
from engine.core.registry import ArtifactRecord, ArtifactRegistry, git_commit
from engine.parity.diff import ParityReport

PARITY_KIND = "parity"


def _number(value: float) -> str:
    if value == 0.0:
        return "0"
    return f"{value:.6g}"


def render_markdown(report: ParityReport) -> str:
    """Render a :class:`~engine.parity.diff.ParityReport` as Markdown."""
    title = "# Parity report"
    if report.label:
        title += f": {report.label}"
    verdict = ("**PARITY** — every mapped cell within tolerance."
               if report.ok else "**DIFFERENCES FOUND.**")
    out = [title, "", verdict, ""]

    out += ["| | |", "|---|---|"]
    if report.results_digest:
        out.append(f"| engine results digest | `{report.results_digest}` |")
    out.append(f"| external content digest | `{report.external_digest}` |")
    if report.external_source:
        out.append(f"| external source | `{report.external_source}` |")
    out.append(f"| spec digest | `{report.spec_digest}` |")
    out.append(f"| external rows | {report.n_external_rows:,} "
               f"({report.n_matched_rows:,} matched) |")
    out.append(f"| engine cells covered | {report.n_covered_cells:,} of "
               f"{report.n_engine_cells:,} ({report.coverage:.1%}) |")
    out.append("")

    out += ["## Variables", "",
            "| variable | column | compared | within | max |Δ| | max rel |Δ| "
            "| worst model point | worst t | tolerance |",
            "|---|---|---:|---:|---:|---:|---|---:|---|"]
    for entry in report.variables:
        flag = "" if entry.ok else " ⚠"
        out.append(
            f"| `{entry.variable}`{flag} | `{entry.column}` "
            f"| {entry.n_compared:,} | {entry.n_within:,} "
            f"| {_number(entry.max_absolute)} "
            f"| {_number(entry.max_relative)} "
            f"| {entry.worst_modelpoint if entry.worst_modelpoint is not None else '—'} "
            f"| {entry.worst_t if entry.worst_t is not None else '—'} "
            f"| {entry.tolerance.describe()} |"
        )
    out.append("")

    outside = [e for e in report.variables if not e.ok]
    if outside:
        out += ["## Cells outside tolerance", ""]
        for entry in outside:
            out += [f"### `{entry.variable}` — {entry.n_outside:,} of "
                    f"{entry.n_compared:,} outside tolerance"
                    + (f", {entry.n_nonfinite:,} non-finite"
                       if entry.n_nonfinite else ""),
                    "",
                    "| model point | t | engine | external | |Δ| | rel |Δ| |",
                    "|---|---:|---:|---:|---:|---:|"]
            for cell in entry.deviations:
                if cell.within:
                    continue
                out.append(
                    f"| {cell.modelpoint} | {cell.t} | {cell.engine!r} "
                    f"| {cell.external!r} | {_number(cell.absolute)} "
                    f"| {_number(cell.relative)} |"
                )
            out.append("")

    if report.unmatched_rows:
        out += [f"## Unmatched external rows ({report.n_unmatched_rows:,})", "",
                "Rows the engine result has no cell for. They are not "
                "reconciled, so the report is not a parity.", "",
                "| row | key | reason |", "|---:|---|---|"]
        for row in report.unmatched_rows[:20]:
            keys = ", ".join(f"{k}={v!r}" for k, v in row.items()
                             if k not in ("row", "reason"))
            out.append(f"| {row['row']} | {keys} | {row['reason']} |")
        if len(report.unmatched_rows) > 20:
            out.append(f"| … | {len(report.unmatched_rows) - 20:,} more | |")
        out.append("")

    if report.unmapped_columns:
        out += ["## Unmapped external columns", "",
                "Present in the extract, mapped to nothing, therefore "
                "reconciled by nobody:", ""]
        out += [f"- `{name}`" for name in report.unmapped_columns]
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def parity_artifact(report: ParityReport, *,
                    code_version: str | None = None) -> ArtifactRecord:
    """The registry record for a reconciliation.

    ``artifact_id`` digests what was compared — the two content digests, the
    mapping and the tolerance policy (all of which are in ``spec_digest``) —
    and ``content_digest`` digests the report that came out. Same inputs,
    same verdict, or the registry says so.
    """
    inputs: dict[str, Any] = {
        "kind": PARITY_KIND,
        "results_digest": report.results_digest,
        "external_digest": report.external_digest,
        "spec_digest": report.spec_digest,
    }
    return ArtifactRecord(
        artifact_id=fingerprint(inputs),
        kind=PARITY_KIND,
        content_digest=report.digest,
        inputs=inputs,
        label=report.label,
        ok=report.ok,
        code_version=code_version if code_version is not None else git_commit(),
    )


def record_parity(report: ParityReport, registry: ArtifactRegistry, *,
                  code_version: str | None = None) -> ArtifactRecord:
    """Record a reconciliation in ``registry`` and return its record."""
    return registry.add(parity_artifact(report, code_version=code_version))
