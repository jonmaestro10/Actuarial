r"""B1, first brick: what a compiled kernel would contain, worked out per model.

PLAN §4.2 asks for the ``@var`` graph to be traced, topologically sorted and
fused into a compiled forward loop. :mod:`engine.core.graph` does the tracing
and the sorting, and says so plainly — "it does **not** fuse anything, and
calling it kernel fusion would be a claim the code cannot support".

This module is the step between. It answers, for a given model, the question
a kernel emitter has to answer first:

    Which of this model's arithmetic can go *inside* a kernel, which values
    must be computed by NumPy and handed *in*, and — if the answer is
    "none of it" — which operation is responsible?

It emits a :class:`CompilationPlan` and **not a kernel**. That boundary is
deliberate and is where B1's previous attempt would have gone wrong: a plan
is checkable against the model it describes, and a kernel is only checkable
against its own output. Getting the plan right first means the kernel has
something to be wrong *against*.

The rule the plan is built on
-----------------------------
RFC-072 measured it: a kernel may contain **only** operations IEEE-754 §5
requires to be correctly rounded, because those are the only ones two
implementations cannot disagree about. Everything else — the transcendental
library, every reduction, and every table gather — is evaluated by NumPy and
passed in as a precomputed slab.

So each operation a model performs is classified by
:func:`engine.core.bitwise.classify` into exactly one of:

``exact``
    goes in the kernel;
``hoist``
    NumPy computes it once per period, and the kernel reads the result;
``reduce``
    a reduction, which is never compiled and which puts every ``@pool``
    body outside a kernel by arithmetic rather than by policy;
``unknown``
    nobody has classified it — a **refusal**, because routing it to the safe
    side would silence the question and quietly stop the kernel fusing.

Traced by running, for the same reason the graph is
----------------------------------------------------
The operations are recorded while the model evaluates, not parsed out of its
source, and :mod:`engine.core.graph`'s docstring already argues why: a
static scan of ``TermLife.pols_if`` sees a helper method, and running it sees
``q_x`` and ``lapse_rate`` being read through two layers of assumption
object.

The tracer therefore carries **real values** alongside the record, so the
model computes exactly what it normally would and every branch it takes is a
branch it would have taken. What it adds is a note of each ufunc.

Where a trace ends
------------------
A ``@var`` body that reaches a table lookup calls ``np.asarray`` on its way
in, and the array that comes back is an ordinary one — the tape stops there.
That is **not** a failure. It is precisely the hoist boundary: the gather is
a per-period value the kernel should be handed, and the tracer records it as
such rather than treating the model as uncompilable.

What is refused, and it is a refusal rather than a fallback: a data-dependent
branch. A ``@var`` body must not branch on model-point data, and if one does,
the recorded tape is specialised to *this batch's values* and would compile a
kernel that is right for the trace and wrong for the next block. RFC-070's
bug was exactly a conditional branch that a particular batch never entered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Type

import numpy as np

from engine.core.bitwise import classify
from engine.core.model import Model
from engine.data.modelpoints import ModelPointBatch, to_batch

#: Periods to trace. Three is the fewest that exercises a variable's
#: ``t == 0`` branch, its first recursive step, and a steady-state step —
#: the same reasoning, and the same number, as ``vector.TRACE_PERIODS``.
TRACE_PERIODS = 3


class CompilationRefused(ValueError):
    """A model whose arithmetic a kernel could not reproduce bit for bit."""


@dataclass(frozen=True)
class Hoist:
    """A value NumPy must compute, because a kernel cannot reproduce it.

    ``reason`` distinguishes the two kinds, and the distinction matters to
    whoever writes the emitter: an ``operation`` hoist is one ufunc whose
    result is a per-period array, while a ``gather`` is a table lookup that
    left the traced world entirely and comes back as ordinary data.
    """

    variable: str
    op: str
    reason: str


@dataclass(frozen=True)
class CompilationPlan:
    """What a kernel for this model would contain, and what it would be handed.

    ``compilable`` is the verdict. It is ``False`` when any operation is
    unclassified or is a reduction, and those are reported as ``refusals``
    with the operation named — a plan that said only "no" would leave the
    caller to guess which of forty variables was responsible.

    Hoists are **not** refusals. A model can be perfectly compilable and
    still need most of its transcendentals precomputed; that is the expected
    shape rather than a degraded one, because those values are almost always
    loop-invariant along the model-point axis anyway.
    """

    model: str
    order: tuple
    exact_ops: dict
    hoists: tuple
    refusals: tuple
    periods_traced: int

    @property
    def compilable(self) -> bool:
        return not self.refusals

    @property
    def exact_op_count(self) -> int:
        return sum(len(ops) for ops in self.exact_ops.values())

    def describe(self) -> str:
        lines = [f"{self.model}: "
                 f"{'compilable' if self.compilable else 'refused'}",
                 f"  {len(self.order)} variables in topological order",
                 f"  {self.exact_op_count} exact operations would be fused",
                 f"  {len(self.hoists)} values hoisted to NumPy"]
        for refusal in self.refusals:
            lines.append(f"  REFUSED  {refusal}")
        return "\n".join(lines)


@dataclass
class _Tape:
    """What the tracer saw, before it is turned into a plan."""

    variable: str = ""
    exact: list = field(default_factory=list)
    hoists: list = field(default_factory=list)
    refusals: list = field(default_factory=list)

    def record(self, op: str) -> None:
        kind = classify(op)
        if kind == "exact":
            self.exact.append(op)
        elif kind == "hoist":
            self.hoists.append(Hoist(self.variable, op, "operation"))
        elif kind == "reduce":
            self.refusals.append(
                f"{self.variable} reduces with {op!r}: a reduction's answer "
                f"depends on association order and there is no length at "
                f"which that is safe, so it is never compiled (RFC-072)"
            )
        else:
            self.refusals.append(
                f"{self.variable} uses {op!r}, which is not classified. "
                f"Decide whether IEEE-754 requires it to be correctly "
                f"rounded and add it to engine/core/bitwise.py"
            )

    def gather(self) -> None:
        self.hoists.append(Hoist(self.variable, "asarray", "gather"))

    def branch(self, what: str) -> None:
        self.refusals.append(
            f"{self.variable} branched on traced data ({what}). The recorded "
            f"tape would be specialised to this batch's values, and a kernel "
            f"built from it would be right for the trace and wrong for the "
            f"next block — which is RFC-070's bug exactly. A @var body must "
            f"not branch on model-point data."
        )


class _Traced(np.lib.mixins.NDArrayOperatorsMixin):
    """An array that carries its real value and notes what is done to it.

    The value is genuine, so the model computes what it normally would and
    takes the branches it normally would. Subclassing ``ndarray`` was the
    alternative and is worse here: it would make the traced values
    indistinguishable from ordinary ones at the boundary, and the boundary is
    the thing being measured.
    """

    __slots__ = ("value", "tape")

    def __init__(self, value, tape: _Tape):
        self.value = np.asarray(value)
        self.tape = tape

    # -- the recording surface ---------------------------------------------

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        if method != "__call__":
            self.tape.record(f"{ufunc.__name__}.{method}")
        else:
            self.tape.record(ufunc.__name__)
        raw = [i.value if isinstance(i, _Traced) else i for i in inputs]
        out = getattr(ufunc, method)(*raw, **kwargs)
        return _Traced(out, self.tape) if isinstance(out, np.ndarray) else out

    def __array_function__(self, func, types, args, kwargs):
        self.tape.record(getattr(func, "__name__", str(func)))
        raw = tuple(a.value if isinstance(a, _Traced) else a for a in args)
        out = func(*raw, **kwargs)
        return _Traced(out, self.tape) if isinstance(out, np.ndarray) else out

    def __array__(self, dtype=None, copy=None):
        # Something asked for a plain array: a table lookup, or NumPy
        # normalising an argument. The tape stops here and the value becomes
        # a hoist rather than a failure.
        self.tape.gather()
        return np.asarray(self.value, dtype=dtype)

    def __bool__(self):
        self.tape.branch("bool()")
        return bool(self.value)

    def __getitem__(self, key):
        self.tape.record("take")
        return _Traced(self.value[key], self.tape)

    # -- enough of the array surface that a @var body cannot tell -----------

    @property
    def shape(self):
        return self.value.shape

    @property
    def dtype(self):
        return self.value.dtype

    @property
    def ndim(self):
        return self.value.ndim

    @property
    def size(self):
        return self.value.size

    def __len__(self):
        return len(self.value)

    def __iter__(self):
        self.tape.branch("iteration")
        return iter(self.value)

    def __repr__(self) -> str:
        return f"_Traced({self.value!r})"


def _traced_batch(batch: ModelPointBatch, tape: _Tape) -> ModelPointBatch:
    """The batch with its numeric fields traced and everything else itself."""
    traced = ModelPointBatch.__new__(ModelPointBatch)
    fields = {}
    for name, values in batch.fields.items():
        array = np.asarray(values)
        fields[name] = (_Traced(array, tape)
                        if array.dtype.kind in "fiu" else values)
    traced.__dict__.update(fields)
    traced.ids, traced.n = batch.ids, batch.n
    return traced


def plan(model_cls: Type[Model], modelpoints, assumptions: Any,
         proj_len: int, outputs: list[str] | None = None,
         periods: int = TRACE_PERIODS) -> CompilationPlan:
    """Work out what a kernel for ``model_cls`` would contain.

    Runs the model over a traced batch for the first few periods, classifies
    every operation it performs, and reports the exact ones a kernel would
    fuse, the values it would be handed, and the reasons it could not be
    built if it could not.

    Does **not** build a kernel. See the module docstring for why that
    boundary is where it is.
    """
    batch = to_batch(modelpoints)
    tape = _Tape()
    model = model_cls(mp=_traced_batch(batch, tape), assumptions=assumptions,
                      proj_len=proj_len, record_graph=True)
    names = list(outputs or model.var_names())

    per_variable: dict[str, list] = {}
    traced_periods = min(periods, proj_len + 1)
    for t in range(traced_periods):
        for name in names:
            tape.variable = name
            before = len(tape.exact)
            try:
                getattr(model, name)(t)
            except Exception as exc:      # noqa: BLE001 - reported, not raised
                tape.refusals.append(
                    f"{name} could not be traced at t={t}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            per_variable.setdefault(name, []).extend(tape.exact[before:])

    try:
        order = model.graph().order()
    except Exception as exc:              # noqa: BLE001
        tape.refusals.append(f"no topological order: {type(exc).__name__}: {exc}")
        order = tuple(names)

    return CompilationPlan(
        model=model_cls.__name__,
        order=tuple(order),
        exact_ops={name: tuple(ops) for name, ops in per_variable.items()},
        hoists=tuple(dict.fromkeys(tape.hoists)),
        refusals=tuple(dict.fromkeys(tape.refusals)),
        periods_traced=traced_periods,
    )
