# RFC-076: Two guarantees instead of the big one, and a bound measured without silicon

Status: **implemented (machinery), unmeasured (device)** —
`engine/core/gpu.py`, `tests/test_gpu.py`, `scripts/benchmark_gpu.py`

**No CUDA device was available when this was written.** The backend seam, the
two guarantees and the reconciliation machinery are built and tested; the
CuPy path is unexercised, and the module says so at `DEVICE_STATUS` rather
than leaving a reader to infer it from skip markers. What is missing is the
measurement, not the machinery.

## Summary

§1.2 asks every executor for bitwise-identical results, and a GPU cannot give
that against a CPU: device reductions run in a different order, and
floating-point addition is not associative. RFC-072 measured the same fact one
layer down — there is no length at which a reduction is order-independent.

So **the device executor does not join the bitwise class.** It is not a
weakened member of it; it is a different class with two guarantees of its own,
both mechanisms rather than claims:

**(a) Run-to-run bitwise determinism on the same device.** The same question
asked twice on the same hardware returns the same bits. Achievable — it needs
fixed reduction orders and RNG streams pinned per (scenario, model point),
never atomics whose completion order varies — and it is the guarantee a
regulator's re-run actually depends on.

**(b) A reconciliation bound against the CPU executor**, per variable.

The plan called this posture "itself the beyond-parity move: incumbents'
grids publish no reproducibility statement at all." That still holds, and it
now has numbers behind it.

## The bound is measured, not guessed — and it can be, without a device

What separates a device answer from a CPU answer is **the order partial sums
are combined in**, and that order is reproducible in NumPy. `DeviceReduction`
reduces in the block-wise tree a device uses — block width 32, a warp, so the
tree has the real shape rather than merely being *a* tree.

That makes the bound a bound on the **arithmetic**, which is what it was
always a bound on. The silicon only chooses the order.

Measured over realistic aggregates:

| comparison | relative spread |
|---|---|
| NumPy pairwise vs device-shaped tree | 0 – 1.8 × 10⁻¹⁵ |
| NumPy pairwise vs naive sequential loop | 1 × 10⁻¹⁵ – 1.3 × 10⁻¹⁴ |

On the stochastic slabs B3 targets — up to 200 million cells — the worst
spread is **1.8 × 10⁻¹⁵**, giving the plan's 1 × 10⁻¹² target about **560×**
headroom.

That is worth knowing in both directions. The target is safe; and a device run
that *missed* it would be evidence of a real defect rather than of floating
point, which is exactly what a bound is for.

## The intuition it corrects

**A device-shaped tree is closer to NumPy than a naive CPU loop is**, because
both are trees. "The GPU is the thing that disagrees" gets the direction
wrong — a sequential loop on the CPU disagrees by an order of magnitude more.

Asserted, not just observed: the test fails if the tree ever disagrees with
NumPy *more* than the sequential loop does, because the reasoning behind the
bound assumed the opposite.

## A benchmark that says what it could not measure

`scripts/benchmark_gpu.py` prints the device status first and, on a machine
without one, lists explicitly what was **not** measured: device throughput,
the bound against real silicon, and run-to-run determinism on a device. A
benchmark that silently omitted the device row would read as though the
device were slow.

It also stopped using a uniform-positive slab. Similar positive numbers reduce
to the same bits in any order, so a benchmark on one reports a spread of
exactly zero and proves nothing — the cancellation between large
opposite-signed partials is where reduction order actually shows. The first
version reported `0.00e+00` on every workload, which looked like a strong
result and was an absence of evidence.

## Two smaller decisions

**Reconciliation is per variable, with the worst named.** A single aggregate
cannot say whether a discrepancy is spread thinly across the model or
concentrated in one variable — and which of those it is decides whether it is
reduction order or a bug. Relative where the CPU value is non-zero, absolute
where it is not, for the same reason RFC-051's `agreement` splits them.

**A shape difference is not a rounding difference** and no bound applies to
it. Reporting it as a large relative difference would invite someone to widen
the bound until it passed.

## Acceptance

`tests/test_gpu.py` — 9 tests, 8 of which run without a device and one that
is skipped until there is one. The skipped test is written so the first
machine with a device runs it unchanged: the contracts are the same
contracts.

Guarantee (a) is asserted, including its failure path — a non-deterministic
run is reported with the digests, because "the device is non-deterministic" is
the beginning of an investigation and the digests are where it starts.
Guarantee (b) is asserted at the bound, above it, and on the shape mismatch
that is not a rounding question at all.

And `DEVICE_STATUS` is asserted to say which case this build is in, because a
passing suite that skipped everything interesting looks exactly like a passing
suite.

## What B3 still owes

The device kernels themselves, and the profile that was supposed to open this
RFC. The plan gates B3 on "the profiling data from the compiled executor
decides which stochastic slabs justify a device" — RFC-074 produced that
profile for the *deterministic* path and found the hoist pre-pass dominates.
The equivalent measurement for the stochastic slabs, which is what a device
would actually accelerate, has not been made, and making it needs a device to
be worth anything.
