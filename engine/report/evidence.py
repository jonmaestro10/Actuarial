"""The validation evidence pack: what this engine can prove about itself.

RFC-049. The landscape report's strategic risk (§6) is not a missing
feature — it is that the incumbents have been in front of regulators for
thirty years and this has not. Most of that gap is bought with time. The
part that is *not* is the evidence itself: a validation file for an SII
internal model or a VM-G governance review is a folder of assertions about
what was tested, what agreed with what, and what version of everything
produced the numbers. Incumbents compile theirs by hand, once, per client.

This generates one, and can regenerate it. Every section is produced from
something that already exists in the repo and is checkable by the reader:

- the **test inventory**, collected live from pytest rather than counted by
  hand;
- the **closed-form identity** list, the subset of that inventory that
  checks arithmetic against a formula rather than against a stored number;
- the **executor-equivalence attestation**, which is *run* here rather than
  quoted — every specimen is projected under both executors and the two
  ``results_digest`` values are compared;
- **docstring coverage**, measured off the library;
- the **parity reports on record** (RFC-033) and their two digests each;
- the **audit chain's head digest** (RFC-045), verified — the pack is where
  that head gets published, because a chain catches an edited entry and only
  an externally recorded head catches a deleted one;
- **benchmark numbers**, if the caller supplies them, and a plain statement
  that there are none if not.

Two rules keep the pack from becoming marketing.

**Nothing is asserted here that is not computed here.** A section reports
what it found, including "pytest was not available, so this pack claims
nothing about the test suite". A pack that silently omits a section it could
not build is a pack whose reader has to check for absences.

**The digest covers claims, not context.** ``environment.json`` — machine,
interpreter, library versions — is written into the pack and left *out* of
the pack digest, so rebuilding on another machine from the same commit
produces the same digest. Benchmark numbers are the deliberate exception:
they are claims, they are machine-specific, and a pack carrying them is
therefore reproducible only on the machine that made it. The index says
which of the two postures a given pack has, because the alternative is a
digest that quietly means less than the reader thinks.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from engine.core.fingerprint import fingerprint
from engine.core.registry import ArtifactRecord, ArtifactRegistry, git_commit

#: Test files whose contents are checks against a formula rather than
#: against a stored expectation. Named rather than inferred: "closed form"
#: is a claim about what a test *is*, and no heuristic over test names can
#: make it.
IDENTITY_FILES = (
    "tests/test_closed_form.py",
    "tests/test_annuity_factors.py",
    "tests/test_reference_model.py",
)


@dataclass(frozen=True)
class Section:
    """One part of the pack: a claim, its evidence, and one line about it."""

    name: str
    title: str
    summary: str
    content: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return fingerprint({"name": self.name, "content": dict(self.content)})

    def __fingerprint__(self):
        return {"name": self.name, "title": self.title,
                "summary": self.summary, "content": dict(self.content)}


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

def test_inventory(root: Path | str = ".") -> Section:
    """Every test the suite collects, from pytest itself.

    Collected rather than counted: a number in a document is a number that
    drifts, and "1,326 tests" is only evidence if the reader can ask the
    same question and get the same answer.
    """
    root = Path(root)
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=root, capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Section(
            "tests", "Test inventory",
            f"Not collected: {type(exc).__name__}. This pack claims nothing "
            f"about the test suite.",
            {"available": False, "reason": str(exc)},
        )
    if out.returncode != 0:
        return Section(
            "tests", "Test inventory",
            "Collection failed; this pack claims nothing about the test "
            "suite.",
            {"available": False, "reason": out.stderr.strip()[-2000:]},
        )

    by_file: dict[str, list[str]] = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if "::" not in line or line.startswith(("=", "<", "ERROR")):
            continue
        path, _, name = line.partition("::")
        by_file.setdefault(path, []).append(name)
    files = {path: sorted(names) for path, names in sorted(by_file.items())}
    total = sum(len(names) for names in files.values())
    return Section(
        "tests", "Test inventory",
        f"{total:,} tests collected across {len(files)} files.",
        {"available": True, "n_tests": total, "n_files": len(files),
         "files": files},
    )


def closed_form_identities(inventory: Section,
                           files: Sequence[str] = IDENTITY_FILES) -> Section:
    """The tests that check arithmetic against a formula.

    Derived from the collected inventory rather than from a separate list,
    so an identity test that is deleted disappears from the evidence pack
    too — which is the behaviour a validation file needs and the opposite of
    a hand-maintained appendix.
    """
    if not inventory.content.get("available"):
        return Section(
            "identities", "Closed-form identities",
            "Not available: the test inventory was not collected.",
            {"available": False},
        )
    collected = inventory.content["files"]
    found = {name: collected[name] for name in files if name in collected}
    missing = [name for name in files if name not in collected]
    total = sum(len(v) for v in found.values())
    return Section(
        "identities", "Closed-form identities",
        f"{total:,} identity checks in {len(found)} file(s)"
        + (f"; {len(missing)} named file(s) not collected" if missing else ""),
        {"available": True, "n_identities": total, "files": found,
         "not_collected": missing},
    )


def default_specimens() -> list[dict]:
    """One runnable specimen per template the worked examples cover.

    Imported from :mod:`engine.api` behind a guard, because that is where
    the repo already keeps a worked request per template and duplicating
    them here would create a second set to keep true. Without the ``[api]``
    extra there are no specimens, the attestation section says so, and it
    names nothing it did not run.
    """
    try:
        from engine.api.catalogue import build_run, catalogue
        from engine.api.examples import EXAMPLES
    except ImportError:  # pragma: no cover - exercised without the extra
        return []

    models = catalogue()
    specimens = []
    for name in sorted(EXAMPLES):
        request = EXAMPLES[name]["request"]
        try:
            built = build_run(request, models)
        except Exception:  # pragma: no cover - a broken example is a bug
            continue
        specimens.append({"name": name, **built})
    return specimens


#: How many scenarios of a bound set the pack re-runs alone. Not all of
#: them: the bridge costs one extra projection per scenario checked, and the
#: claim it supports — that the scenario axis does not couple — is a property
#: of the *formulas*, so it either holds everywhere or fails on the first
#: column. The number actually checked is reported beside the verdict, in
#: the pack, because a bounded check that reads as an exhaustive one is the
#: overclaim this whole document exists to avoid.
SCENARIO_BRIDGE_SAMPLE = 8


def _scenario_bridge(specimen: Mapping[str, Any]) -> dict:
    """Run single scenarios alone and compare them with the slab's columns.

    RFC-068's bridge into the equivalence class, and the exact analogue of
    RFC-061's pool of one: the stochastic executor evaluates a
    ``(model point x scenario)`` slab, and if that slab is what it claims to
    be then scenario ``s`` on its own is column ``s`` of it, bitwise. A
    template that let one scenario see another — a running maximum taken
    across the wrong axis, a reduction that swept the slab rather than the
    block — would break here and nowhere else in this pack.
    """
    from engine.core.registry import record_run

    scenarios = specimen["scenarios"]
    names = list(specimen.get("outputs")
                 or sorted(specimen["model_cls"].var_names()))
    slab, _ = record_run(**specimen, executor="stochastic")
    checked = min(SCENARIO_BRIDGE_SAMPLE, scenarios.n_scenarios)
    agreed = True
    for s in range(checked):
        alone, _ = record_run(**{**specimen, "scenarios": scenarios.single(s)},
                              executor="stochastic")
        for name in names:
            if not np.array_equal(np.asarray(slab.array(name))[:, :, s],
                                  np.asarray(alone.array(name))[:, :, 0]):
                agreed = False
                break
        if not agreed:
            break
    return {"bitwise": agreed, "scenarios_checked": checked,
            "of_scenarios": scenarios.n_scenarios}


def executor_equivalence(specimens: Iterable[Mapping[str, Any]] | None = None,
                         executors: Sequence[str] = ("interpreted",
                                                     "vectorized")) -> Section:
    """Run each specimen under every executor and compare answer digests.

    The attestation is *performed*, not quoted. Each run goes through
    :func:`~engine.core.registry.record_run`, so what is compared is the
    same ``results_digest`` the run registry uses to detect a
    non-deterministic engine — and what lands in the pack is the digest
    itself, which a reader can reproduce.
    """
    from engine.core.registry import record_run

    specimens = list(default_specimens() if specimens is None else specimens)
    if not specimens:
        return Section(
            "equivalence", "Executor equivalence",
            "No specimens available (the worked examples need the [api] "
            "extra); this pack attests nothing about executor equivalence.",
            {"available": False, "executors": list(executors)},
        )

    entries = []
    for specimen in specimens:
        specimen = dict(specimen)
        name = specimen.pop("name", None) or specimen["model_cls"].__name__
        # A specimen may carry the executor the caller last ran it under
        # (``build_run`` does); the attestation chooses its own.
        specimen.pop("executor", None)
        model_cls = specimen["model_cls"]

        # A pooled model reduces across the model-point axis, and the
        # interpreted executor sees one policy at a time — so it is outside
        # the equivalence class by construction rather than in breach of it.
        # Reporting that as a disagreement would be the pack's first lie.
        pooled = list(model_cls.pooled_names())
        coupled = bool(getattr(model_cls, "couples_model_points", False))
        # A specimen that binds a scenario set is outside *both* deterministic
        # executors, and for a blunter reason than pooling: a template reading
        # `self.scenarios` cannot be handed `None`, so neither of them can
        # evaluate it at all. RFC-068's third class, held to its own bridge
        # below rather than reported as a broken run.
        scenarios = specimen.get("scenarios")
        if scenarios is not None:
            applicable = ["stochastic"]
            excluded = (
                f"a bound scenario set runs under the stochastic executor "
                f"alone: {', '.join(executors)} pass no scenarios, and this "
                f"template reads them ({scenarios.n_scenarios} scenarios x "
                f"{scenarios.horizon} periods)"
            )
        else:
            applicable = [e for e in executors
                          if not (e == "interpreted" and (pooled or coupled))]
            excluded = None
            if len(applicable) < len(executors):
                excluded = (
                    "the interpreted executor evaluates one policy at a "
                    "time, so it cannot reproduce a reduction across the "
                    "block: "
                    + (f"pooled variables {pooled}" if pooled
                       else "couples_model_points is set")
                )

        digests: dict[str, str] = {}
        failure = None
        for executor in applicable:
            try:
                _, record = record_run(**specimen, executor=executor)
            except Exception as exc:  # pragma: no cover - defensive
                failure = f"{executor}: {type(exc).__name__}: {exc}"
                break
            digests[executor] = record.results_digest
        # A single-executor template gets the weaker claims it can support:
        # the same question asked twice gets the same answer, and the
        # *formulas* still meet an invariant on a block of one — a pool of
        # one being the same reduction either way (RFC-061) — or, for a
        # scenario-bound template, on a set of one, where running a scenario
        # alone must reproduce its column of the slab (RFC-068).
        repeated = None
        single_point = None
        single_scenario = None
        if failure is None and len(applicable) == 1:
            _, again = record_run(**specimen, executor=applicable[0])
            repeated = again.results_digest == digests[applicable[0]]
            if scenarios is not None:
                try:
                    single_scenario = _scenario_bridge(specimen)
                except Exception as exc:  # pragma: no cover - defensive
                    single_scenario = False
                    failure = f"scenario bridge: {type(exc).__name__}: {exc}"
            else:
                one = {**specimen,
                       "modelpoints": list(specimen["modelpoints"])[:1]}
                try:
                    bridge = {
                        e: record_run(**one, executor=e)[1].results_digest
                        for e in executors
                    }
                    single_point = len(set(bridge.values())) == 1
                except Exception as exc:  # pragma: no cover - defensive
                    single_point = False
                    failure = f"single-point bridge: {type(exc).__name__}: {exc}"
        agreed = (failure is None and len(applicable) > 1
                  and len(set(digests.values())) == 1)
        entries.append({
            "template": name,
            "n_modelpoints": len(list(specimen["modelpoints"])),
            "proj_len": specimen["proj_len"],
            "n_scenarios": None if scenarios is None else scenarios.n_scenarios,
            "executors": list(applicable),
            "results_digest": (next(iter(digests.values()))
                               if failure is None and len(set(digests.values())) == 1
                               else None),
            "digests": digests,
            "bitwise_identical": agreed,
            "in_equivalence_class": len(applicable) > 1,
            "excluded_because": excluded,
            "repeats_deterministically": repeated,
            "bitwise_on_one_modelpoint": single_point,
            "bitwise_on_one_scenario": single_scenario,
            "error": failure,
        })
    entries.sort(key=lambda row: row["template"])
    in_class = [row for row in entries if row["in_equivalence_class"]]
    agreed = sum(row["bitwise_identical"] for row in in_class)
    outside = len(entries) - len(in_class)
    stochastic = sum(row["n_scenarios"] is not None for row in entries)
    return Section(
        "equivalence", "Executor equivalence",
        f"{agreed} of {len(in_class)} templates bitwise-identical across "
        f"{', '.join(executors)}"
        + (f"; {outside} outside it by construction "
           f"({outside - stochastic} pooled or coupled, {stochastic} bound "
           f"to a scenario set), each held instead to determinism and to the "
           f"same invariant on a block of one or a set of one, where the "
           f"reduction and the slab are the same either way."
           if outside else "."),
        {"available": True, "executors": list(executors),
         "n_templates": len(entries), "n_in_class": len(in_class),
         "n_bitwise": agreed, "n_outside_class": outside,
         "n_scenario_bound": stochastic,
         "templates": entries},
    )


def docstring_coverage() -> Section:
    """Documentation coverage, measured off the library as it stands."""
    import importlib
    import inspect
    import pkgutil

    import engine.library as library
    from engine.core.model import Model
    from engine.core.modeldoc import library_coverage

    classes = []
    for module_info in pkgutil.iter_modules(library.__path__):
        module = importlib.import_module(f"engine.library.{module_info.name}")
        for cls in vars(module).values():
            if (inspect.isclass(cls) and issubclass(cls, Model)
                    and cls is not Model and cls.__module__ == module.__name__
                    and cls.var_names()):
                classes.append(cls)
    coverage = library_coverage(*sorted(classes, key=lambda c: c.__name__))
    documented, total = coverage["TOTAL"]
    share = documented / total if total else 1.0
    return Section(
        "coverage", "Docstring coverage",
        f"{documented:,} of {total:,} library variables documented "
        f"({share:.1%}).",
        {"available": True, "documented": documented, "total": total,
         "share": share,
         "per_template": {name: list(pair)
                          for name, pair in sorted(coverage.items())}},
    )


def parity_reports(registry: ArtifactRegistry | None = None) -> Section:
    """Reconciliations on record, named by both of their digests."""
    records = list(registry.of_kind("parity")) if registry is not None else []
    if not records:
        return Section(
            "parity", "Reconciliations on record",
            "None on record. This pack makes no reconciliation claim.",
            {"available": True, "n_reports": 0, "reports": []},
        )
    reports = [
        {"artifact_id": record.artifact_id,
         "label": record.label,
         "ok": record.ok,
         "results_digest": record.inputs.get("results_digest"),
         "external_digest": record.inputs.get("external_digest"),
         "content_digest": record.content_digest}
        for record in sorted(records, key=lambda r: r.artifact_id)
    ]
    passing = sum(bool(r["ok"]) for r in reports)
    return Section(
        "parity", "Reconciliations on record",
        f"{len(reports)} reconciliation(s) on record, {passing} in parity.",
        {"available": True, "n_reports": len(reports), "reports": reports},
    )


def audit_chain(log=None) -> Section:
    """The audit log's head digest, verified — RFC-045's external anchor.

    A hash chain catches an edited entry and cannot catch a deleted one; the
    only thing that catches a deletion is a head digest recorded somewhere
    the editor does not control. This is that somewhere: the pack is
    content-addressed and dated, so a head published here is a head somebody
    can hold a later log against.
    """
    if log is None:
        # "No log was supplied" is the same shape of answer as "no
        # reconciliation is on record": the section was built, it looked,
        # and there was nothing. Reporting it as *unavailable* would mark
        # every pack from a deployment that keeps no audit log as
        # incomplete, which is a different and untrue statement.
        return Section(
            "audit", "Audit chain",
            "No audit log supplied; this pack anchors no chain.",
            {"available": True, "anchored": False, "entries": 0,
             "head": None},
        )
    try:
        verified, problem = log.verify(), None
    except Exception as exc:
        verified, problem = False, str(exc)
    return Section(
        "audit", "Audit chain",
        f"{len(log):,} entries, head `{log.head}`, chain "
        + ("verified." if verified else f"**BROKEN**: {problem}"),
        {"available": True, "anchored": True, "entries": len(log),
         "head": log.head,
         "verified": verified, "problem": problem,
         "actions": sorted({event.action for event in log})},
    )


#: What the pack's digest is and is not an identity for. Stated because the
#: benchmarks section used to claim the pack "rebuilds to the same digest
#: anywhere", and that was never true.
#:
#: It is true *on a machine*: CI builds the pack twice and `diff -r` fails if
#: any digest moved, which is what RFC-049's acceptance criterion asserts and
#: what the claim was reaching for. Across machines it is false, and the
#: reason is not the engine's determinism but NumPy's: `np.exp` and `**` over
#: an array dispatch on the CPU's instruction set, so the same NumPy on two
#: microarchitectures returns values differing in the last unit in the last
#: place. Found when a CI runner and the machine the specimens were written
#: on — identical NumPy 2.4.6, identical Python — produced different digests,
#: and reproduced with `NPY_DISABLE_CPU_FEATURES=AVX512_SPR,AVX512_ICL,X86_V4`.
#:
#: Nothing the repo claims elsewhere is weakened by this. The dual-executor
#: invariant compares two executors *on one machine* and is untouched; so is
#: the registry's determinism check, and so is every golden test, which
#: either states an exact closed form or reconciles to a tolerance. What is
#: affected is precisely the one claim that quantified over machines.
REPRODUCIBILITY_SCOPE = (
    "This pack's digest is reproducible on a given machine — CI rebuilds it "
    "and requires the two to be identical — and is not a cross-machine "
    "identity. NumPy dispatches `exp` and `**` on the CPU's instruction set, "
    "so two machines running the same NumPy can differ in the last ULP, and "
    "a digest is over bits. Cite a pack digest alongside the environment "
    "section, which records what it was built on."
)


def benchmarks(records: Sequence[Mapping[str, Any]] | None = None) -> Section:
    """Benchmark numbers, if somebody measured some.

    Off by default and empty rather than absent when off: a validation pack
    that silently omits performance evidence reads as a pack whose reader
    forgot to look for it.
    """
    if not records:
        return Section(
            "benchmarks", "Benchmarks",
            "No benchmark on record for this pack, so nothing in it is a "
            "measurement of this machine's speed. It is not therefore "
            "machine-independent: see `reproducibility_scope`.",
            {"available": True, "n_benchmarks": 0, "benchmarks": [],
             "reproducibility_scope": REPRODUCIBILITY_SCOPE},
        )
    rows = [dict(record) for record in records]
    return Section(
        "benchmarks", "Benchmarks",
        f"{len(rows)} benchmark record(s). These are machine-specific "
        f"claims, so this pack's digest is reproducible only on the machine "
        f"that built it.",
        {"available": True, "n_benchmarks": len(rows), "benchmarks": rows},
    )


def environment() -> dict:
    """Machine and version context — recorded, and outside the digest."""
    import numpy as np

    from engine import __version__

    return {
        "engine_version": __version__,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


# --------------------------------------------------------------------------
# The pack
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidencePack:
    """A content-addressed folder of what the engine can prove about itself."""

    sections: tuple[Section, ...]
    context: Mapping[str, Any] = field(default_factory=dict)
    code_version: str | None = None

    def section(self, name: str) -> Section:
        for entry in self.sections:
            if entry.name == name:
                return entry
        raise KeyError(name)

    @property
    def digest(self) -> str:
        """Digest of the claims. Deliberately not of the environment."""
        return fingerprint({"sections": list(self.sections)})

    @property
    def machine_specific(self) -> bool:
        """Whether this pack carries claims only one machine can reproduce."""
        try:
            return bool(self.section("benchmarks").content.get("n_benchmarks"))
        except KeyError:
            return False

    def manifest(self) -> dict:
        return {
            "pack_digest": self.digest,
            "code_version": self.code_version,
            "machine_specific": self.machine_specific,
            "sections": {entry.name: entry.digest for entry in self.sections},
            "environment_in_digest": False,
        }

    def to_markdown(self) -> str:
        """The index: the whole pack in one screen, then section by section."""
        out = ["# Validation evidence pack", "",
               f"Pack digest `{self.digest}`"
               + (f", engine source `{self.code_version}`."
                  if self.code_version else ", engine source unknown "
                  "(not a git checkout)."),
               ""]
        out.append(
            "Every line below was generated from something this repository "
            "can be asked to recompute. Nothing here is hand-maintained."
        )
        out.append("")
        if self.machine_specific:
            out += ["> This pack carries benchmark numbers, which are claims "
                    "about a machine. Its digest is therefore reproducible "
                    "only on the machine that built it.", ""]
        else:
            out += ["> This pack carries no machine-specific claim: rebuilt "
                    "from the same source anywhere, it digests identically.",
                    ""]
        out += ["| section | finding | digest |", "|---|---|---|"]
        for entry in self.sections:
            out.append(f"| {entry.title} | {entry.summary} "
                       f"| `{entry.digest}` |")
        for entry in self.sections:
            out += ["", f"## {entry.title}", "", entry.summary, ""]
            out += _render(entry)
        if self.context:
            out += ["", "## Environment (context, outside the digest)", "",
                    "| | |", "|---|---|"]
            out += [f"| {key} | `{value}` |"
                    for key, value in sorted(self.context.items())]
        return "\n".join(out).rstrip() + "\n"

    def write(self, root: Path | str) -> Path:
        """Write the pack into ``root/<digest>/`` and return the directory.

        Content-addressed by directory name, per the execution plan's §1.6:
        an evidence pack with a mutable name is an evidence pack somebody can
        replace.
        """
        directory = Path(root) / self.digest
        directory.mkdir(parents=True, exist_ok=True)
        for entry in self.sections:
            _write_json(directory / f"{entry.name}.json", dict(entry.content))
        _write_json(directory / "environment.json", dict(self.context))
        _write_json(directory / "manifest.json", self.manifest())
        (directory / "index.md").write_text(self.to_markdown(),
                                            encoding="utf-8")
        return directory

    def record(self, registry: ArtifactRegistry) -> ArtifactRecord:
        """Register the pack, keyed by the source it was built from."""
        inputs = {
            "kind": "evidence",
            "code_version": self.code_version,
            "sections": [entry.name for entry in self.sections],
            # Which sections could be built is part of the derivation, not of
            # the result: a pack built with pytest unavailable is a different
            # question from one built with it, and conflating the two would
            # make the registry refuse the pair as a contradiction.
            "available": {entry.name: bool(entry.content.get("available", True))
                          for entry in self.sections},
            "machine_specific": self.machine_specific,
        }
        return registry.add(ArtifactRecord(
            artifact_id=fingerprint(inputs), kind="evidence",
            content_digest=self.digest, inputs=inputs,
            label="validation evidence pack",
            ok=all(entry.content.get("available", True)
                   for entry in self.sections),
            code_version=self.code_version,
        ))

    def __fingerprint__(self):
        return {"sections": list(self.sections)}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True,
                               default=str) + "\n", encoding="utf-8")


def _render(entry: Section) -> list[str]:
    """A section's evidence as a table where it has a natural one."""
    content = entry.content
    if entry.name == "equivalence" and content.get("available"):
        out = ["| template | model points | proj_len | results digest "
               "| bitwise |", "|---|---:|---:|---|---|"]
        for row in content["templates"]:
            digest = row["results_digest"] or "—"
            verdict = "yes" if row["bitwise_identical"] else "NO"
            if not row["in_equivalence_class"]:
                verdict = (
                    f"n/a — {row['excluded_because']}; repeat run "
                    + ("identical" if row["repeats_deterministically"]
                       else "**DIFFERED**")
                    + "; one model point "
                    + ("bitwise across both executors"
                       if row.get("bitwise_on_one_modelpoint")
                       else "**NOT bitwise**")
                )
            if row.get("error"):
                # A template that could not be run is not a template that
                # agreed, and the reason belongs on the page rather than in
                # a JSON file the reader may not open.
                verdict = f"NOT RUN — {row['error']}"
            out.append(f"| `{row['template']}` | {row['n_modelpoints']:,} "
                       f"| {row['proj_len']} | `{digest}` | {verdict} |")
        return out
    if entry.name == "coverage":
        out = ["| template | documented | variables |", "|---|---:|---:|"]
        for name, (documented, total) in sorted(
                content["per_template"].items()):
            out.append(f"| {name} | {documented} | {total} |")
        return out
    if entry.name == "parity" and content.get("reports"):
        out = ["| reconciliation | results digest | external digest "
               "| parity |", "|---|---|---|---|"]
        for row in content["reports"]:
            out.append(f"| {row['label'] or row['artifact_id']} "
                       f"| `{row['results_digest']}` "
                       f"| `{row['external_digest']}` "
                       f"| {'yes' if row['ok'] else 'NO'} |")
        return out
    if entry.name == "tests" and content.get("available"):
        out = ["| test file | tests |", "|---|---:|"]
        for path, names in content["files"].items():
            out.append(f"| `{path}` | {len(names)} |")
        return out
    if entry.name == "identities" and content.get("available"):
        out = ["| file | identity checks |", "|---|---:|"]
        for path, names in content["files"].items():
            out.append(f"| `{path}` | {len(names)} |")
        return out
    if entry.name == "benchmarks" and content.get("benchmarks"):
        keys = sorted({key for row in content["benchmarks"] for key in row})
        out = ["| " + " | ".join(keys) + " |",
               "|" + "---|" * len(keys)]
        for row in content["benchmarks"]:
            out.append("| " + " | ".join(str(row.get(key, "—"))
                                         for key in keys) + " |")
        return out
    return []


def build_pack(*, root: Path | str = ".",
               specimens: Iterable[Mapping[str, Any]] | None = None,
               registry: ArtifactRegistry | None = None,
               benchmark_records: Sequence[Mapping[str, Any]] | None = None,
               audit_log=None,
               code_version: str | None = None,
               collect_tests: bool = True) -> EvidencePack:
    """Build the whole pack.

    ``collect_tests`` exists for the pack's own test, which cannot recurse
    into pytest, and for a caller who wants the rest of the evidence in a
    second — every other section is computed in-process.
    """
    inventory = (test_inventory(root) if collect_tests else Section(
        "tests", "Test inventory",
        "Not collected for this pack.", {"available": False,
                                         "reason": "collect_tests=False"}))
    sections = (
        inventory,
        closed_form_identities(inventory),
        executor_equivalence(specimens),
        docstring_coverage(),
        parity_reports(registry),
        audit_chain(audit_log),
        benchmarks(benchmark_records),
    )
    return EvidencePack(
        sections=sections, context=environment(),
        code_version=(code_version if code_version is not None
                      else git_commit(str(root))),
    )
