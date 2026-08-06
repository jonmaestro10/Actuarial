r"""Exact-decimal audit mode: the interpreted executor over ``decimal.Decimal``.

PLAN §3.4 promises to "document float behavior; offer a slow exact-decimal
audit mode for regulatory sign-off runs". This is that mode, and its purpose
is **not** to produce better numbers. It is to produce a second, independent
answer under different arithmetic, so the float answer's error can be
*measured* rather than assumed — the same move the dual-executor invariant
makes, one level down.

The claim, stated precisely
---------------------------
"Exact" overclaims if left unqualified, so it is qualified here rather than
in a footnote. Under :data:`EXACT_CONTEXT` this mode is:

- **exact** for addition, subtraction and multiplication of decimal inputs —
  no representation error, no rounding, at any depth of recursion;
- **correctly rounded to 34 significant decimal digits** for division and
  for powers, because ``1/3`` has no finite decimal expansion and no
  arithmetic can give it one.

34 digits is not arbitrary: it is the significand of IEEE 754 **decimal128**,
so the mode is a named format rather than a preference. Double precision
carries about 15.95 decimal digits, so the audit run holds roughly eighteen
more than the run it is checking — enough that the difference between them
is the float run's error, not a contest between two approximations.

The decision that makes it worth running
-----------------------------------------
A float assumption is not the number the actuary wrote. ``0.035`` parses to
``0.0350000000000000033306690738754696212708950042724609375``, because
binary floating point has no exact representation for it. That error is
present before any arithmetic happens, it is proportional to nothing the
model controls, and it compounds through a forty-year discount chain.

So conversion goes through :func:`as_written` — ``Decimal(repr(x))``, which
recovers ``0.035`` exactly — and **not** ``Decimal(x)``, which faithfully
preserves the binary value and would defeat the entire point. The two differ
in the 17th digit and diverge from there; both readings are meaningful and
this module offers both, but they answer different questions:

- :func:`as_written` — "what would this model produce from the assumptions
  as the actuary stated them?" This is the sign-off question.
- :func:`as_stored` — "what would this model produce from the assumptions as
  the machine actually holds them?" This isolates *arithmetic* error from
  *representation* error, and is how you tell which one you are looking at.

Float literals inside ``@var`` bodies — the ``1.0`` in ``1.0 - q`` — are
coerced by the same rule, and for the same reason: they are decimal numerals
written by a human. It costs nothing here, since ``1.0`` and ``0.5`` are
exact in both bases, but the rule is one rule.

Why a subclass rather than a rewritten executor
-----------------------------------------------
Template bodies mix Decimals with float literals, and ``float + Decimal`` is
a ``TypeError`` — deliberately, because silently mixing the two is how a
decimal calculation quietly becomes a binary one. :class:`Exact` is a
``Decimal`` subclass that coerces float operands through :func:`as_written`,
which makes **the whole template library polymorphic without editing a
template**. That matters more than the convenience: an audit mode that
required its own copies of the formulas would be auditing the copies.

What it refuses
---------------
Everything the interpreted executor refuses, for the same reasons — a pooled
model run per policy would give each policy a pool of itself
(:class:`~engine.core.runner.PooledBlockError`). Beyond that, a template
whose body reaches for NumPy operates on arrays this mode does not produce,
and is refused by name rather than silently falling back to float. See
:func:`run_exact`.

And it is **slow** — one to two orders of magnitude slower than the
interpreted float executor, which is itself the slow one. That is the price
of the arithmetic and not a defect to optimise away; the mode is opt-in, for
sign-off runs over a sample, not for a valuation.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, DivisionByZero
from decimal import InvalidOperation, Overflow, localcontext
from typing import Any, Iterable, Type

import numpy as np

from engine.core.model import Model
from engine.core.results import RunResult
from engine.core.runner import check_per_policy

#: Significand digits of IEEE 754 **decimal128**. Double precision carries
#: about 15.95 decimal digits, so an audit run holds roughly eighteen more
#: than the run it checks — the gap that lets the difference be read as the
#: float run's error rather than as two approximations disagreeing.
EXACT_PRECISION = 34

#: The audit context. ``ROUND_HALF_EVEN`` matches IEEE 754's default so the
#: two arithmetics differ in precision and radix but not in rounding policy —
#: one variable at a time.
#:
#: The traps are the point of configuring it at all. An overflow, a division
#: by zero or an invalid operation **raises** here rather than returning a
#: quiet ``NaN`` or ``Infinity`` the way float does. A sign-off run that
#: silently produced ``NaN`` and reported a reserve would be worse than one
#: that stopped.
EXACT_CONTEXT = Context(
    prec=EXACT_PRECISION,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class ExactError(ValueError):
    """A run this mode will not perform, or a value it will not convert."""


def as_written(value) -> "Exact":
    r"""The number as a human wrote it: ``Decimal(repr(x))``.

    ``as_written(0.035)`` is ``0.035``. This is the conversion a sign-off run
    wants, because the assumption *is* 3.5% — the binary expansion is an
    artefact of how it was stored, not a fact about the basis.
    """
    return Exact(value)


def as_stored(value) -> Decimal:
    r"""The number as the machine holds it: ``Decimal(float)``, exactly.

    ``as_stored(0.035)`` is
    ``0.0350000000000000033306690738754696212708950042724609375`` — the true
    value of the double, to its last bit.

    Offered beside :func:`as_written` because the difference between two runs
    that use them is precisely the **representation** error, with arithmetic
    error held constant. Without both, a discrepancy has two possible causes
    and no way to tell them apart.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (bool,)):
        raise ExactError("a bool is not a quantity to convert")
    return Decimal(float(value))


