"""The variable dependency graph.

PLAN.md §2.1 says the engine resolves calculation order from the dependency
graph, §4.2 wants that graph traced once per model and fused into compiled
kernels, and §7 wants every number traceable to the formula that produced
it. All three want the same object, and the engine has been getting by
without it: evaluation order emerged from recursive calls into a memo, which
works but leaves the graph implicit — unqueryable, unprintable, and
undetectable when it contains a cycle.

This is that graph. It does three jobs today and exists to make a fourth
possible:

1. **Cycle detection.** A variable that depends on itself within a period
   used to blow the Python stack a thousand frames later, with a traceback
   that named the recursion rather than the mistake. It now raises at depth
   two and prints the cycle. docs/rfc-001-dsl.md listed this as an open
   question.
2. **Lineage.** What does ``claims`` actually depend on, and what would
   changing ``q_x`` move? Both directions, transitively, as a query rather
   than as a reading exercise.
3. **A picture.** ``to_mermaid()`` renders the model, which is the fastest
   review of a template anybody has yet had.
4. **The prerequisite for compilation.** A topological order over the
   same-period edges is exactly what §4.2's "trace the graph, topologically
   sort, fuse into kernels" needs first. This module does the tracing and
   the sorting. It does **not** fuse anything, and calling it kernel fusion
   would be a claim the code cannot support.

Traced by running, not by reading
---------------------------------
The graph is recorded while the model evaluates, not parsed out of the
source. That is not laziness — it is the only approach that works here.
``TermLife.pols_if`` calls ``self._survivors(t - 1)``, which calls
``self.assumptions.decrements.split`` on ``self._decrements(t)``, which is
where ``q_x`` and ``lapse_rate`` actually get read. A static scan of
``pols_if``'s body sees a helper method and nothing else; running it sees
the truth.

The cost is that the graph describes the path the model *took*, so a
variable behind an indicator that happened to be zero for every model point
traced is still recorded — the indicator is evaluated either way, which is
the whole point of indicator style. A branch that never ran would be missed,
but a ``@var`` body is not allowed to branch on model-point data.

Time offsets
------------
Every edge carries the offset between the reading period and the period
read: ``0`` for a dependency within the same period, ``-1`` for last
period's value, and so on. That distinction is the difference between a
cycle and a recursion. ``pols_if`` reading ``pols_if`` at ``t - 1`` is the
projection doing its job; reading it at ``t`` is a model that cannot be
evaluated. Only the same-period edges are sorted, and only they can form a
cycle.
"""

from __future__ import annotations

from typing import Iterable, Mapping


class CyclicModelError(RuntimeError):
    """A variable depends on itself within one period."""


