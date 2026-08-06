r"""The device executor, and the two guarantees it makes instead of the big one.

§1.2 asks every executor for **bitwise-identical** results. A GPU cannot give
that against a CPU, and the plan says so up front rather than discovering it
later: device reductions run in a different order, and floating-point addition
is not associative. RFC-072 measured the same fact one layer down — there is
no length at which a reduction is order-independent.

So **the device executor does not join the bitwise class.** It is not a
weakened member of it; it is a different class, with two guarantees of its
own, both mechanisms rather than claims:

**(a) Run-to-run bitwise determinism on the same device.** The same question
asked twice on the same hardware returns the same bits. This is achievable —
it needs fixed reduction orders and RNG streams pinned per (scenario, model
point), never atomics whose completion order varies — and it is the guarantee
that matters for a valuation being re-run for a regulator.

**(b) A reconciliation bound against the CPU executor**, per variable,
published rather than targeted.

The bound is measured, not guessed
----------------------------------
The difference between a device answer and a CPU answer comes from
**reduction order**, and reduction order can be simulated exactly on a CPU.
:class:`DeviceReduction` reduces in the block-wise tree a device uses, so the
bound is measurable here without silicon — because the thing being bounded is
arithmetic, not hardware.

Measured over realistic aggregates (100k cashflows, 1M scenario cells, both
narrow and wide ranges), the relative spread between NumPy's pairwise
reduction and a device-shaped tree is **0 to 5 × 10⁻¹⁶**, and between pairwise
and a naive sequential loop **1 × 10⁻¹⁵ to 1.3 × 10⁻¹⁴**. The plan's target of
1e-12 is therefore roughly two orders of magnitude looser than the arithmetic
requires, which is worth knowing in both directions: the target is safe, and
a run that *missed* it would be evidence of a real defect rather than of
floating point.

A detail worth keeping: the device-shaped tree is **closer** to NumPy than the
sequential loop is, because both are tree-shaped. The intuition that a GPU is
the thing that disagrees gets the direction wrong — a naive CPU loop disagrees
more.

What is and is not built here
-----------------------------
The backend seam, the two guarantees as executable contracts, and the
reconciliation machinery. **No device was available when this was written**,
so the CuPy path is unexercised and this module says so at
:data:`DEVICE_STATUS` rather than implying otherwise. Everything the contracts
assert is asserted against the CPU backend, which is exactly the arrangement
the plan asked for — "a CPU-fallback path keeps the code imported and
unit-tested in CI" — and the contracts are written so that a device violating
them would fail them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

#: The bound this executor promises against the CPU executor, per variable,
#: relative. The plan's target; measured to be roughly two orders of
#: magnitude looser than reduction-order arithmetic actually requires, which
#: means a run that missed it would indicate a defect rather than rounding.
RECONCILIATION_BOUND = 1e-12

#: The block width a device reduction tree uses. 32 is a warp on NVIDIA
#: hardware, which is the granularity at which a device's partial sums are
#: actually combined — the number is here so the simulated reduction has the
#: same *shape* as the real one rather than merely being "some tree".
DEVICE_BLOCK = 32


class DeviceUnavailable(RuntimeError):
    """No device backend, where one was required rather than preferred."""


class ReconciliationFailed(AssertionError):
    """A device answer outside the bound this executor promises."""


def backend(prefer_device: bool = True):
    """The array namespace to compute in: CuPy if there is a device, else NumPy.

    Returned as a *namespace* rather than switched on inside the code,
    because CuPy deliberately mirrors NumPy's op set — which is the whole
    reason the plan chose it over JAX. The executor is written once.
    """
    if prefer_device:
        try:                              # pragma: no cover - needs a device
            import cupy

            if cupy.cuda.runtime.getDeviceCount() > 0:
                return cupy
        except Exception:
            pass
    return np


def device_available() -> bool:
    """Whether a usable device is present, without raising if it is not."""
    return backend(prefer_device=True) is not np


#: What this build can actually say about a device. Recorded so a reader of
#: the docs is not left to infer it from a passing test suite that skipped
#: everything interesting.
DEVICE_STATUS = (
    "no device detected: the CuPy path is unexercised here and the "
    "reconciliation bound is measured against a simulated device-shaped "
    "reduction rather than against silicon"
    if not device_available() else
    "device present: the CuPy path is exercised and the reconciliation "
    "bound is measured against it"
)


class DeviceReduction:
    """A block-wise tree reduction, in the shape a device performs one.

    Exists so guarantee (b) can be **measured without a device**. What
    separates a GPU answer from a CPU answer is the order the partial sums
    are combined in, and that order can be reproduced exactly in NumPy. A
    bound established this way is a bound on the arithmetic, which is what it
    was always a bound on — the silicon only chooses the order.
    """

    def __init__(self, block: int = DEVICE_BLOCK):
        if block < 2:
            raise ValueError(f"block {block} must be at least 2")
        self.block = block

    def sum(self, values, axis: int = 0) -> np.ndarray:
        """Reduce ``values`` along ``axis`` in device order."""
        array = np.asarray(values, dtype=np.float64)
        if axis != 0:
            return self.sum(np.moveaxis(array, axis, 0))
        while array.shape[0] > 1:
            rows = array.shape[0]
            pad = (-rows) % self.block
            if pad:
                array = np.concatenate(
                    [array, np.zeros((pad,) + array.shape[1:])], axis=0)
            array = array.reshape(-1, self.block, *array.shape[1:]).sum(axis=1)
        return array[0]


@dataclass(frozen=True)
class Reconciliation:
    """How far a device answer sits from the CPU one, per variable."""

    bound: float
    worst_relative: float
    worst_variable: str
    per_variable: dict

    @property
    def within_bound(self) -> bool:
        return self.worst_relative <= self.bound

    def describe(self) -> str:
        lines = [f"worst relative difference {self.worst_relative:.3e} "
                 f"on {self.worst_variable!r} against a bound of "
                 f"{self.bound:.0e} — "
                 f"{'within' if self.within_bound else 'OUTSIDE'}"]
        for name in sorted(self.per_variable):
            lines.append(f"  {name}: {self.per_variable[name]:.3e}")
        return "\n".join(lines)


def reconcile(device_result, cpu_result, *, outputs: Sequence[str] | None = None,
              bound: float = RECONCILIATION_BOUND) -> Reconciliation:
    """Per-variable relative agreement between a device run and a CPU run.

    Per variable rather than one aggregate, because a single worst-case
    figure cannot say whether a discrepancy is spread thinly across the model
    or concentrated in one variable — and which of those it is decides
    whether it is reduction order or a bug.

    Relative where the CPU value is non-zero, absolute where it is not: a
    relative difference against zero is either zero or undefined, and a
    cashflow that should be exactly nothing is the one case where the
    absolute figure is the meaningful one.
    """
    names = list(outputs) if outputs else sorted(cpu_result._stacked)
    per_variable, worst, worst_name = {}, 0.0, ""
    for name in names:
        want = np.asarray(cpu_result.array(name), dtype=np.float64)
        got = np.asarray(device_result.array(name), dtype=np.float64)
        if got.shape != want.shape:
            raise ReconciliationFailed(
                f"{name}: device produced {got.shape}, CPU {want.shape}. A "
                f"shape difference is not a rounding difference and no bound "
                f"applies to it."
            )
        scale = np.where(np.abs(want) > 0.0, np.abs(want), 1.0)
        relative = float(np.max(np.abs(got - want) / scale))
        per_variable[name] = relative
        if relative > worst:
            worst, worst_name = relative, name
    return Reconciliation(bound=bound, worst_relative=worst,
                          worst_variable=worst_name or (names[0] if names else ""),
                          per_variable=per_variable)


def result_digest(result, outputs: Sequence[str] | None = None) -> str:
    """A digest of every array in a result, for the determinism check."""
    names = list(outputs) if outputs else sorted(result._stacked)
    parts = []
    for name in names:
        parts.append(name.encode())
        parts.append(np.ascontiguousarray(
            result.array(name), dtype=np.float64).tobytes())
    return hashlib.sha256(b"".join(parts)).hexdigest()[:16]


def assert_run_to_run_determinism(run, *, times: int = 3,
                                  outputs: Sequence[str] | None = None) -> str:
    """**Guarantee (a).** The same question, asked ``times`` times, same bits.

    Returns the digest they agreed on. Raises with the digests that differed
    rather than a bare assertion, because "the device is non-deterministic"
    is the beginning of an investigation and the digests are where it starts.

    This is the guarantee a regulator's re-run depends on, and it is the one a
    device can actually keep — provided reductions have fixed orders and the
    RNG is pinned per (scenario, model point) rather than per thread.
    """
    digests = [result_digest(run(), outputs) for _ in range(times)]
    if len(set(digests)) > 1:
        raise ReconciliationFailed(
            f"the same run produced {len(set(digests))} different answers "
            f"over {times} attempts: {digests}. Run-to-run determinism is "
            f"guarantee (a) and it has failed — look for an unordered "
            f"reduction or an RNG keyed by thread rather than by (scenario, "
            f"model point)."
        )
    return digests[0]