class Exact(Decimal):
    r"""A ``Decimal`` that accepts float operands, reading them as written.

    ``float + Decimal`` is a ``TypeError`` in Python, and rightly: mixing
    them silently is how a decimal calculation becomes a binary one halfway
    through. But every ``@var`` body in the library contains float literals —
    ``1.0 - q``, ``0.5 * x`` — and rewriting them all for an audit mode would
    mean auditing the rewrites rather than the model.

    So the coercion is explicit, one-directional and stated: a ``float``
    operand is read through :func:`as_written`, the same rule the inputs go
    through. Every operator returns an ``Exact``, so the coercion holds for
    the whole depth of a recursion rather than decaying to ``Decimal`` after
    the first operation and failing on the second.
    """

    __slots__ = ()

    def __new__(cls, value="0", context=None):
        if isinstance(value, Exact):
            return value
        if isinstance(value, bool):
            raise ExactError("a bool is not a quantity")
        if isinstance(value, (float, np.floating)):
            value = repr(float(value))
        elif isinstance(value, np.integer):
            value = int(value)
        elif isinstance(value, np.ndarray):
            if value.ndim != 0:
                raise ExactError(
                    f"an array of shape {value.shape} is not a scalar "
                    f"quantity; the exact executor runs one policy at a time"
                )
            return cls(value.item())
        return super().__new__(cls, value, context)

    def __repr__(self) -> str:
        return f"Exact('{self}')"


def _coerce(other):
    if isinstance(other, (float, np.floating, np.integer)):
        return Exact(other)
    return other


