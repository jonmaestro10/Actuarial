# RFC-008: Scale-out, and where the reduction has to happen

Status: **implemented for cores on one machine** — `engine/core/parallel.py`,
`scripts/benchmark_parallel.py`

## Summary

PLAN.md §4.3 asks for batches sharded across cores and nodes, "with results
reduced as streaming aggregations"; §8 lists multi-node scale-out among the
Phase 2 exits. The clause about reduction turns out to be the whole
sentence, and the measurements below are why.

## What is here and what is not

The hard part of scale-out is not the dispatch mechanism. It is deciding
what may be split, proving the split cannot move a number, and reducing the
pieces back in an order that does not depend on which finished first. All
three are here.

Cross-machine dispatch is **not**. Ray or an equivalent would replace
`ProcessPoolExecutor` and nothing else in this module — but saying that is
not the same as having done it, and this runs on one machine.

## Why sharding is safe, and where it is not

The same argument that licenses chunking: model points are independent, so
evaluating one has no effect on any other. A shard is a chunk that happens
to live in another process, and per-policy results are **bitwise** identical
for any number of workers.

The argument fails for exactly the models where chunking is already
disabled. A `@pool` variable reduces across the model-point axis, so a
reduction over a shard would be a reduction over the wrong population.
Those are **refused**, because a pooled model sharded four ways produces
plausible numbers that are wrong.

## The measurement that shaped the module

Sharding a block and shipping **per-policy series** back is a loss at every
size measured, on four cores:

| policies | serial | per-policy shards | speedup |
|---|---|---|---|
| 10,000 | 150 ms | 170 ms | 0.89× |
| 40,000 | 537 ms | 559 ms | 0.96× |
| 100,000 | 1352 ms | 5340 ms | **0.25×** |

The results are the payload. Four outputs over 61 periods for 100,000
policies is nearly 200 MB going back through pipes, and moving it costs
several times the arithmetic that produced it. It gets *worse* with size,
which is the opposite of what scale-out is for.

Sharding and **reducing in the worker** is a different picture:

| policies | serial | reduced in worker | speedup |
|---|---|---|---|
| 10,000 | 150 ms | 68 ms | 2.20× |
| 40,000 | 537 ms | 246 ms | 2.19× |
| 100,000 | 1352 ms | 547 ms | **2.47×** |

Same shards, same arithmetic. What changed is that 61 numbers per output
cross the process boundary instead of 61 × 100,000.

So `run_parallel` exists but is documented as what it is — the per-policy
form, useful when a worker writes its own results somewhere, which is the
cross-machine case, and a loss when it has to hand them back.
`run_parallel_totals` is the one that pays on a single machine.

2.5× on four cores is not linear. The residue is the fork, the pickle of the
model points going out, and the import of the engine in each worker; on a
projection of this length those are a fixed cost that four-way splitting
cannot amortise away. A longer projection or a heavier basis would amortise
it better, which is the direction real workloads go.

## One honest caveat about determinism

Per-policy results are bitwise across worker counts. **Block totals are
not.**

Summing a shard and then summing the shard totals regroups the additions, so
a four-worker total can differ from a two-worker total in the last bit. On
the blocks measured here the difference is exactly zero, and the test bounds
it at 1e-14 relative rather than asserting equality.

A given worker count is exactly reproducible — shards are contiguous and
reduced in shard order, never in completion order. But a change of worker
count is not a change of question, so RFC-003's determinism claim needs the
worker count recorded alongside the run id. Stated here rather than
discovered later.

## A fix that fell out

`MortalityTable` held its rates in a `MappingProxyType`, which does not
pickle — so an assumption set could not be sent to a worker process, cached,
or shipped anywhere. It now round-trips through `__getstate__`/`__setstate__`
and comes back as a read-only view, with the same fingerprint. The proxy
exists to stop the table being mutated in place; it is a view over a dict
either way, so handing the dict across costs nothing.

## Not in scope

- **Cross-machine dispatch.** The sharding, the safety argument and the
  reduction are what a Ray runner would need first; the runner itself is
  not here.
- **Sharding the scenario axis.** A stochastic run could be split by
  scenario as well as by model point. Scenarios are independent in exactly
  the same way, so the argument carries — but nothing has measured whether
  it pays, and the per-policy result above is a warning against assuming.
- **Shared memory for results.** Would rescue the per-policy path on one
  machine, and is the wrong shape for the cross-machine case that path
  exists to serve.
