"""Run registry: what was computed, from what, and did it come out the same.

PLAN.md §7 asks every run to record the model, the assumption snapshot, the
input hashes, the engine version and the seed. §2.3 explains why: a run that
pins its inputs exactly is reproducible, and reproducibility is what makes
an accuracy claim checkable by somebody who was not there.

A :class:`RunRecord` carries two digests that do different jobs:

- ``run_id`` fingerprints the **inputs** — model source, assumptions, model
  points, scenarios, projection length, outputs, executor. Two runs with the
  same ``run_id`` were asked the same question.
- ``results_digest`` fingerprints the **answer**.

The pair is the assertion. Same ``run_id`` and a different
``results_digest`` means the engine is not deterministic, and that is a bug
of a kind no per-number tolerance would catch. tests/test_registry.py holds
that up against repeat runs, chunk sizes, and both executors.

What ``run_id`` deliberately excludes is anything that provably cannot move
a number: the chunk size the vectorized executor happened to pick, and the
wall-clock time. Excluding them is a claim, so both are tested — different
chunk sizes must produce the same ``run_id`` *and* the same
``results_digest``.

:class:`ArtifactRecord` extends the same discipline to things *derived* from
runs — the parity report of RFC-033 first. Same pair, same assertion: the
inputs digest to an identifier, the output digests to a content hash, and a
second derivation that disagrees is refused rather than filed alongside.

What it cannot capture is recorded rather than papered over. ``source_digest``
sees a model class and its bases, not the module-level helpers a formula may
call, so ``RunRecord`` carries a ``code_version`` field for the git commit
that a serious deployment should pin alongside it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np

from engine.core.fingerprint import fingerprint, source_fingerprint
from engine.core.runner import run as run_interpreted
from engine.core.stochastic import run_stochastic
from engine.core.vector import run_vectorized


def git_commit(repo: str | None = None) -> str | None:
    """Current commit of the working tree, if this is a git checkout.

    Best effort by design: a run from an unpacked archive has no commit, and
    saying so is better than inventing one.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
            text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    return commit or None


@dataclass(frozen=True)
class RunRecord:
    """Everything needed to say what a run was, and to know if it repeated."""

    run_id: str
    engine_version: str
    executor: str
    model_module: str
    model_name: str
    model_source_digest: str
    assumptions_digest: str
    modelpoints_digest: str
    n_modelpoints: int
    proj_len: int
    outputs: tuple[str, ...]
    results_digest: str
    scenarios_digest: str | None = None
    n_scenarios: int | None = None
    scenario_horizon: int | None = None
    #: Not part of ``run_id``: provably cannot change a number.
    chunk_size: int | None = None
    #: Not part of ``run_id``: the git commit and the clock are context, and
    #: a run repeated from the same source at a later time is the same run.
    code_version: str | None = None
    #: The shard tree of a dispatched run: ``{shard index: results digest}``
    #: for the pieces this answer was reduced from, and the arithmetic
    #: attestation the workers agreed on.
    #:
    #: **Not part of ``run_id``**, and that is the claim rather than an
    #: omission. RFC-075's whole point is that where a shard ran cannot move
    #: a number: the reduction is by shard index, so a run split five ways
    #: and the same run split eight ways are the *same run* and must share an
    #: identifier. Putting the topology in the identity would make a
    #: correctly reproduced answer look like a different one.
    #:
    #: Recorded because it is the evidence *for* that claim. A reviewer
    #: comparing two records with one ``run_id``, one ``results_digest`` and
    #: different shard trees is looking at the guarantee being kept.
    shards: dict | None = None
    #: The arithmetic every worker attested to. ``None`` for an undispatched
    #: run — which is not the same as "one machine", and the difference is
    #: why it is nullable rather than defaulted to this machine's.
    arithmetic: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def matches(self, other: "RunRecord") -> bool:
        """Same question, same answer."""
        return (
            self.run_id == other.run_id
            and self.results_digest == other.results_digest
        )

    def to_dict(self) -> dict:
        record = asdict(self)
        record["outputs"] = list(self.outputs)
        if self.shards is not None:
            record["shards"] = {str(k): v for k, v in self.shards.items()}
        return record

    @classmethod
    def from_dict(cls, record: dict) -> "RunRecord":
        record = dict(record)
        record["outputs"] = tuple(record["outputs"])
        if record.get("shards"):
            record["shards"] = {int(k): v
                                for k, v in record["shards"].items()}
        return cls(**record)

    @property
    def dispatched(self) -> bool:
        return bool(self.shards)