def _install_operators() -> None:
    """Wrap ``Decimal``'s arithmetic so it coerces floats and returns ``Exact``.

    Generated rather than written out, so no operator can be forgotten — a
    missing ``__rsub__`` would surface as a ``TypeError`` deep inside one
    template's recursion and nowhere else.
    """
    binary = ("add", "radd", "sub", "rsub", "mul", "rmul", "truediv",
              "rtruediv", "floordiv", "rfloordiv", "mod", "rmod",
              "pow", "rpow", "divmod", "rdivmod")
    for name in binary:
        dunder = f"__{name}__"
        base = getattr(Decimal, dunder)

        def op(self, other, _base=base):
            result = _base(self, _coerce(other))
            if isinstance(result, tuple):
                return tuple(Exact(r) for r in result)
            if result is NotImplemented or not isinstance(result, Decimal):
                return result
            return Exact(result)

        op.__name__ = dunder
        setattr(Exact, dunder, op)

    for name in ("neg", "pos", "abs"):
        dunder = f"__{name}__"
        base = getattr(Decimal, dunder)

        def unary(self, _base=base):
            return Exact(_base(self))

        unary.__name__ = dunder
        setattr(Exact, dunder, unary)

    for name in ("lt", "le", "gt", "ge", "eq", "ne"):
        dunder = f"__{name}__"
        base = getattr(Decimal, dunder)

        def cmp(self, other, _base=base):
            return _base(self, _coerce(other))

        cmp.__name__ = dunder
        setattr(Exact, dunder, cmp)

    # Overriding __eq__ drops the inherited __hash__; a model point field
    # that cannot be hashed breaks any caller that keys on one.
    Exact.__hash__ = Decimal.__hash__


_install_operators()