class DependencyGraph:
    """Which variables read which, and across what time offset.

    ``edges`` maps a variable name to the ``(name, offset)`` pairs it read.
    Built by :meth:`engine.core.model.Model.graph`; constructing one by hand
    is only useful in tests.
    """

    def __init__(self, edges: Mapping[str, Iterable[tuple[str, int]]],
                 pooled: Iterable[str] = ()):
        self.edges = {name: set(deps) for name, deps in edges.items()}
        for deps in list(self.edges.values()):
            for dep, _ in deps:
                self.edges.setdefault(dep, set())
        self.pooled = frozenset(pooled)

    def __repr__(self) -> str:
        return (f"DependencyGraph({len(self.variables)} variables, "
                f"{sum(len(d) for d in self.edges.values())} edges)")

    def __eq__(self, other) -> bool:
        return (isinstance(other, DependencyGraph)
                and self.edges == other.edges and self.pooled == other.pooled)

    @property
    def variables(self) -> tuple:
        return tuple(sorted(self.edges))

    # --- queries ----------------------------------------------------------

    def reads(self, name: str, *, offset: int | None = None) -> tuple:
        """What ``name`` reads directly, optionally at one time offset."""
        deps = self._deps(name)
        if offset is None:
            return tuple(sorted(deps))
        return tuple(sorted(dep for dep, off in deps if off == offset))

    def read_by(self, name: str, *, offset: int | None = None) -> tuple:
        """What reads ``name`` directly."""
        self._deps(name)  # validates the name
        return tuple(sorted(
            caller for caller, deps in self.edges.items()
            for dep, off in deps
            if dep == name and (offset is None or off == offset)
        ))

    def inputs_of(self, name: str) -> tuple:
        """Everything ``name`` depends on, transitively, at any offset.

        The lineage answer: what could possibly have moved this number.
        """
        return tuple(sorted(self._reach(name, forward=True)))

    def affected_by(self, name: str) -> tuple:
        """Everything that would move if ``name`` changed, transitively.

        The other lineage answer, and the one a reviewer asks: I am about to
        change this formula — what else am I changing?
        """
        return tuple(sorted(self._reach(name, forward=False)))

    def leaves(self) -> tuple:
        """Variables that read no other variable — where a model starts."""
        return tuple(sorted(n for n, deps in self.edges.items() if not deps))

    def roots(self) -> tuple:
        """Variables nothing else reads — a model's outputs."""
        read = {dep for deps in self.edges.values() for dep, _ in deps}
        return tuple(sorted(n for n in self.edges if n not in read))

    # --- ordering ---------------------------------------------------------

    def order(self) -> tuple:
        """A topological order over the **same-period** edges.

        Everything a variable needs from its own period comes before it. A
        compiler emitting a forward loop over ``t`` needs exactly this;
        cross-period edges impose no constraint within a period, because
        those values are already computed.

        Ties are broken alphabetically, so the order is deterministic — a
        compilation step that reordered itself between runs would defeat the
        reproducibility guarantee in RFC-003 before it started.
        """
        pending = {
            name: {dep for dep, off in deps if off == 0 and dep != name}
            for name, deps in self.edges.items()
        }
        ordered: list[str] = []
        while pending:
            ready = sorted(n for n, deps in pending.items() if not deps)
            if not ready:
                raise CyclicModelError(
                    "same-period dependency cycle among "
                    f"{sorted(pending)}; a variable cannot read itself, "
                    "directly or indirectly, at the same t"
                )
            for name in ready:
                ordered.append(name)
                del pending[name]
            done = set(ordered)
            for deps in pending.values():
                deps -= done
        return tuple(ordered)

    def horizon(self) -> int:
        """How many periods back the model ever reaches.

        The size of the window a compiled forward loop has to keep alive:
        1 for a model that only looks at the previous period, and every
        template here is 1.
        """
        offsets = [off for deps in self.edges.values() for _, off in deps]
        return max((-off for off in offsets if off < 0), default=0)

    # --- rendering --------------------------------------------------------

    def to_mermaid(self) -> str:
        """A Mermaid flowchart of the model.

        Cross-period edges are dashed and labelled with their offset, so the
        recursion a projection is built on is visible rather than implied.
        """
        lines = ["graph TD"]
        for name in self.variables:
            shape = f'{name}(["{name}"])' if name in self.pooled else f"{name}[{name}]"
            lines.append(f"    {shape}")
        for name in self.variables:
            for dep, offset in sorted(self.edges[name]):
                if offset == 0:
                    lines.append(f"    {dep} --> {name}")
                else:
                    lines.append(f"    {dep} -. t{offset:+d} .-> {name}")
        return "\n".join(lines)

    def describe(self) -> str:
        """One line per variable in evaluation order."""
        out = []
        for name in self.order():
            deps = ", ".join(
                dep if off == 0 else f"{dep}[t{off:+d}]"
                for dep, off in sorted(self.edges[name])
            )
            marker = " (pooled)" if name in self.pooled else ""
            out.append(f"{name}{marker} <- {deps}" if deps else f"{name}{marker}")
        return "\n".join(out)

    # --- internals --------------------------------------------------------

    def _deps(self, name: str) -> set:
        try:
            return self.edges[name]
        except KeyError:
            raise KeyError(
                f"no variable {name!r} in this graph; it has "
                f"{list(self.variables)}"
            ) from None

    def _reach(self, name: str, *, forward: bool) -> set:
        self._deps(name)
        seen: set = set()
        stack = [name]
        while stack:
            current = stack.pop()
            if forward:
                nxt = {dep for dep, _ in self.edges[current]}
            else:
                nxt = {
                    caller for caller, deps in self.edges.items()
                    if any(dep == current for dep, _ in deps)
                }
            for other in nxt:
                if other not in seen and other != name:
                    seen.add(other)
                    stack.append(other)
        return seen
