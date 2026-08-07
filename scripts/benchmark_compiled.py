"""The compiled executor against the vectorized one, with the split shown.

Run: ``python scripts/benchmark_compiled.py``

Reports three numbers per template rather than one, because one would be
misleading. A compiled run is a **hoist pre-pass** in NumPy plus a **fused
kernel**, and those scale differently: the kernel is worth an order of
magnitude, and the pre-pass is serial work the kernel cannot remove. Quoting
only the end-to-end figure hides which half to attack next; quoting only the
kernel figure would be a benchmark of the part that got fast.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from engine.core.compiled import cached_plan, compile_kernel, run_compiled
from engine.core.vector import run_vectorized
from engine.data.modelpoints import to_batch

POLICIES = 50_000
REPEATS = 3


def _time(call, repeats=REPEATS):
    call()
    start = time.perf_counter()
    for _ in range(repeats):
        call()
    return (time.perf_counter() - start) / repeats


def measure(name, model_cls, modelpoints, assumptions, proj_len,
            policies=POLICIES):
    prototype = list(modelpoints)
    batch = to_batch([prototype[i % len(prototype)] for i in range(policies)])
    compilation = cached_plan(model_cls, batch, assumptions, proj_len)
    if not compilation.compilable:
        return {"name": name, "compiled": False,
                "why": compilation.refusals[0]}
    kernel = compile_kernel(compilation)

    def hoist_pass():
        result = run_vectorized(model_cls, batch, assumptions, proj_len,
                                outputs=list(compilation.hoisted))
        return {n: np.ascontiguousarray(result.array(n))
                for n in compilation.hoisted}

    slabs = {n: np.empty((proj_len + 1, batch.n)) for n in compilation.fused}
    hoisted = hoist_pass() if compilation.hoisted else {}
    arguments = (
        [np.ascontiguousarray(getattr(batch, f), dtype=np.float64)
         for f in compilation.fields]
        + [hoisted[n] for n in compilation.hoisted]
        + [np.array([v.get(t, 0.0) for t in range(proj_len + 1)])
           for _, v in compilation.scalars]
        + [slabs[n] for n in compilation.fused])

    vectorized = _time(lambda: run_vectorized(model_cls, batch, assumptions,
                                              proj_len))
    pre_pass = _time(hoist_pass) if compilation.hoisted else 0.0
    kernel(batch.n, proj_len, *arguments)
    fused = _time(lambda: kernel(batch.n, proj_len, *arguments))

    # RFC-082: end to end is **measured**, not modelled. It used to be
    # `vectorized / (pre_pass + fused)`, which is a sum of two things timed
    # separately — it silently assumed the two phases are all there is, and
    # after interleaving they are not even sequential. A headline figure
    # derived from two other figures is a figure nobody ran.
    end_to_end = _time(lambda: run_compiled(model_cls, batch, assumptions,
                                            proj_len))
    return {
        "name": name, "compiled": True,
        "policies": policies, "periods": proj_len,
        "fused_vars": len(compilation.fused),
        "hoisted_vars": len(compilation.hoisted),
        "interleaved": compilation.interleaves,
        "recomputed_vars": len(compilation.recomputed),
        "vectorized_ms": vectorized * 1000,
        "pre_pass_ms": pre_pass * 1000,
        "kernel_ms": fused * 1000,
        "compiled_ms": end_to_end * 1000,
        "end_to_end_speedup": vectorized / end_to_end,
        "kernel_speedup": vectorized / fused,
        "pre_pass_share": pre_pass / vectorized,
    }


def main() -> int:
    try:
        from engine.report.evidence import default_specimens
    except ImportError:
        print("the worked examples need the [api] extra")
        return 1

    print(f"{'template':20} {'fused':>5} {'hoist':>5} {'recomp':>6} "
          f"{'vector':>9} {'compiled':>9} {'kernel':>8} {'end-to-end':>11} "
          f"{'kernel':>8}")
    print("-" * 96)
    rows = []
    for specimen in default_specimens():
        if specimen.get("scenarios") is not None:
            continue
        name = specimen.get("name") or specimen["model_cls"].__name__
        row = measure(name, specimen["model_cls"], specimen["modelpoints"],
                      specimen["assumptions"], specimen["proj_len"])
        rows.append(row)
        if not row["compiled"]:
            print(f"{name[:20]:20} {'—':>5} {'—':>5}   not compiled: "
                  f"{row['why'][:34]}")
            continue
        print(f"{name[:20]:20} {row['fused_vars']:>5} {row['hoisted_vars']:>5} "
              f"{row['recomputed_vars']:>6} "
              f"{row['vectorized_ms']:>8.1f}ms {row['compiled_ms']:>8.1f}ms "
              f"{row['kernel_ms']:>7.1f}ms {row['end_to_end_speedup']:>10.2f}x "
              f"{row['kernel_speedup']:>7.1f}x")

    ran = [r for r in rows if r["compiled"]]
    if ran:
        print("-" * 82)
        print(f"{'median':20} {'':>5} {'':>5} {'':>9} {'':>9} {'':>8} "
              f"{np.median([r['end_to_end_speedup'] for r in ran]):>10.2f}x "
              f"{np.median([r['kernel_speedup'] for r in ran]):>7.1f}x")
        print(f"\nThe kernel is worth an order of magnitude. End-to-end is "
              f"lower because the hoist\npre-pass is "
              f"{np.median([r['pre_pass_share'] for r in ran]):.0%} of the "
              f"vectorized runtime and the kernel cannot remove it.\n\n"
              f"RFC-082 tried removing it by interleaving the pre-pass with "
              f"the kernel per period,\nand measured that it does not pay: "
              f"splitting one fused kernel into one per segment\ngives back "
              f"the fusion, and per-period dispatch across segments and "
              f"chunks costs more\nthan the recomputation it avoids. The "
              f"'recomp' column is the work a pre-pass\nduplicates, and it "
              f"does not predict the outcome — which is why the answer had "
              f"to be\nmeasured. What would change it is a smaller hoisted "
              f"set, not a better schedule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
