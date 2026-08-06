r"""The compiled executor: the ``@var`` graph fused into a native forward loop.

PLAN §4.2 asks for the graph to be traced, topologically sorted and fused
into compiled kernels. :mod:`engine.core.graph` does the tracing and the
sorting, and says plainly that it "does **not** fuse anything". This module
is the fusion, and it produces results **bitwise identical** to the
vectorized executor rather than merely close — §1.2's guarantee, not a
weakened version of it.

What makes that possible is RFC-072's measurement: a kernel may contain only
operations IEEE-754 §5 requires to be correctly rounded, plus the structural
ones that perform no arithmetic at all. Everything else is *hoisted* —
computed by NumPy and handed in as a slab.

Three decisions carry the design
--------------------------------
**A variable is hoisted whole, never in part.** A ``@var`` body that reaches
a mortality table, or evaluates ``exp``, cannot be fused. The tempting move
is to hoist that sub-expression and fuse the rest, and it cannot be done: a
sub-expression is an anonymous intermediate with no name that survives to run
time, so nothing could compute it for the *next* block. A ``@var`` has a name
the vectorized executor can evaluate for any batch, which is exactly what a
hoist slab has to be. So tracing runs twice — once to find the variables that
cannot be fully traced, once more with those hoisted — and the kernel fuses
what is left.

**Every cross-variable reference is ``ref(variable, offset)``.** Reading
``pols_if(t - 1)`` and reading ``q_x(t)`` are the same kind of edge at
different offsets, and whether the answer comes from a kernel slab or a hoist
slab is a property of the *variable*, not of the reference. Unifying them is
what makes the recorded tape stable across periods; keeping them apart is
what made an earlier version drift, because a hoisted value read at ``t - 1``
interned identically to one read at ``t``.

**A scalar is keyed by position, not by value.** ``years_elapsed(t)`` is a
different number every period. Interning it by value gives a different tape
each period and nothing ever stabilises; interning it by *where it appears*
gives a stable tape and a per-period input vector. A scalar whose value is
the same at every period is folded into the source as a literal, and one that
varies becomes an argument.

The tape must stabilise, and that is checked
--------------------------------------------
Period 0 gets its own expression, because every stock variable branches on
``t == 0``. From period 1 the tape must be structurally identical at every
period, and :func:`plan` **refuses** a model where it is not. A tape that
kept changing would mean the kernel had been specialised to the periods that
happened to be traced.

For the same reason a ``@var`` body that branches on model-point data is
refused rather than specialised: the tape would be right for the traced batch
and wrong for the next block, which is RFC-070's bug with a compiler behind
it.

The generated source is kept
----------------------------
:attr:`CompilationPlan.source` is ordinary Python, and it is readable::

    o_pols_if[t, j] = (o_pols_if[t - 1, j] * (1.0 - h_q_x[t - 1, j]))

An auditor can read the loop the engine actually ran. That is worth more here
than in most compilers, because the thing being compiled is a regulatory
calculation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Type

import numpy as np

from engine.core.bitwise import UNSAFE_FLAGS, classify
from engine.core.model import Model
from engine.core.results import ArrayRunResult
from engine.core.vector import run_vectorized
from engine.data.modelpoints import to_batch

#: Periods whose tape must agree before the steady state is believed. Period
#: 0 is separate by construction; 1, 2 and 3 are the fewest that distinguish
#: "the same every period" from "the same as the period before".
STEADY_PERIODS = (1, 2, 3)

#: How many times tracing may discover a new variable that has to be hoisted
#: before the search is abandoned. Each pass can only *add* to the hoisted
#: set, so it converges; the cap is a backstop against a pathological model,
#: not a tuning knob.
MAX_HOIST_PASSES = 8

#: Scalar operator templates. Only operations :mod:`engine.core.bitwise`
#: classifies as ``exact`` or ``structural`` appear here — anything else is
#: hoisted before it reaches codegen, so a missing entry is a refusal rather
#: than a silent approximation.
_BINARY = {
    "add": "({} + {})", "subtract": "({} - {})", "multiply": "({} * {})",
    "divide": "({} / {})", "true_divide": "({} / {})",
    "maximum": "max({}, {})", "minimum": "min({}, {})",
    "fmax": "max({}, {})", "fmin": "min({}, {})",
    "less": "({} < {})", "less_equal": "({} <= {})",
    "greater": "({} > {})", "greater_equal": "({} >= {})",
    "equal": "({} == {})", "not_equal": "({} != {})",
    "logical_and": "({} and {})", "logical_or": "({} or {})",
    "remainder": "({} % {})", "fmod": "math.fmod({}, {})",
    "copysign": "math.copysign({}, {})",
}
_UNARY = {
    "negative": "(-{})", "positive": "(+{})", "absolute": "abs({})",
    "sqrt": "math.sqrt({})", "floor": "math.floor({})",
    "ceil": "math.ceil({})", "trunc": "math.trunc({})",
    "logical_not": "(not {})",
}


class CompilationRefused(ValueError):
    """A model whose arithmetic a kernel could not reproduce bit for bit."""


@dataclass(frozen=True)
class Node:
    """One vertex of the recorded expression DAG."""

    kind: str            # field | scalar | ref | op | opaque
    op: str = ""
    args: tuple = ()
    label: str = ""


@dataclass
class _DAG:
    nodes: list = field(default_factory=list)
    index: dict = field(default_factory=dict)
    scalars: dict = field(default_factory=dict)
    hoisted: set = field(default_factory=set)
    must_hoist: set = field(default_factory=set)
    branches: list = field(default_factory=list)
    variable: str = ""
    period: int = 0
    counter: int = 0

    def intern(self, node: Node) -> int:
        found = self.index.get(node)
        if found is None:
            found = len(self.nodes)
            self.nodes.append(node)
            self.index[node] = found
        return found

    def field_of(self, name: str) -> int:
        return self.intern(Node("field", label=name))

    def ref(self, name: str, offset: int) -> int:
        return self.intern(Node("ref", label=name, args=(offset,)))

    def scalar(self, value) -> int:
        key = (self.variable, self.counter)
        self.counter += 1
        self.scalars.setdefault(key, {})[self.period] = float(value)
        return self.intern(Node("scalar", label=f"{key[0]}#{key[1]}"))

    def opaque(self, why: str) -> int:
        self.must_hoist.add(self.variable)
        return self.intern(Node("opaque", label=f"{self.variable}:{why}"))


class _Traced(np.lib.mixins.NDArrayOperatorsMixin):
    """An array that carries its real value and records what is done to it.

    The value is genuine, so the model computes what it normally would and
    takes the branches it normally would. What it adds is the DAG.
    """

    __slots__ = ("value", "dag", "node", "origin")

    def __init__(self, value, dag: _DAG, node: int, origin=None):
        self.value = np.asarray(value)
        self.dag = dag
        self.node = node
        self.origin = origin

    def _operand(self, other) -> int:
        dag = self.dag
        if isinstance(other, _Traced):
            if other.origin is None:
                return other.node                     # a model-point field
            name, period = other.origin
            if name == dag.variable and period == dag.period:
                return other.node                     # own temporary: inline
            return dag.ref(name, dag.period - period)
        if isinstance(other, np.ndarray) and other.ndim > 0:
            return dag.opaque("untracked-array")
        return dag.scalar(other)

    def _wrap(self, out, node):
        if isinstance(out, np.ndarray):
            return _Traced(out, self.dag, node,
                           (self.dag.variable, self.dag.period))
        return out

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        name = (ufunc.__name__ if method == "__call__"
                else f"{ufunc.__name__}.{method}")
        raw = [i.value if isinstance(i, _Traced) else i for i in inputs]
        out = getattr(ufunc, method)(*raw, **kwargs)
        if classify(name) in ("exact", "structural"):
            node = self.dag.intern(
                Node("op", name, tuple(self._operand(i) for i in inputs)))
        else:
            node = self.dag.opaque(name)
        return self._wrap(out, node)

    def __array_function__(self, func, types, args, kwargs):
        name = getattr(func, "__name__", str(func))
        raw = tuple(a.value if isinstance(a, _Traced) else a for a in args)
        out = func(*raw, **kwargs)
        if classify(name) in ("exact", "structural"):
            node = self.dag.intern(Node("op", name, tuple(
                self._operand(a) for a in args
                if isinstance(a, (_Traced, int, float, np.floating)))))
        else:
            node = self.dag.opaque(name)
        return self._wrap(out, node)

    def __array__(self, dtype=None, copy=None):
        return np.asarray(self.value, dtype=dtype)

    def __bool__(self):
        self.dag.branches.append(self.dag.variable)
        return bool(self.value)

    def __iter__(self):
        self.dag.branches.append(self.dag.variable)
        return iter(self.value)

    @property
    def shape(self):
        return self.value.shape

    @property
    def dtype(self):
        return self.value.dtype

    @property
    def ndim(self):
        return self.value.ndim

    def __len__(self):
        return len(self.value)

    def __repr__(self) -> str:
        return f"_Traced({self.value!r})"


@dataclass(frozen=True)
class CompilationPlan:
    """What the kernel contains, what it is handed, and whether it exists."""

    model: str
    order: tuple
    fused: tuple
    hoisted: tuple
    fields: tuple
    scalars: tuple            # ((variable, index), values-by-period)
    constants: dict
    source: str
    refusals: tuple
    proj_len: int

    @property
    def compilable(self) -> bool:
        return not self.refusals and bool(self.fused)

    @property
    def digest(self) -> str:
        """Identity of the generated loop, for the kernel cache.

        Over the **source**, not over the model class: two models that
        compile to the same loop are the same kernel, and one model whose
        formulas changed is a different one.
        """
        return hashlib.sha256(self.source.encode()).hexdigest()[:16]

    def describe(self) -> str:
        lines = [f"{self.model}: "
                 f"{'compilable' if self.compilable else 'not compiled'}",
                 f"  {len(self.fused)} of {len(self.order)} variables fused",
                 f"  {len(self.hoisted)} hoisted to NumPy: "
                 f"{', '.join(self.hoisted) or 'none'}",
                 f"  {len(self.scalars)} per-period scalars, "
                 f"{len(self.constants)} folded constants"]
        lines += [f"  REFUSED  {r}" for r in self.refusals]
        return "\n".join(lines)


def _trace_once(model_cls, batch, assumptions, proj_len, periods, forced):
    dag = _DAG()
    traced = type(batch).__new__(type(batch))
    fields = {}
    for name, values in batch.fields.items():
        array = np.asarray(values)
        fields[name] = (_Traced(array, dag, dag.field_of(name))
                        if array.dtype.kind in "fiu" else values)
    traced.__dict__.update(fields)
    traced.ids, traced.n = batch.ids, batch.n

    probe = model_cls(mp=batch, assumptions=assumptions, proj_len=proj_len,
                      record_graph=True)
    for t in range(min(3, proj_len + 1)):
        for name in probe.var_names():
            getattr(probe, name)(t)
    order = list(probe.graph().order())
    order += [n for n in probe.var_names() if n not in order]

    model = model_cls(mp=traced, assumptions=assumptions, proj_len=proj_len)
    roots: dict = {}
    for t in periods:
        dag.period, dag.counter = t, 0
        roots[t] = {}
        for name in order:
            dag.variable, dag.counter = name, 0
            result = getattr(model, name)(t)
            if name in forced or not isinstance(result, _Traced):
                dag.hoisted.add(name)
                roots[t][name] = None
                # Re-injected as a traced leaf so whatever reads it next
                # records a `ref` rather than an untracked array.
                model._cache[(name, t)] = _Traced(
                    np.asarray(result), dag, dag.ref(name, 0), (name, t))
            else:
                roots[t][name] = result.node
    return dag, roots, tuple(order)


def _emit(dag, roots, order, fused, constants, scalars) -> str:
    """Generate the kernel source. Readable on purpose — see the module doc."""

    def expression(index: int, t: str) -> str:
        node = dag.nodes[index]
        if node.kind == "field":
            return f"f_{node.label}[j]"
        if node.kind == "scalar":
            name, position = node.label.split("#")
            key = (name, int(position))
            if key in constants:
                return repr(constants[key])
            return f"s_{name}_{position}[{t}]"
        if node.kind == "ref":
            offset = node.args[0]
            slot = t if offset == 0 else f"{t} - {offset}"
            prefix = "h_" if node.label in dag.hoisted else "o_"
            return f"{prefix}{node.label}[{slot}, j]"
        if node.kind == "op":
            parts = [expression(a, t) for a in node.args]
            if node.op in _BINARY and len(parts) == 2:
                return _BINARY[node.op].format(*parts)
            if node.op in _UNARY and len(parts) == 1:
                return _UNARY[node.op].format(*parts)
            if node.op in ("where", "select") and len(parts) == 3:
                return f"({parts[1]} if {parts[0]} else {parts[2]})"
            if node.op in ("rint",) and len(parts) == 1:
                return f"np.rint({parts[0]})"
            raise CompilationRefused(
                f"no scalar form for {node.op!r} with {len(parts)} operands"
            )
        raise CompilationRefused(f"cannot emit a {node.kind!r} node")

    args = ([f"f_{n}" for n in sorted(
                {nd.label for nd in dag.nodes if nd.kind == "field"})]
            + [f"h_{n}" for n in sorted(dag.hoisted)]
            + [f"s_{v}_{k}" for (v, k) in scalars]
            + [f"o_{n}" for n in fused])
    lines = ["import math", "import numpy as np",
             f"def kernel(n_mp, n_t, {', '.join(args)}):",
             "    for j in range(n_mp):"]
    for name in fused:
        lines.append(f"        o_{name}[0, j] = "
                     f"{expression(roots[0][name], '0')}")
    lines.append("    for t in range(1, n_t + 1):")
    lines.append("        for j in range(n_mp):")
    for name in fused:
        lines.append(f"            o_{name}[t, j] = "
                     f"{expression(roots[1][name], 't')}")
    lines.append("    return 0")
    return "\n".join(lines)


def plan(model_cls: Type[Model], modelpoints, assumptions: Any,
         proj_len: int) -> CompilationPlan:
    """Trace ``model_cls`` and generate its kernel source.

    Traced over a **single** model point: the tape's structure and the
    per-period scalars are properties of the formulas and the calendar, not
    of the block, so compilation costs O(periods) rather than O(policies ×
    periods). The hoist slabs are the part that needs the real batch, and
    those are computed at run time.
    """
    batch = to_batch(modelpoints)
    one = batch.take(0, 1)
    periods = tuple(range(proj_len + 1))

    forced: set = set()
    dag = roots = order = None
    for _ in range(MAX_HOIST_PASSES):
        dag, roots, order = _trace_once(model_cls, one, assumptions,
                                        proj_len, periods, forced)
        discovered = dag.must_hoist - forced
        if not discovered:
            break
        forced |= discovered
    else:
        return CompilationPlan(
            model_cls.__name__, tuple(order or ()), (), (), (), (), {}, "",
            (f"the set of variables needing to be hoisted did not settle "
             f"after {MAX_HOIST_PASSES} passes",), proj_len)

    refusals = []
    if dag.branches:
        refusals.append(
            f"{sorted(set(dag.branches))} branched on traced data. The tape "
            f"would be specialised to this batch's values and a kernel built "
            f"from it would be right for the trace and wrong for the next "
            f"block — RFC-070's bug with a compiler behind it. A @var body "
            f"must not branch on model-point data."
        )

    steady = [t for t in STEADY_PERIODS if t <= proj_len]
    for name in order:
        seen = {roots[t][name] for t in steady}
        if len(seen) > 1:
            refusals.append(
                f"{name}'s tape is not the same at periods {steady}: the "
                f"kernel would be specialised to the periods that happened "
                f"to be traced"
            )

    fused = tuple(n for n in order if n not in dag.hoisted)
    if not fused:
        refusals.append(
            "every variable is hoisted, so there is nothing to fuse and a "
            "kernel would be the vectorized executor with extra steps"
        )

    # A scalar recorded at every traced period with one value is a literal.
    # One that is *absent* in some period belongs to a branch not taken there
    # — `pols_if`'s t == 0 form has different operands from its recursion —
    # so it stays a per-period vector and the periods that never read it are
    # filled with zero at run time.
    every_period = set(range(proj_len + 1))
    constants = {k: next(iter(set(v.values())))
                 for k, v in dag.scalars.items()
                 if len(set(v.values())) == 1 and set(v) == every_period}
    scalars = tuple(sorted(k for k in dag.scalars if k not in constants))

    source = ""
    if not refusals:
        try:
            source = _emit(dag, roots, order, fused, constants, scalars)
        except CompilationRefused as exc:
            refusals.append(str(exc))

    return CompilationPlan(
        model=model_cls.__name__,
        order=tuple(order),
        fused=fused,
        hoisted=tuple(sorted(dag.hoisted)),
        fields=tuple(sorted({nd.label for nd in dag.nodes
                             if nd.kind == "field"})),
        scalars=tuple((k, dict(dag.scalars[k])) for k in scalars),
        constants=constants,
        source=source,
        refusals=tuple(refusals),
        proj_len=proj_len,
    )


_KERNELS: dict = {}
_PLANS: dict = {}


def cached_plan(model_cls, modelpoints, assumptions, proj_len):
    """:func:`plan`, memoised per (model class, time structure, basis).

    PLAN §4.2 asks for kernels cached "per (model class, time structure)".
    The basis is in the key too, and has to be: the per-period scalars are
    values off the assumption set — a discount factor, a crediting
    accumulation — so two runs of the same model over the same horizon on
    different bases need different scalar vectors and therefore different
    folded constants.

    Keyed on the basis's *identity* rather than its contents. An assumption
    set is not hashable in general and computing a structural digest of one
    would be a second fingerprinting scheme to keep true; identity is
    conservative — a rebuilt-but-equal basis simply misses the cache and
    re-traces, which costs time and cannot cost correctness.
    """
    key = (model_cls, proj_len, id(assumptions))
    found = _PLANS.get(key)
    if found is None:
        found = plan(model_cls, modelpoints, assumptions, proj_len)
        _PLANS[key] = found
    return found


def compile_kernel(compilation: CompilationPlan):
    """The generated source, JIT-compiled and cached by its digest.

    ``fastmath`` is **off** and must stay off: it licenses reassociation and
    contraction, which is exactly the permission to turn ``a * b + c`` into a
    fused multiply-add — more accurate, and a different number. See
    :data:`engine.core.bitwise.UNSAFE_FLAGS`.
    """
    if not compilation.compilable:
        raise CompilationRefused(
            f"{compilation.model} was not compiled:\n  "
            + "\n  ".join(compilation.refusals))
    cached = _KERNELS.get(compilation.digest)
    if cached is not None:
        return cached
    try:
        from numba import njit
    except ImportError as exc:  # pragma: no cover - needs the extra absent
        raise CompilationRefused(
            "the compiled executor needs the [compile] extra "
            "(pip install -e '.[compile]')"
        ) from exc
    assert "fastmath" not in UNSAFE_FLAGS[:0]     # documented, never enabled
    namespace: dict = {}
    exec(compile(compilation.source, f"<{compilation.model} kernel>", "exec"),
         namespace)
    kernel = njit(cache=False)(namespace["kernel"])
    _KERNELS[compilation.digest] = kernel
    return kernel


def run_compiled(model_cls: Type[Model], modelpoints, assumptions: Any,
                 proj_len: int, outputs: list[str] | None = None
                 ) -> ArrayRunResult:
    """Project ``model_cls`` with the fused kernel, bitwise as the array path.

    The hoisted variables are evaluated by :func:`~engine.core.vector.run_vectorized`
    — the same code, on the same batch, producing the same bits — and handed
    to the kernel as slabs. What the kernel adds is the fusion of everything
    else: one pass over memory per period instead of one per operation.
    """
    batch = to_batch(modelpoints)
    # No per-policy check: this is an *array* executor, like the vectorized
    # one. A `@pool` body reduces with `pool_sum`, which classifies as a
    # reduction and is therefore hoisted whole — so the reduction is
    # performed by the vectorized executor over the real block, exactly as it
    # would be without a kernel. Pooling costs fusion, not correctness.
    compilation = cached_plan(model_cls, batch, assumptions, proj_len)
    kernel = compile_kernel(compilation)

    hoist_slabs = {}
    if compilation.hoisted:
        hoisted = run_vectorized(model_cls, batch, assumptions, proj_len,
                                 outputs=list(compilation.hoisted))
        hoist_slabs = {name: np.ascontiguousarray(hoisted.array(name))
                       for name in compilation.hoisted}

    slabs = {name: np.empty((proj_len + 1, batch.n), dtype=np.float64)
             for name in compilation.fused}
    arguments = (
        [np.ascontiguousarray(getattr(batch, f), dtype=np.float64)
         for f in compilation.fields]
        + [hoist_slabs[n] for n in compilation.hoisted]
        + [np.array([values.get(t, 0.0) for t in range(proj_len + 1)],
                    dtype=np.float64)
           for _, values in compilation.scalars]
        + [slabs[n] for n in compilation.fused]
    )
    kernel(batch.n, proj_len, *arguments)

    stacked = dict(slabs)
    stacked.update(hoist_slabs)
    if outputs is not None:
        missing = [n for n in outputs if n not in stacked]
        if missing:
            raise CompilationRefused(
                f"{missing} were not produced by the compiled run; it emits "
                f"{sorted(stacked)}"
            )
        stacked = {n: stacked[n] for n in outputs}
    return ArrayRunResult(stacked=stacked, mp_ids=batch.ids)
