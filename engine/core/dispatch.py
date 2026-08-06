r"""Cross-machine dispatch: shards out, one answer back, and what it can promise.

B2's insight is that no job model is needed. A run is already an idempotent,
content-addressed question, so dispatching one is *submitting sub-runs and
reducing* — the shard split is :func:`engine.core.parallel.shard_bounds` and
the reduction is by **shard index, not arrival order**, exactly as the
single-machine parallel path already does it.

What this module adds beyond that is the part the plan got wrong.

The claim B2 was written with, and what it has to become
--------------------------------------------------------
The plan says the answer is "bitwise-identical for 1 machine or N, **any
topology**". On one machine that is true and is tested. Across machines it
is **not**, and RFC-072 measured why: ``np.exp``, ``log`` and ``**`` are
implementation-defined to within an ulp, and the repo has already reproduced
a last-bit difference between microarchitectures on the *same* NumPy with
``NPY_DISABLE_CPU_FEATURES``. A shard evaluated on an AVX-512 worker and one
evaluated on an older core can disagree in the last bit, and then the
concatenated answer depends on which worker happened to receive which shard —
which is worse than a slow answer, because it is a reproducibility claim that
fails silently and only sometimes.

So the guarantee is stated at the resolution it actually holds:

    **bitwise-identical across workers that attest the same arithmetic**,
    and refused otherwise.

That is not a weakening dressed up. It is the same scope
:data:`engine.report.evidence.REPRODUCIBILITY_SCOPE` already puts on a pack
digest — reproducible *on a machine*, not across them — applied to the one
feature whose whole selling point is spanning machines.

An attestation, not a caveat
----------------------------
RFC-070's rule: where a class boundary cannot be enforced it is a test, not a
paragraph — and here it *can* be enforced. Every worker computes
:func:`attest`, a digest of what its floating-point unit actually does to a
fixed probe, and the coordinator compares them **before** reducing. Workers
that disagree produce :class:`ArithmeticMismatch` naming the digests rather
than a number nobody can reproduce.

The probe deliberately includes both halves of RFC-072's split: the exact
operations, which must agree by IEEE-754 §5 and whose disagreement would mean
something is badly wrong; and the transcendentals, which are the ones that
actually vary and are the reason this check exists.

Idempotency is what makes retry free
------------------------------------
A shard is a pure function of (model, model points, assumptions, horizon), so
re-running it anywhere returns the same bits. A worker that dies mid-shard
costs the shard, not the run, and the retry needs no bookkeeping beyond
"submit it again" — there is no partial state to reconcile because there is
no state.
"""

from __future__ import annotations

import hashlib
import platform
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence, Type

import numpy as np

from engine.core.model import Model
from engine.core.parallel import shard_bounds
from engine.core.results import ArrayRunResult
from engine.data.modelpoints import to_batch

#: How many values :func:`attest` probes. **Not a detail.** NumPy dispatches
#: its hand-written SIMD kernels only above a length threshold and falls back
#: to scalar code below it — and the scalar path is the one that does *not*
#: vary between microarchitectures. A short probe therefore attests the
#: wrong path and agrees everywhere.
#:
#: Measured: at 9 values the digest is identical with AVX-512 enabled and
#: disabled, which is exactly the difference the check exists to catch. At
#: this length it is not.
PROBE_LENGTH = 4096

#: The probe itself. Generated arithmetically rather than drawn from a
#: generator, so two machines can compare digests without exchanging data and
#: without depending on the RNG being identical — which is a separate
#: guarantee this check should not lean on. Spread over the ordinary range a
#: projection holds: rates, factors, fund values.
_PROBE = (np.arange(1, PROBE_LENGTH + 1, dtype=np.float64) / PROBE_LENGTH
          * 9.0 + 0.001)


class DispatchError(RuntimeError):
    """A dispatched run that could not be completed as asked."""


class ArithmeticMismatch(DispatchError):
    """Workers whose floating-point results do not agree bit for bit.

    Raised **before** any answer is produced. A run reduced across these
    workers would be arithmetically fine and reproducibly wrong: right to
    fifteen digits, different in the sixteenth, and different again next time
    if the shards land elsewhere.
    """


@dataclass(frozen=True)
class Attestation:
    """What a worker's arithmetic does, as a digest it can be compared by."""

    exact: str
    transcendental: str
    numpy: str
    machine: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            f"{self.exact}:{self.transcendental}".encode()).hexdigest()[:16]

    def describe(self) -> str:
        return (f"exact={self.exact} transcendental={self.transcendental} "
                f"numpy={self.numpy} machine={self.machine}")


def attest() -> Attestation:
    """Digest what this machine's floating-point unit actually does.

    Two digests, not one, because they fail differently. ``exact`` covers the
    operations IEEE-754 §5 requires to be correctly rounded: if that differs
    between two workers, one of them is non-conforming and the problem is far
    larger than a dispatch. ``transcendental`` covers the ones §9.2 only
    recommends, which is where real machines genuinely differ.

    The NumPy version and the platform ride along for the error message. They
    are **not** part of the digest: what matters is whether the arithmetic
    agrees, not whether the labels do, and two different builds that produce
    identical bits are identical for this purpose.
    """
    probe = _PROBE
    with np.errstate(all="ignore"):
        exact = np.concatenate([
            probe + probe[::-1], probe * probe[::-1],
            probe / probe[::-1], np.sqrt(probe),
        ])
        transcendental = np.concatenate([
            np.exp(probe), np.log(probe), probe ** probe[::-1],
            np.log1p(probe), np.expm1(probe),
        ])
    return Attestation(
        exact=hashlib.sha256(exact.tobytes()).hexdigest()[:16],
        transcendental=hashlib.sha256(
            transcendental.tobytes()).hexdigest()[:16],
        numpy=np.__version__,
        machine=f"{platform.machine()}/{platform.processor() or 'unknown'}",
    )