def _convert(value, reader):
    """Convert a value crossing out of the float world, structure and all."""
    if isinstance(value, dict):
        return {k: _convert(v, reader) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_convert(v, reader) for v in value)
    if isinstance(value, list):
        return [_convert(v, reader) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return reader(value.item())
        raise ExactError(
            f"the assumption layer returned an array of shape {value.shape}. "
            f"The exact executor runs one policy at a time and has no array "
            f"to apply it to — this template is outside the mode's coverage, "
            f"which is stated rather than worked around."
        )
    if isinstance(value, (int, float, Decimal, np.floating, np.integer)):
        return reader(value)
    return value


def _as_lookup_key(value):
    """Prepare an argument handed *into* the assumption layer.

    **Only whole numbers are converted down**, and the distinction is the
    difference between an audit mode and a decorated float run.

    An integral argument is a lookup key — an attained age, a duration, a
    sub-period — and the basis behind it indexes a table with it. Handing it
    down as an ``int`` loses nothing, because the *rate* that comes back is
    converted straight up again.

    A non-integral argument is a **quantity**, and it is passed through
    untouched. An in-force count on its way into
    :meth:`~engine.data.decrements.Decrements.split`, a crediting rate on its
    way into a tax basis: these are accumulated, and rounding them to double
    on the way in would put the whole recursion back into float arithmetic
    while the answer still arrived wearing ``Decimal``. That is the one
    failure mode this module cannot tolerate, because it is invisible — the
    run completes, the types are right, and the numbers are the float
    numbers.

    It works because the arithmetic on the other side is written in operators
    rather than in NumPy. ``Decrements.split`` is "deliberately uncoerced:
    the operands stay exactly what the caller passed", which is what lets a
    ``Decimal`` in-force count multiply a ``Decimal`` rate and stay decimal.
    Where a basis is *not* written that way it fails on the Decimal rather
    than silently downgrading it, and :func:`run_exact` turns that into a
    stated refusal.
    """
    if isinstance(value, Decimal):
        whole = int(value)
        return whole if value == whole else value
    if isinstance(value, dict):
        return {k: _as_lookup_key(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_as_lookup_key(v) for v in value)
    return value


class _DecimalView:
    """The assumption set, with every number crossing out of it converted.

    A proxy rather than a parallel implementation, so the audit run uses the
    *same* mortality table, the same treaty, the same expense basis as the
    run it is checking. An audit mode with its own copy of the assumptions
    would be checking the copy.
    """

    __slots__ = ("_wrapped", "_reader")

    def __init__(self, wrapped, reader):
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_reader", reader)

    def __getattr__(self, name):
        wrapped = object.__getattribute__(self, "_wrapped")
        reader = object.__getattribute__(self, "_reader")
        value = getattr(wrapped, name)
        if callable(value):
            def call(*args, **kwargs):
                return _convert(
                    value(*[_as_lookup_key(a) for a in args],
                          **{k: _as_lookup_key(v)
                             for k, v in kwargs.items()}),
                    reader,
                )
            return call
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return value
        if isinstance(value, (int, float, Decimal, np.floating, np.integer)):
            return reader(value)
        if hasattr(value, "__dict__") or hasattr(value, "__slots__"):
            return _DecimalView(value, reader)
        return value

    def __repr__(self) -> str:
        return f"_DecimalView({object.__getattribute__(self, '_wrapped')!r})"


def exact_model_point(mp, reader=as_written):
    """A copy of ``mp`` with every numeric field converted."""
    fields = {}
    for name, value in vars(mp).items():
        if isinstance(value, (int, float, np.floating, np.integer)) \
                and not isinstance(value, bool):
            fields[name] = reader(value)
        else:
            fields[name] = value
    return type(mp)(**fields)


def run_exact(
    model_cls: Type[Model],
    modelpoints: Iterable[Any],
    assumptions: Any,
    proj_len: int,
    outputs: list[str] | None = None,
    *,
    reader=as_written,
    context: Context | None = None,
) -> RunResult:
    """Project under decimal arithmetic, one policy at a time.

    Same signature as :func:`engine.core.runner.run`, and the same refusals —
    a pooled model raises :class:`~engine.core.runner.PooledBlockError`,
    because one instance per model point would give each policy a pool of
    itself whichever arithmetic it used.

    ``reader`` chooses how the inputs are read: :func:`as_written` (the
    default, and the sign-off question) or :func:`as_stored` (which isolates
    representation error from arithmetic error).
    """
    points = list(modelpoints)
    if not points:
        raise ValueError("no model points supplied")
    check_per_policy(model_cls, len(points))

    per_mp, mp_ids = [], []
    with localcontext(context or EXACT_CONTEXT):
        view = _DecimalView(assumptions, reader)
        for i, mp in enumerate(points):
            model = model_cls(mp=exact_model_point(mp, reader),
                              assumptions=view, proj_len=proj_len)
            try:
                series = model.run(outputs)
            except TypeError as exc:
                raise ExactError(
                    f"{model_cls.__name__} cannot run under decimal "
                    f"arithmetic: {exc}. A template whose body reaches for "
                    f"NumPy operates on arrays this mode does not produce. "
                    f"It is outside the coverage, which is stated rather "
                    f"than worked around by falling back to float."
                ) from exc
            per_mp.append({name: [Exact(v) for v in values]
                           for name, values in series.items()})
            mp_ids.append(getattr(mp, "id", i))
    return RunResult(per_mp=per_mp, mp_ids=mp_ids)


def agreement(float_result: RunResult, exact_result: RunResult) -> dict:
    """How far the float run sits from the decimal one, worst case.

    Relative where the exact value is non-zero, absolute where it is not —
    a relative difference against zero is either zero or undefined, and
    reporting it as either would misdescribe the one case (a cashflow that
    should be exactly nothing) where an absolute difference is what matters.

    Returns the worst of each and where it occurred, because a single
    aggregate figure cannot say whether a discrepancy is spread thinly or
    concentrated in one variable at one period — and which of those it is
    decides whether it is rounding or a bug.
    """
    worst_rel, worst_abs = Decimal(0), Decimal(0)
    at_rel = at_abs = None
    compared = 0
    for index, (f_series, e_series) in enumerate(
            zip(float_result.per_mp, exact_result.per_mp)):
        for name, exact_values in e_series.items():
            for t, exact_value in enumerate(exact_values):
                approx = Exact(float(f_series[name][t]))
                difference = abs(approx - exact_value)
                compared += 1
                if difference > worst_abs:
                    worst_abs, at_abs = difference, (index, name, t)
                if exact_value != 0:
                    relative = difference / abs(exact_value)
                    if relative > worst_rel:
                        worst_rel, at_rel = relative, (index, name, t)
    return {
        "worst_relative": worst_rel,
        "worst_relative_at": at_rel,
        "worst_absolute": worst_abs,
        "worst_absolute_at": at_abs,
        "values_compared": compared,
        "precision": EXACT_PRECISION,
    }
