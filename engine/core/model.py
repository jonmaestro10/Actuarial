"""Declarative model core: the ``@var`` DSL and the ``Model`` base class.

A model variable is one pure formula over projection time ``t``. The engine
owns evaluation order and caching; formulas own only the actuarial logic.
See docs/rfc-001-dsl.md for the full contract.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from engine.core.graph import CyclicModelError, DependencyGraph


class EvictedValueError(RuntimeError):
    """A variable reached further back than the window kept for it."""

#: Cache sentinel. `dict.get` with a default beats `try/except KeyError`
#: here because a projection is dominated by *misses* — every (variable,
#: t) is computed exactly once — and raising an exception per miss costs
#: more than the lookup it saves on a hit.
_MISS = object()


class VarSpec:
    """Metadata attached to a model variable by the ``@var`` decorator."""

    def __init__(self, fn: Callable, assumption: str | None = None,
                 pooled: bool = False):
        self.fn = fn
        self.name = fn.__name__
        self.assumption = assumption
        self.pooled = pooled
        self.doc = fn.__doc__


def var(fn: Callable | None = None, *, assumption: str | None = None):
    """Mark a method as a time-indexed model variable.

    Usable bare (``@var``) or with metadata (``@var(assumption="mortality")``).
    The body must be a pure function of ``t``, the model point, assumptions,
    and other variables — no I/O, no mutation, no dependence on evaluation
    order.
    """

    def wrap(f: Callable) -> Callable:
        f.__var_spec__ = VarSpec(f, assumption=assumption)
        return f

    return wrap(fn) if fn is not None else wrap


def pool(fn: Callable | None = None, *, assumption: str | None = None):
    """Mark a method as a **pooled** model variable.

    A ``@var`` is one formula per model point. A ``@pool`` is one formula per
    *block*: its body reduces across the model-point axis, so every policy
    sees the same value at a given ``t``. That is what a pooled
    variable-payment adjustment, a with-profits bonus declaration or an asset
    share needs, and what a per-policy formula cannot express.

    The body reduces with ``self.pool_sum``, which sums the model-point axis
    and leaves any scenario axis alone::

        @pool
        def adjustment(self, t):
            return self.pool_sum(self.assets(t)) / self.pool_sum(
                self.liability(t)
            ) - 1.0

    Everything else is unchanged: same purity rules, same caching, and the
    graph must still be acyclic. A pooled variable may read per-policy
    variables at the same ``t`` and per-policy variables may read it, but a
    per-policy variable it consumes must not, in turn, depend on it — in a
    variable-payment pool the liability being valued is the one carried
    *into* the period, not the one the adjustment produces.

    Declaring one has a real consequence for execution: the vectorized
    executor stops chunking the block, because a reduction over a chunk would
    be a reduction over the wrong population.
    """

    def wrap(f: Callable) -> Callable:
        f.__var_spec__ = VarSpec(f, assumption=assumption, pooled=True)
        return f

    return wrap(fn) if fn is not None else wrap


class Model:
    """Base class for projection models.

    ``t`` runs over ``0 .. proj_len`` inclusive. Convention: stock variables
    (in-force counts, fund values) are measured at the *start* of period
    ``t``; flow variables (claims, premiums) are amounts arising *during*
    period ``t``. ``proj_len`` is therefore one past the last period with
    cashflows, so end-of-period discounting at ``t + 1`` stays in range.
    """

    #: Set on a subclass that couples model points *without* declaring a
    #: ``@pool`` variable — an escape-hatch model doing its own reduction,
    #: say. Models with pooled variables are detected automatically and do
    #: not need this.
    couples_model_points = False

    def __init__(self, mp: Any, assumptions: Any, proj_len: int,
                 scenarios: Any = None, *, record_graph: bool = False):
        if proj_len < 1:
            raise ValueError("proj_len must be >= 1")
        self.mp = mp
        self.assumptions = assumptions
        self.proj_len = proj_len
        self.scenarios = scenarios
        self._cache: dict[tuple[str, int], Any] = {}
        #: Variables currently being evaluated, innermost last. Maintained
        #: for two reasons: a same-period cycle is `key in self._active`,
        #: caught at depth two instead of a thousand frames later, and the
        #: top of the stack is who to attribute a dependency edge to.
        self._stack: list[tuple[str, int]] = []
        self._active: set[tuple[str, int]] = set()
        #: Periods strictly before this have been dropped from the cache.
        #: -1 means nothing has been. See `prune`.
        self._evicted_before = -1
        #: Dependency edges, recorded only when asked for. Recording
        #: costs a dict lookup, a tuple and a set insert on *every*
        #: evaluation including cache hits, which is ~16% of the per-policy
        #: interpreter — measurable, and pure waste on a production run that
        #: will never look at the graph. `Model.trace` turns it on.
        #: A one-element cell rather than a plain attribute: the bound
        #: evaluators close over it, so an executor can trace the first few
        #: periods and then switch recording off for the rest of the run
        #: without rebinding anything.
        self._record = [bool(record_graph)]
        self._edges: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for name in self.var_names():
            spec = getattr(type(self), name).__var_spec__
            setattr(self, name, self._bind(spec))
        self.setup()

    def setup(self) -> None:
        """Precompute whole-projection data, once, before any ``@var`` runs.

        Some inputs are cheapest to build for the entire time axis in one
        call rather than a period at a time — survival curves off a
        fractional-age basis, discount vectors off a yield curve. Computing
        them here keeps the formulas declarative and keeps the calendar work
        out of the projection loop.

        The same rules apply as to a ``@var`` body: pure, no I/O, no
        dependence on evaluation order. Nothing set here may depend on a
        ``@var``, because none have been evaluated yet.
        """

    def at(self, slab, t: int):
        """One period out of a ``(n_policies, n_periods)`` array from
        ``setup()``, shaped to broadcast the way the executor expects.

        The stochastic executor puts model-point fields in columns so they
        broadcast against per-scenario rows; a slab column has to be shaped
        the same way or it would broadcast against the scenario axis instead.
        """
        value = slab[..., t]
        return value[..., None] if self.scenarios is not None else value

    def pool_sum(self, values):
        """Total across the model-point axis, leaving any scenario axis alone.

        The reduction the DSL sanctions inside a ``@pool`` body. NumPy's
        pairwise summation is used rather than a compensated loop: it is
        deterministic for a fixed block order and length, which is what the
        reproducibility guarantee needs, and it is fast enough to run once
        per time step over a whole block.

        A block is never chunked when it has pooled variables, so this
        always sees the entire population — see engine/core/vector.py.
        """
        import numpy as np

        totals = np.asarray(values, dtype=np.float64)
        return totals if totals.ndim == 0 else totals.sum(axis=0)

    @classmethod
    def pooled_names(cls) -> list[str]:
        """Variables that reduce across model points."""
        return [
            name
            for name in cls.var_names()
            if getattr(cls, name).__var_spec__.pooled
        ]

    @classmethod
    def var_names(cls) -> list[str]:
        names = []
        for name in dir(cls):
            attr = getattr(cls, name, None)
            if callable(attr) and hasattr(attr, "__var_spec__"):
                names.append(name)
        return sorted(names)

    def _bind(self, spec: VarSpec) -> Callable[[int], Any]:
        name = spec.name
        fn = spec.fn
        cache = self._cache
        stack = self._stack
        active = self._active
        model = self
        record = self._record
        edges = self._edges

        def evaluate(t: int):
            if not isinstance(t, int) or isinstance(t, bool):
                raise TypeError(f"{name}(t): t must be an int, got {t!r}")
            if t < 0 or t > self.proj_len:
                raise IndexError(
                    f"{name}({t}) outside projection range [0, {self.proj_len}]"
                )
            key = (name, t)
            if record[0]:
                # A node for this variable, so one that reads nothing still
                # appears; and the edge from whoever asked, carrying the
                # offset between their period and this one. Recorded on a
                # cache hit too — a dependency that is only ever served from
                # the cache is still a dependency.
                edges[name]
                if stack:
                    caller, caller_t = stack[-1]
                    edges[caller].add((name, t - caller_t))
            value = cache.get(key, _MISS)
            if value is not _MISS:
                return value
            if t < model._evicted_before:
                raise EvictedValueError(
                    f"{name}({t}) was dropped from the cache: the executor "
                    f"kept only periods from {model._evicted_before} on, "
                    "because the traced dependency graph said nothing "
                    "reached further back. This model does. Re-trace it over "
                    "enough periods to see the wider look-back, or run "
                    "without a window."
                )
            if key in active:
                raise CyclicModelError(self._cycle_message(key))
            active.add(key)
            stack.append(key)
            try:
                value = fn(self, t)
            finally:
                stack.pop()
                active.discard(key)
            cache[key] = value
            return value

        evaluate.__name__ = name
        evaluate.__doc__ = spec.doc
        return evaluate

    def restart_fields(self, t: int) -> dict:
        """Model-point fields describing this policy's state at period ``t``.

        A projection normally starts at inception. A valuation — and every
        inner projection of a nested run — starts from wherever the block
        has got to, which means a template has to be able to say what its
        state *is*: the fund, the benefit base, the in-force count, the
        attained age, the term left to run.

        A template implements this by returning the model-point fields that
        would make a fresh projection begin exactly where this one stands at
        ``t``. That it can is not an accident: the ``t == 0`` branch of
        every stock variable reads one model-point field, so the state and
        the model point are the same list.

        Restarts land on policy anniversaries only. Attained age and
        remaining term are whole years, and a template that pretended
        otherwise would be inventing a part-year age.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot be restarted mid-projection: it "
            "does not implement restart_fields(t). Implement it to return "
            "the model-point fields for the state at t."
        )

    @property
    def record_graph(self) -> bool:
        """Whether dependency edges are being recorded. Settable mid-run:
        an executor traces a few periods, reads the look-back window off the
        graph, and then stops paying for the recording."""
        return self._record[0]

    @record_graph.setter
    def record_graph(self, value: bool) -> None:
        self._record[0] = bool(value)

    def prune(self, keep_from: int) -> None:
        """Drop every cached value from before period ``keep_from``.

        A projection's memo holds every ``(variable, t)`` it ever computed,
        which for a large block is hundreds of megabytes of arrays that
        nothing will read again — the dependency graph says so, since
        ``horizon()`` is the furthest back anything reaches. Dropping them
        keeps the working set small enough to stay in cache, and that is
        worth more than the arithmetic.

        Correctness does not rest on the caller getting the window right.
        A value asked for after it was dropped raises
        :class:`EvictedValueError` naming the variable and period, rather
        than being silently recomputed — which would be correct but could
        cascade into recomputing the whole projection.
        """
        if keep_from <= self._evicted_before:
            return
        for key in [k for k in self._cache if k[1] < keep_from]:
            del self._cache[key]
        self._evicted_before = keep_from

    def _cycle_message(self, key) -> str:
        """The cycle, as the path that closed it."""
        path = self._stack[self._stack.index(key):] + [key]
        chain = " -> ".join(f"{n}({t})" for n, t in path)
        return (
            f"{key[0]} depends on itself within one period: {chain}. A "
            "variable may read an earlier period of itself — that is what a "
            "projection is — but not the period it is computing."
        )

    def graph(self) -> "DependencyGraph":
        """The dependency graph of everything evaluated so far.

        Only what has actually run is in it, so ask after a projection — or
        use :meth:`trace`, which runs one for you.
        """
        if not self._edges and not self._record[0]:
            raise RuntimeError(
                "this model was built without graph recording, so there is "
                "no graph to return. Build it with record_graph=True, or "
                f"call {type(self).__name__}.trace(...), which does."
            )
        return DependencyGraph(self._edges, pooled=self.pooled_names())

    @classmethod
    def trace(cls, mp: Any, assumptions: Any, proj_len: int = 3,
              scenarios: Any = None, names: list[str] | None = None
              ) -> "DependencyGraph":
        """Build a model's dependency graph by running a short projection.

        Three periods by default, which is the fewest that exercises both a
        variable's ``t == 0`` branch and its recursive one. A longer trace
        cannot find new edges in a well-formed model — a ``@var`` body may
        not branch on model-point data — but it costs more.
        """
        model = cls(mp, assumptions, proj_len, scenarios, record_graph=True)
        for name in names or cls.var_names():
            model.series(name)
        return model.graph()

    def series(self, name: str) -> list:
        """All values of a variable for ``t = 0 .. proj_len``.

        Evaluates in ascending ``t`` so backward references hit the cache,
        keeping recursion depth flat regardless of projection length.
        """
        fn = getattr(self, name)
        return [fn(t) for t in range(self.proj_len + 1)]

    def run(self, names: list[str] | None = None) -> dict[str, list]:
        return {name: self.series(name) for name in (names or self.var_names())}