@dataclass(frozen=True)
class Shard:
    """One contiguous slice of the batch, and where it is to be evaluated."""

    index: int
    start: int
    stop: int

    @property
    def size(self) -> int:
        return self.stop - self.start


@dataclass
class DispatchReport:
    """What actually happened, for the registry and for a human."""

    shards: tuple = ()
    attempts: dict = field(default_factory=dict)
    attestations: dict = field(default_factory=dict)
    shard_digests: dict = field(default_factory=dict)
    retried: tuple = ()

    @property
    def workers_agreed(self) -> bool:
        return len({a.digest for a in self.attestations.values()}) <= 1

    def describe(self) -> str:
        lines = [f"{len(self.shards)} shards over "
                 f"{len(self.attestations)} workers",
                 f"  arithmetic agreed: {self.workers_agreed}"]
        if self.retried:
            lines.append(f"  retried shards: {list(self.retried)}")
        for index in sorted(self.shard_digests):
            lines.append(f"  shard {index}: {self.shard_digests[index]} "
                         f"({self.attempts.get(index, 1)} attempt(s))")
        return "\n".join(lines)


def plan_shards(n_modelpoints: int, workers: int) -> tuple:
    """The batch split into contiguous shards, in index order.

    Contiguous rather than round-robin, reusing
    :func:`engine.core.parallel.shard_bounds`, because reassembly is then a
    concatenation in index order — which is what makes the answer independent
    of which worker finished first.
    """
    if n_modelpoints < 1:
        raise DispatchError("no model points to dispatch")
    return tuple(Shard(i, start, stop) for i, (start, stop)
                 in enumerate(shard_bounds(n_modelpoints, workers)))


def _shard_digest(stacked: dict) -> str:
    parts = []
    for name in sorted(stacked):
        parts.append(name.encode())
        parts.append(np.ascontiguousarray(
            stacked[name], dtype=np.float64).tobytes())
    return hashlib.sha256(b"".join(parts)).hexdigest()[:16]


def dispatch(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    submit: Callable,
    *,
    workers: int = 2,
    outputs: Sequence[str] | None = None,
    attempts: int = 3,
    require_matching_arithmetic: bool = True,
) -> tuple:
    """Split, submit, reduce. Returns ``(result, report)``.

    ``submit(shard, payload)`` is the transport, injected rather than
    imported: ``engine/core`` stays NumPy-only (§1.4), so nothing here knows
    what HTTP is. :mod:`engine.api.worker` supplies the one that speaks to a
    remote engine instance, and the tests supply one that speaks to a local
    process — the coordinator cannot tell, which is the point.

    A shard that fails is retried up to ``attempts`` times, anywhere. That is
    safe by construction rather than by bookkeeping: a shard is a pure
    function of its inputs, so a re-run returns the same bits and there is no
    partial state to reconcile.
    """
    batch = to_batch(modelpoints)
    if getattr(model_cls, "couples_model_points", False) \
            or model_cls.pooled_names():
        raise DispatchError(
            f"{model_cls.__name__} couples its model points, so a shard "
            f"would reduce over the wrong population. Dispatching it would "
            f"give each shard a pool of itself — the same error "
            f"engine.core.runner refuses one policy at a time, one level up."
        )

    shards = plan_shards(batch.n, workers)
    report = DispatchReport(shards=shards)
    results: dict = {}
    retried = []

    for shard in shards:
        payload = {
            "model_cls": model_cls,
            "modelpoints": batch.take(shard.start, shard.stop),
            "assumptions": assumptions,
            "proj_len": proj_len,
            "outputs": list(outputs) if outputs else None,
        }
        last = None
        for attempt in range(1, attempts + 1):
            report.attempts[shard.index] = attempt
            try:
                answer = submit(shard, payload)
            except Exception as exc:      # noqa: BLE001 - retried, then raised
                last = exc
                if attempt > 1 or shard.index not in retried:
                    retried.append(shard.index)
                continue
            results[shard.index] = answer["stacked"]
            report.attestations[shard.index] = answer["attestation"]
            report.shard_digests[shard.index] = _shard_digest(answer["stacked"])
            break
        else:
            raise DispatchError(
                f"shard {shard.index} (model points {shard.start}:"
                f"{shard.stop}) failed {attempts} times; last error: {last}"
            ) from last

    report.retried = tuple(sorted(set(retried)))

    if require_matching_arithmetic and not report.workers_agreed:
        seen = {}
        for index, attestation in report.attestations.items():
            seen.setdefault(attestation.digest, []).append(index)
        raise ArithmeticMismatch(
            "workers do not agree on floating-point arithmetic, so the "
            "reduced answer would depend on which worker received which "
            "shard:\n  "
            + "\n  ".join(
                f"{digest}: shards {indices} — "
                f"{report.attestations[indices[0]].describe()}"
                for digest, indices in sorted(seen.items()))
            + "\nRFC-072: the transcendental library is implementation-"
              "defined to within an ulp, so this is expected between "
              "unlike machines rather than a fault. Dispatch to a set that "
              "attests alike, or pass require_matching_arithmetic=False and "
              "accept that the run is not bitwise-reproducible."
        )

    # Reduced by shard index, never by arrival order.
    names = sorted(results[0])
    stacked = {name: np.concatenate([results[i][name]
                                     for i in range(len(shards))], axis=1)
               for name in names}
    return ArrayRunResult(stacked=stacked, mp_ids=batch.ids), report