def _results_digest(result, outputs: Sequence[str]) -> str:
    return fingerprint({name: np.asarray(result.array(name)) for name in outputs})


def record_run(
    model_cls,
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    *,
    outputs: Sequence[str] | None = None,
    scenarios: Any = None,
    executor: str = "auto",
    chunk_size: int | None = None,
    code_version: str | None = None,
):
    """Run a model and return ``(result, RunRecord)``.

    ``executor`` defaults to the stochastic one when scenarios are supplied
    and the vectorized one otherwise; ``"interpreted"`` forces the per-policy
    path. Only the array-backed executors are registrable — the interpreted
    one is run through the vectorized contract for its digest, because a
    registry entry has to name results that can be compared.
    """
    points = list(modelpoints)
    if executor == "auto":
        executor = "stochastic" if scenarios is not None else "vectorized"
    if executor == "stochastic" and scenarios is None:
        raise ValueError("the stochastic executor needs a scenario set")
    if executor != "stochastic" and scenarios is not None:
        raise ValueError(
            f"scenarios were supplied but the executor is {executor!r}"
        )

    if executor == "stochastic":
        result = run_stochastic(
            model_cls, points, assumptions, scenarios, proj_len, outputs=outputs
        )
    elif executor == "vectorized":
        result = run_vectorized(
            model_cls, points, assumptions, proj_len, outputs=outputs,
            chunk_size=chunk_size,
        )
    elif executor == "interpreted":
        result = run_interpreted(
            model_cls, points, assumptions, proj_len, outputs=outputs
        )
    else:
        raise ValueError(f"unknown executor {executor!r}")

    names = tuple(outputs or sorted(model_cls.var_names()))
    if executor == "interpreted":
        # The interpreted result is per-model-point lists; put it in array
        # form so its digest is comparable with the other executors'.
        digest = fingerprint(
            {name: np.array([mp[name] for mp in result.per_mp]).T
             for name in names}
        )
    else:
        digest = _results_digest(result, names)

    from engine import __version__

    inputs = {
        "engine_version": __version__,
        "executor": executor,
        "model": f"{model_cls.__module__}.{model_cls.__qualname__}",
        "model_source": source_fingerprint(model_cls),
        "assumptions": fingerprint(assumptions),
        "modelpoints": fingerprint(points),
        "scenarios": fingerprint(scenarios) if scenarios is not None else None,
        "proj_len": proj_len,
        "outputs": list(names),
    }
    record = RunRecord(
        run_id=fingerprint(inputs),
        engine_version=__version__,
        executor=executor,
        model_module=model_cls.__module__,
        model_name=model_cls.__qualname__,
        model_source_digest=inputs["model_source"],
        assumptions_digest=inputs["assumptions"],
        modelpoints_digest=inputs["modelpoints"],
        n_modelpoints=len(points),
        proj_len=proj_len,
        outputs=names,
        results_digest=digest,
        scenarios_digest=inputs["scenarios"],
        n_scenarios=getattr(scenarios, "n_scenarios", None),
        scenario_horizon=getattr(scenarios, "horizon", None),
        chunk_size=chunk_size,
        code_version=code_version if code_version is not None else git_commit(),
    )
    return result, record


