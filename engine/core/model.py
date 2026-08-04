"""Declarative model core: the ``@var`` DSL and the ``Model`` base class.

A model variable is one pure formula over projection time ``t``. The engine
owns evaluation order and caching; formulas own only the actuarial logic.
See docs/rfc-001-dsl.md for the full contract.
"""

from __future__ import annotations

from typing import Any, Callable


class VarSpec:
    """Metadata attached to a model variable by the ``@var`` decorator."""

    def __init__(self, fn: Callable, assumption: str | None = None):
        self.fn = fn
        self.name = fn.__name__
        self.assumption = assumption
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


class Model:
    """Base class for projection models.

    ``t`` runs over ``0 .. proj_len`` inclusive. Convention: stock variables
    (in-force counts, fund values) are measured at the *start* of period
    ``t``; flow variables (claims, premiums) are amounts arising *during*
    period ``t``. ``proj_len`` is therefore one past the last period with
    cashflows, so end-of-period discounting at ``t + 1`` stays in range.
    """

    #: Set on a subclass whose variables reduce across the model-point axis
    #: — a pooled bonus or crediting rate, say. The vectorized executor
    #: keeps such a block whole instead of chunking it, because a chunk
    #: would reduce over the wrong population. Nothing in the library sets
    #: this yet; see docs/vpla-review.md §7.1.
    couples_model_points = False

    def __init__(self, mp: Any, assumptions: Any, proj_len: int,
                 scenarios: Any = None):
        if proj_len < 1:
            raise ValueError("proj_len must be >= 1")
        self.mp = mp
        self.assumptions = assumptions
        self.proj_len = proj_len
        self.scenarios = scenarios
        self._cache: dict[tuple[str, int], Any] = {}
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

    @classmethod
    def var_names(cls) -> list[str]:
        names = []
        for name in dir(cls):
            attr = getattr(cls, name, None)
            if callable(attr) and hasattr(attr, "__var_spec__"):
                names.append(name)
        return sorted(names)

    def _bind(self, spec: VarSpec) -> Callable[[int], Any]:
        def evaluate(t: int):
            if not isinstance(t, int) or isinstance(t, bool):
                raise TypeError(f"{spec.name}(t): t must be an int, got {t!r}")
            if t < 0 or t > self.proj_len:
                raise IndexError(
                    f"{spec.name}({t}) outside projection range [0, {self.proj_len}]"
                )
            key = (spec.name, t)
            if key not in self._cache:
                self._cache[key] = spec.fn(self, t)
            return self._cache[key]

        evaluate.__name__ = spec.name
        evaluate.__doc__ = spec.doc
        return evaluate

    def series(self, name: str) -> list:
        """All values of a variable for ``t = 0 .. proj_len``.

        Evaluates in ascending ``t`` so backward references hit the cache,
        keeping recursion depth flat regardless of projection length.
        """
        fn = getattr(self, name)
        return [fn(t) for t in range(self.proj_len + 1)]

    def run(self, names: list[str] | None = None) -> dict[str, list]:
        return {name: self.series(name) for name in (names or self.var_names())}
