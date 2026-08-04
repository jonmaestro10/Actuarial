"""Nested stochastic projection.

PLAN.md §4.4 calls this "the real killer workload — VA/VPLA hedging, VM-21,
SII internal models", and §8 makes a nested-stochastic prototype a Phase 2
exit. It is the workload that separates an actuarial platform from a
projection script, and it is where every naive implementation falls over on
cost.

The shape of the problem
------------------------
An **outer** projection runs the block under real-world scenarios. At
selected times along each outer path, the guarantees have to be *valued* —
which needs a second, risk-neutral projection starting from the state that
outer path has reached. Outer scenarios times valuation dates times inner
scenarios is a number with three factors in it, and each one is in the
hundreds.

What makes it tractable
-----------------------
Not cleverness — batching. At a given valuation time, every outer path has
reached *some* state, and those states are just model points. So one inner
run values all of them at once: ``n_model_points x n_outer`` restarted
policies against ``n_inner`` scenarios, in the same slab the stochastic
executor already knows how to fill.

The cost is therefore **one inner projection per valuation time**, not one
per outer node, and :class:`NestedRun` reports the count rather than leaving
it to be guessed at.

Restarting is exact, not approximate
------------------------------------
The hand-off is :meth:`engine.core.model.Model.restart_fields` — the state a
template holds at ``t``, expressed as the model-point fields a fresh
projection would start from. It is exact because the ``t == 0`` branch of
every stock variable reads exactly one model-point field, so the state and
the model point are the same list of numbers.

tests/test_nested.py holds that up directly: restart a contract part way
through and project it forward on the tail of the same scenario, and it
reproduces the straight-through projection **bitwise**, ratcheting benefit
base and dynamic lapse included. If that failed, every number here would be
wrong in a way no amount of scenario count could fix.

Common random numbers, deliberately
-----------------------------------
Every outer node at a given valuation time is valued against the **same**
inner scenarios. That is a choice, not an accident: the interesting quantity
is usually how the guarantee cost *differs* between outer states, and
independent inner draws would bury that difference under sampling noise that
has nothing to do with the states being compared. Different valuation times
get independent streams, so the noise does not accumulate along a path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from engine.core.model import Model
from engine.core.stochastic import build_stochastic_model
from engine.core.vector import TRACE_PERIODS
from engine.data.modelpoints import ModelPointBatch, to_batch
from engine.data.scenarios import ScenarioSet


@dataclass(frozen=True)
class NestedRun:
    """What a nested run produced, and what it cost to produce it."""

    #: ``(n_valuation_times, n_model_points, n_outer)`` — the inner mean.
    values: np.ndarray
    #: Monte Carlo standard error of each entry, same shape. Reported next
    #: to the value rather than left to be inferred: an inner mean over 200
    #: scenarios is an estimate, and a reader who cannot see its error bar
    #: will read it as a number.
    stderr: np.ndarray
    valuation_times: tuple
    n_model_points: int
    n_outer: int
    n_inner: int
    measure: str

    @property
    def inner_projections(self) -> int:
        """Inner runs performed — one per valuation time, because the outer
        states at a given time are batched into a single projection."""
        return len(self.valuation_times)

    @property
    def inner_cells(self) -> int:
        """Policy-scenario cells evaluated across all inner runs. The
        honest size of the job."""
        return (
            self.inner_projections * self.n_model_points * self.n_outer
            * self.n_inner
        )

    def at(self, valuation_time: int) -> np.ndarray:
        """The ``(n_model_points, n_outer)`` values at one valuation time."""
        try:
            return self.values[self.valuation_times.index(valuation_time)]
        except ValueError:
            raise KeyError(
                f"no valuation at period {valuation_time}; this run valued "
                f"at {list(self.valuation_times)}"
            ) from None

    def summary(self) -> str:
        return (
            f"{self.measure}: {self.n_model_points} model points x "
            f"{self.n_outer} outer x {self.n_inner} inner at "
            f"{self.inner_projections} valuation times = "
            f"{self.inner_cells:,} inner cells"
        )


def risk_neutral_inner(rate: float, vol: float, n_scenarios: int,
                       seed: int) -> Callable[[int, int], ScenarioSet]:
    """An inner scenario factory under the risk-neutral measure.

    ``drift = log(1 + rate)`` makes ``E[1 + r] = 1 + rate``, which is what
    makes the inner mean a *price* rather than an expectation under some
    other measure. `engine.data.esg.check_risk_neutral` will confirm it on
    any set this produces.

    The stream is seeded from ``(seed, valuation_time)``: independent across
    valuation times, and identical across the outer nodes valued at one —
    see the module docstring on common random numbers.
    """
    drift = float(np.log1p(rate))

    def build(valuation_time: int, periods: int) -> ScenarioSet:
        return ScenarioSet.lognormal(
            n_scenarios, periods, drift=drift, vol=vol,
            seed=seed * 1_000_003 + valuation_time,
        )

    return build


def _restart_batch(model: Model, batch: ModelPointBatch, t: int,
                   n_outer: int) -> ModelPointBatch:
    """Flatten ``(model point, outer scenario)`` states into model points.

    The whole trick. A state is a model point, so ``n_mp`` policies on
    ``n_outer`` paths are ``n_mp * n_outer`` policies — and one projection
    values them all.
    """
    fields = model.restart_fields(t)
    flat = {}
    for name, value in fields.items():
        value = np.asarray(value)
        if value.dtype == object:
            value = np.broadcast_to(value.reshape(-1, 1), (batch.n, n_outer))
        else:
            value = np.broadcast_to(value, (batch.n, n_outer))
        flat[name] = np.ascontiguousarray(value).reshape(-1)
    ids = [f"{i}@{s}" for i in batch.ids for s in range(n_outer)]
    return ModelPointBatch(flat, ids)


def _discounted_total(model: Model, name: str, timing: str,
                      discount: str, proj_len: int, shape) -> np.ndarray:
    """Present value of one variable over a whole projection.

    Accumulated inside the forward loop, with the memo pruned behind it, so
    an inner run's working set stays bounded however many cells it has. A
    nested job that materialised every period of every inner scenario would
    run out of memory long before it ran out of patience.
    """
    total = np.zeros(shape, dtype=np.float64)
    offset = 1 if timing == "end" else 0
    window = None
    for t in range(proj_len + 1):
        flow = np.asarray(getattr(model, name)(t), dtype=np.float64)
        factor = np.asarray(
            getattr(model, discount)(min(t + offset, proj_len)),
            dtype=np.float64,
        )
        total += flow * factor
        if t == TRACE_PERIODS:
            window = model.graph().horizon()
            model.record_graph = False
        if window is not None:
            model.prune(t - window)
    return total


def nested_stochastic(
    model_cls: type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    *,
    outer: ScenarioSet,
    inner: Callable[[int, int], ScenarioSet],
    valuation_times: Sequence[int],
    proj_len: int,
    measure: str,
    timing: str = "end",
    discount: str = "v",
) -> NestedRun:
    """Value ``measure`` at each valuation time along every outer path.

    ``measure`` names a ``@var`` — a cashflow — and the inner value is its
    present value from the valuation date, averaged over inner scenarios. A
    bespoke measure means writing a ``@var`` for it, which is what the DSL
    is for; passing a Python callable instead would put arbitrary code
    inside the hot loop and outside the dependency graph.

    ``timing`` says whether the flow falls at the start of its period or the
    end, matching the discounting convention the templates already use.
    """
    if timing not in ("start", "end"):
        raise ValueError(f"timing must be 'start' or 'end', got {timing!r}")
    times = tuple(int(t) for t in valuation_times)
    if not times:
        raise ValueError("no valuation times")
    if any(t < 0 or t > proj_len for t in times):
        raise ValueError(
            f"valuation times {times} outside the projection [0, {proj_len}]"
        )
    if outer.horizon < proj_len:
        raise ValueError(
            f"outer horizon {outer.horizon} shorter than the projection "
            f"{proj_len}"
        )

    batch = to_batch(modelpoints)
    outer_model = build_stochastic_model(
        model_cls, batch, assumptions, outer, proj_len
    )

    values, errors = [], []
    n_inner = 0
    for t in times:
        remaining = proj_len - t
        if remaining < 1:
            raise ValueError(
                f"valuation at period {t} leaves no projection to value; "
                f"the outer projection ends at {proj_len}"
            )
        inner_batch = _restart_batch(outer_model, batch, t, outer.n_scenarios)
        scenarios = inner(t, remaining + 1)
        n_inner = scenarios.n_scenarios
        if scenarios.horizon < remaining:
            raise ValueError(
                f"inner scenarios at period {t} cover {scenarios.horizon} "
                f"periods, the remaining projection needs {remaining}"
            )
        shifted = assumptions.at_year(assumptions.years_elapsed(t)) if hasattr(
            assumptions, "at_year"
        ) else assumptions
        inner_model = build_stochastic_model(
            model_cls, inner_batch, shifted, scenarios, remaining
        )
        totals = _discounted_total(
            inner_model, measure, timing, discount, remaining,
            (inner_batch.n, scenarios.n_scenarios),
        )
        per_state = totals.reshape(batch.n, outer.n_scenarios, -1)
        values.append(per_state.mean(axis=2))
        errors.append(
            per_state.std(axis=2, ddof=1) / np.sqrt(scenarios.n_scenarios)
            if scenarios.n_scenarios > 1
            else np.zeros_like(values[-1])
        )

    return NestedRun(
        values=np.stack(values),
        stderr=np.stack(errors),
        valuation_times=times,
        n_model_points=batch.n,
        n_outer=outer.n_scenarios,
        n_inner=n_inner,
        measure=measure,
    )