class RunRegistry:
    """An append-only log of runs.

    Deliberately small: a list, a JSON file, and one query that matters —
    has this exact question been asked before, and did it get the same
    answer? Anything richer belongs in the metadata database PLAN.md §2.4
    describes, not in the engine.
    """

    def __init__(self, records: Iterable[RunRecord] = ()):
        self._records: list[RunRecord] = list(records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        return iter(self._records)

    @property
    def records(self) -> list[RunRecord]:
        return list(self._records)

    def add(self, record: RunRecord) -> RunRecord:
        """Append a run, refusing one that contradicts a previous one.

        A repeat of a run already recorded is fine and is not stored twice.
        A repeat that produced *different* results is a determinism failure,
        and the registry is the natural place to notice it.
        """
        for existing in self._records:
            if existing.run_id != record.run_id:
                continue
            if existing.results_digest != record.results_digest:
                raise NonDeterministicRunError(
                    f"run {record.run_id} was recorded with results "
                    f"{existing.results_digest} and has now produced "
                    f"{record.results_digest}"
                )
            return existing
        self._records.append(record)
        return record

    def find(self, run_id: str) -> RunRecord | None:
        for record in self._records:
            if record.run_id == run_id:
                return record
        return None

    def to_json(self, path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([r.to_dict() for r in self._records], handle, indent=2)

    @classmethod
    def from_json(cls, path) -> "RunRegistry":
        with open(path, encoding="utf-8") as handle:
            return cls(RunRecord.from_dict(row) for row in json.load(handle))

    def to_parquet(self, path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(
            pa.Table.from_pylist([r.to_dict() for r in self._records]), path
        )

    @classmethod
    def from_parquet(cls, path) -> "RunRegistry":
        import pyarrow.parquet as pq

        return cls(
            RunRecord.from_dict(row) for row in pq.read_table(path).to_pylist()
        )


class NonDeterministicRunError(RuntimeError):
    """The same inputs produced different results."""


@dataclass(frozen=True)
class ArtifactRecord:
    """A derived artifact, addressed by what it was derived from.

    A run is not the only thing a serious deployment has to be able to point
    at later. A reconciliation (RFC-033), an approval, an evidence pack —
    each is produced *from* registered digests and each is worth exactly as
    much as its provenance. So they are recorded the same way runs are:
    ``artifact_id`` digests the inputs, ``content_digest`` digests what came
    out, and the pair is the assertion.

    ``inputs`` carries the digests it binds — for a parity report, the
    engine's ``results_digest`` and the external file's content digest — so
    the record names the exact things compared rather than a file path that
    can be repointed.
    """

    artifact_id: str
    kind: str
    content_digest: str
    inputs: dict
    label: str | None = None
    #: The artifact's own verdict, where it has one (a parity report does).
    ok: bool | None = None
    engine_version: str | None = None
    code_version: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self):
        if self.engine_version is None:
            from engine import __version__

            object.__setattr__(self, "engine_version", __version__)

    def matches(self, other: "ArtifactRecord") -> bool:
        """Same derivation, same artifact."""
        return (
            self.artifact_id == other.artifact_id
            and self.content_digest == other.content_digest
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> "ArtifactRecord":
        return cls(**dict(record))


class ArtifactRegistry:
    """An append-only log of derived artifacts.

    The same shape as :class:`RunRegistry`, and for the same reason: a second
    record with the same ``artifact_id`` and a different ``content_digest``
    means the derivation is not reproducible, which is a defect no amount of
    re-reading the artifact would reveal.
    """

    def __init__(self, records: Iterable[ArtifactRecord] = ()):
        self._records: list[ArtifactRecord] = list(records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        return iter(self._records)

    @property
    def records(self) -> list[ArtifactRecord]:
        return list(self._records)

    def add(self, record: ArtifactRecord) -> ArtifactRecord:
        for existing in self._records:
            if existing.artifact_id != record.artifact_id:
                continue
            if existing.content_digest != record.content_digest:
                raise ArtifactConflictError(
                    f"{record.kind} artifact {record.artifact_id} was "
                    f"recorded as {existing.content_digest} and has now "
                    f"produced {record.content_digest}"
                )
            return existing
        self._records.append(record)
        return record

    def find(self, artifact_id: str) -> ArtifactRecord | None:
        for record in self._records:
            if record.artifact_id == artifact_id:
                return record
        return None

    def of_kind(self, kind: str) -> list[ArtifactRecord]:
        return [record for record in self._records if record.kind == kind]

    def to_json(self, path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([r.to_dict() for r in self._records], handle, indent=2)

    @classmethod
    def from_json(cls, path) -> "ArtifactRegistry":
        with open(path, encoding="utf-8") as handle:
            return cls(ArtifactRecord.from_dict(row) for row in json.load(handle))


class ArtifactConflictError(RuntimeError):
    """The same derivation produced a different artifact."""
