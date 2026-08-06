"""Run a model over a set of model points.

Phase 0 executor: one interpreted model instance per model point, sequential.
The interface (model class + model points + assumptions in, RunResult out) is
the stable contract; the vectorized executor will slot in behind it.

**A pooled model is refused here.** One instance per model point means a
``@pool`` body's ``pool_sum`` reduces over one policy, so every policy would
see a pool consisting of itself — and it would *run*, returning plausible
numbers nobody could tell from the real ones. RFC-061 turns that into an
error, the same way :func:`engine.core.parallel.run_parallel` already
refuses to shard one.

A block of exactly one model point is allowed, because there the pool of one
is the same reduction in both executors, bit for bit — which is what lets
the pooled templates stay inside the dual-executor equivalence class for
everything except the reduction itself. See tests/test_pooled.py.
"""

from __future__ import annotations

from typing import Any, Iterable, Type

from engine.core.model import Model
from engine.core.results import RunResult


class PooledBlockError(ValueError):
    """A pooled model asked to run one policy at a time over a block of many."""


def check_per_policy(model_cls: Type[Model], n_modelpoints: int) -> None:
    """Refuse a block whose pooled reduction the interpreted executor cannot make.

    Judged on the model rather than on the requested outputs. A per-policy
    variable may read a pooled one, so "these outputs happen not to be
    pooled" is not the same statement as "nothing pooled is evaluated", and
    the difference is a graph walk whose answer nobody would check.
    """
    pooled = list(model_cls.pooled_names())
    coupled = getattr(model_cls, "couples_model_points", False)
    if n_modelpoints <= 1 or not (pooled or coupled):
        return
    reason = (f"declares pooled variable(s) {pooled}" if pooled
              else "sets couples_model_points")
    raise PooledBlockError(
        f"{model_cls.__name__} {reason}, which reduce across the block — and "
        f"the interpreted executor evaluates one policy at a time, so each "
        f"of these {n_modelpoints} policies would see a pool of itself. Run "
        f"it with run_vectorized, which keeps the block whole. (One model "
        f"point is permitted: a pool of one is the same reduction either "
        f"way.)"
    )


def run(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
) -> RunResult:
    points = list(modelpoints)
    if not points:
        raise ValueError("no model points supplied")
    check_per_policy(model_cls, len(points))
    per_mp = []
    mp_ids = []
    for i, mp in enumerate(points):
        model = model_cls(mp=mp, assumptions=assumptions, proj_len=proj_len)
        per_mp.append(_as_float64(model.run(outputs)))
        mp_ids.append(getattr(mp, "id", i))
    return RunResult(per_mp=per_mp, mp_ids=mp_ids)


def _as_float64(series_by_name: dict) -> dict:
    """Every recorded series as ``float64``, which is the executors' contract.

    ``engine/core/vector.py`` stores into a ``float64`` slab and coerces
    every value on the way in, so a variable whose formula returns an
    integer — an attained age, a duration count — is a float by the time any
    caller sees it. The interpreted executor kept whatever the formula
    happened to produce, so the same variable came back ``int64``.

    Equal numbers, different dtype, and therefore a different
    ``results_digest``: the same failure mode as RFC-069's spurious policy
    axis, one layer down and invisible until RFC-070 removed the exception
    that was hiding it. ``PayoutAnnuity.age`` is the instance.

    Coerced in the executor rather than in :meth:`Model.series`, which keeps
    the model honest about what its formulas return — and which is exactly
    where ``vector.py`` does it, so the two executors state the same
    contract in the same place.
    """
    return {name: [float(value) for value in values]
            for name, values in series_by_name.items()}
