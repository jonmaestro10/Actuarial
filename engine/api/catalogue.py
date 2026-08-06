"""What the API will run, and how a JSON request becomes engine objects.

Two jobs, kept together because they are the same decision seen from both
ends: the catalogue says which models exist, and the builder says what a
request for one of them has to contain.

Why the assumption schema is small
----------------------------------
:class:`engine.data.assumptions.Assumptions` takes twenty-odd arguments,
most of them rich objects — a decrement table, a reinsurance treaty, a tax
basis, an index-credit rule. Exposing all of that over HTTP would mean
inventing a serialisation format for every one of them, and a format
invented here would be wrong the moment any of those classes changed.

So this exposes the scalar basis plus a flat mortality table, and says so.
A deployment that needs more passes its own builder to
:func:`engine.api.app.create_app`. That is the same move RFC-014 made for
the risk margin's run-off driver and RFC-027 for the deferred tax
demonstration: where the right answer is somebody else's, take it as an
input rather than ship a guess.

The one assumption *object* that is worth the format
----------------------------------------------------
That reasoning held for twenty-odd rich objects and stopped holding for one
of them. Half the catalogue runs on a
:class:`~engine.data.basis.ValuationBasis` — a mortality basis and a yield
curve — and every one of those templates was unavailable over HTTP and
therefore invisible to the evidence pack's specimen set, which walks
``EXAMPLES``. The chassis is not exotic; it is where the library has been
growing, and each new template on it widened the same gap.

So ``assumptions`` is now a **discriminated union** on ``kind``:

- ``"scalar"`` (the default, and what every existing request already is) —
  the flat table and the scalar basis, unchanged in every particular;
- ``"valuation_basis"`` — a :class:`~engine.data.mortality.MortalityBasis`
  and a :class:`~engine.data.rates.YieldCurve`;
- ``"longevity_swap_basis"`` — two of the above, one per leg.

``kind`` defaults to ``"scalar"``, so no existing request changes meaning.
That default is load-bearing rather than merely convenient: a request that
omitted ``kind`` and got a different basis than it did last week would be a
silent revaluation, which is the one failure this whole layer exists to
prevent.

What is still out of scope is unchanged and is now the *whole* of what is
out of scope: a bound scenario set, a ``TransitionMatrix``, and an
index-crediting rule. Those are five templates, and each needs a format
invented for a moving class rather than a format for two settled ones.

Dates arrive as strings, and are coerced here rather than in the core
-------------------------------------------------------------------
JSON has no date. :func:`~engine.data.modelpoints.from_dicts` does not
coerce one — a string arrives as a string and the template asks it for a
year — and it should not start, because it is a core function with callers
who pass real objects and would not thank it for guessing.

So the coercion lives in :func:`build_run`, at the HTTP boundary where the
strings actually come from, and it is deliberately narrow: a value that is
*exactly* an ISO-8601 ``YYYY-MM-DD`` string becomes a
:class:`datetime.date`, and nothing else is touched. A model point field
holding exactly that and needing to stay a string is a case nobody has, and
the alternative it replaces — an ``AttributeError`` from inside a
projection naming ``year`` — is the failure this exists to remove.
"""

from __future__ import annotations

import datetime as _dt
import importlib
import inspect
import pkgutil
import re
from typing import Any, Callable

import engine.library as library
from engine.core.model import Model
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.basis import ValuationBasis
from engine.data.modelpoints import from_dicts
from engine.data.mortality import MortalityBasis
from engine.data.rates import YieldCurve

#: The assumption fields a request may set. Everything else on
#: :class:`Assumptions` is an object rather than a scalar and is out of
#: scope — see the module docstring.
SCALAR_ASSUMPTIONS = (
    "lapse", "interest", "expense_per_policy", "crediting_rate", "amc",
    "gmdb_fee", "gmab_fee", "gmwb_fee", "glwb_fee", "freq", "base_year",
    "fractional_ages",
)

EXECUTORS = ("auto", "vectorized", "interpreted", "stochastic")

#: What ``assumptions.kind`` may say. ``"scalar"`` is the default and is
#: what every request written before this existed already means.
ASSUMPTION_KINDS = ("scalar", "valuation_basis", "longevity_swap_basis")

#: A model-point value in exactly this shape becomes a ``datetime.date``.
#: Anchored at both ends on purpose: a partial or prefixed match is a
#: string that resembles a date, and guessing at those is how a coercion
#: rule stops being a rule.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UnknownModelError(LookupError):
    """The request named a model the catalogue does not carry."""


class InvalidRequestError(ValueError):
    """The request is malformed. Distinguished from a *failed run*: this is
    a 422 before anything is queued, not a run that queued and then broke."""


def catalogue() -> dict:
    """Every model the library ships, by name.

    Discovered by walking :mod:`engine.library` rather than listed, so a new
    template is exposed by existing. Private classes — those whose name
    begins with an underscore — are shared bases and are left out.
    """
    found = {}
    for module_info in pkgutil.iter_modules(library.__path__):
        module = importlib.import_module(f"engine.library.{module_info.name}")
        for name, cls in vars(module).items():
            if (inspect.isclass(cls) and issubclass(cls, Model)
                    and cls is not Model and cls.__module__ == module.__name__
                    and not name.startswith("_") and cls.var_names()):
                found[name] = cls
    return dict(sorted(found.items()))


def _ages(table: Any, where: str) -> dict:
    """``{age: rate}`` from a JSON object, whose keys are strings."""
    if not isinstance(table, dict):
        raise InvalidRequestError(f"{where} must be an age-to-rate object")
    try:
        return {int(age): float(rate) for age, rate in table.items()}
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"{where}: {exc}") from exc


def _by_sex(spec: Any, where: str) -> dict:
    """``{sex: {age: rate}}``, the layout every published table comes in."""
    if not isinstance(spec, dict) or not spec:
        raise InvalidRequestError(
            f"{where} must be a non-empty object keyed by sex code"
        )
    return {str(sex): _ages(table, f"{where}.{sex}")
            for sex, table in spec.items()}


def build_mortality_basis(spec: Any) -> MortalityBasis:
    """A :class:`~engine.data.mortality.MortalityBasis` from JSON.

    ``rates`` is ``{sex: {age: q}}`` and ``year_start`` is required, because
    a generational basis without the year its rates are quoted at is a table
    that means something different every year it is used.
    """
    if not isinstance(spec, dict):
        raise InvalidRequestError("mortality must be an object for this kind")
    spec = dict(spec)
    rates = spec.pop("rates", None)
    if rates is None:
        raise InvalidRequestError("mortality.rates is required")
    year_start = spec.pop("year_start", None)
    if not isinstance(year_start, int) or isinstance(year_start, bool):
        raise InvalidRequestError(
            "mortality.year_start is required and must be an integer: a "
            "generational basis without the year its rates are quoted at "
            "means something different every year it is used"
        )
    improvement = spec.pop("improvement", None)
    unknown = sorted(set(spec) - {"use_improvement", "calc",
                                  "actual_daycount", "omega"})
    if unknown:
        raise InvalidRequestError(f"unsupported mortality fields {unknown}")
    try:
        return MortalityBasis(
            _by_sex(rates, "mortality.rates"), year_start=year_start,
            improvement=(None if improvement is None
                         else _by_sex(improvement, "mortality.improvement")),
            **spec,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"mortality: {exc}") from exc


def build_curve(spec: Any) -> YieldCurve:
    """A :class:`~engine.data.rates.YieldCurve` from JSON.

    ``rates`` is a list of period rates — one entry is a flat curve, which
    is the common case and needs no special form.
    """
    if not isinstance(spec, dict):
        raise InvalidRequestError("curve must be an object")
    spec = dict(spec)
    rates = spec.pop("rates", None)
    if isinstance(rates, (int, float)) and not isinstance(rates, bool):
        rates = [float(rates)]
    if not isinstance(rates, list) or not rates:
        raise InvalidRequestError(
            "curve.rates must be a number or a non-empty list of rates"
        )
    unknown = sorted(set(spec) - {"freq", "horizon_years"})
    if unknown:
        raise InvalidRequestError(f"unsupported curve fields {unknown}")
    try:
        return YieldCurve([float(r) for r in rates], **spec)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"curve: {exc}") from exc


def build_valuation_basis(spec: dict) -> ValuationBasis:
    """A :class:`~engine.data.basis.ValuationBasis` from JSON."""
    spec = dict(spec)
    spec.pop("kind", None)
    mortality = spec.pop("mortality", None)
    curve = spec.pop("curve", None)
    if mortality is None or curve is None:
        raise InvalidRequestError(
            "a valuation_basis needs both assumptions.mortality and "
            "assumptions.curve; it is a mortality basis and a discount "
            "curve, and neither stands in for the other"
        )
    unknown = sorted(set(spec) - {"revalue_every"})
    if unknown:
        raise InvalidRequestError(f"unsupported basis fields {unknown}")
    try:
        return ValuationBasis(mortality=build_mortality_basis(mortality),
                              curve=build_curve(curve), **spec)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"assumptions: {exc}") from exc


def build_assumptions(spec: Any) -> Any:
    """The assumption object a request asks for, by ``kind``.

    ``kind`` defaults to ``"scalar"``, which is what every request written
    before the other kinds existed already means — and the default is
    load-bearing rather than convenient, because a request that omitted it
    and quietly got a different basis than it did last week would be a
    silent revaluation.
    """
    if not isinstance(spec, dict):
        raise InvalidRequestError("assumptions must be an object")
    kind = spec.get("kind", "scalar")
    if kind not in ASSUMPTION_KINDS:
        raise InvalidRequestError(
            f"assumptions.kind must be one of {list(ASSUMPTION_KINDS)}, got "
            f"{kind!r}"
        )
    if kind == "valuation_basis":
        return build_valuation_basis(spec)
    if kind == "longevity_swap_basis":
        from engine.library.longevity_swap import LongevitySwapBasis

        legs = {name: spec.get(name) for name in ("projection", "fixed")}
        missing = sorted(n for n, v in legs.items() if not isinstance(v, dict))
        if missing:
            raise InvalidRequestError(
                f"a longevity_swap_basis needs a valuation basis under each "
                f"of 'projection' and 'fixed'; {missing} is missing. The two "
                f"legs are two different survival schedules, which is the "
                f"whole content of the contract."
            )
        try:
            return LongevitySwapBasis(
                projection=build_valuation_basis(legs["projection"]),
                fixed=build_valuation_basis(legs["fixed"]))
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(f"assumptions: {exc}") from exc

    spec = dict(spec)
    spec.pop("kind", None)
    mortality = spec.pop("mortality", None)
    if mortality is None:
        raise InvalidRequestError("assumptions.mortality is required")
    if isinstance(mortality, (int, float)) and not isinstance(mortality, bool):
        table = MortalityTable.flat(float(mortality))
    elif isinstance(mortality, dict):
        try:
            table = MortalityTable({int(age): float(q)
                                    for age, q in mortality.items()})
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(f"assumptions.mortality: {exc}") from exc
    else:
        raise InvalidRequestError(
            "assumptions.mortality must be a number or an age-to-rate object"
        )
    unknown = set(spec) - set(SCALAR_ASSUMPTIONS)
    if unknown:
        raise InvalidRequestError(
            f"unsupported assumption fields {sorted(unknown)}; this API "
            f"carries {list(SCALAR_ASSUMPTIONS)} plus mortality"
        )
    try:
        return Assumptions(mortality=table, **spec)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"assumptions: {exc}") from exc


def build_run(request: dict, models: dict | None = None) -> dict:
    """Turn a request into keyword arguments for ``record_run``.

    Raises :class:`InvalidRequestError` for anything wrong with the request
    itself. A model that *runs* and then fails — a missing model-point
    field, say — is a failed run rather than a bad request, and belongs to
    the store.
    """
    models = catalogue() if models is None else models
    if not isinstance(request, dict):
        raise InvalidRequestError("a run request must be an object")

    name = request.get("model")
    if not name:
        raise InvalidRequestError("model is required")
    if name not in models:
        raise UnknownModelError(
            f"unknown model {name!r}; this deployment carries "
            f"{sorted(models)}"
        )

    rows = request.get("modelpoints")
    if not isinstance(rows, list) or not rows:
        raise InvalidRequestError("modelpoints must be a non-empty list")
    if not all(isinstance(row, dict) for row in rows):
        raise InvalidRequestError("every model point must be an object")

    proj_len = request.get("proj_len")
    if not isinstance(proj_len, int) or isinstance(proj_len, bool) \
            or proj_len < 1:
        raise InvalidRequestError("proj_len must be a positive integer")

    outputs = request.get("outputs")
    if outputs is not None:
        if not isinstance(outputs, list) or not outputs:
            raise InvalidRequestError("outputs, if given, must be a non-empty "
                                      "list")
        known = set(models[name].var_names())
        unknown = [o for o in outputs if o not in known]
        if unknown:
            raise InvalidRequestError(
                f"{name} has no variables {unknown}; it carries "
                f"{sorted(known)}"
            )

    executor = request.get("executor", "auto")
    if executor not in EXECUTORS:
        raise InvalidRequestError(
            f"executor must be one of {list(EXECUTORS)}, got {executor!r}"
        )

    return {
        "model_cls": models[name],
        "modelpoints": from_dicts(coerce_dates(rows)),
        "assumptions": build_assumptions(request.get("assumptions", {})),
        "proj_len": proj_len,
        "outputs": outputs,
        "executor": executor,
        "code_version": request.get("code_version"),
    }


def coerce_dates(rows: list) -> list:
    """ISO-8601 ``YYYY-MM-DD`` strings in model points become dates.

    Applied at the HTTP boundary rather than inside
    :func:`~engine.data.modelpoints.from_dicts`, which has callers passing
    real :class:`datetime.date` objects and should not start guessing at
    strings on their behalf. Narrow on purpose: an exact match and nothing
    else, so the rule stays a rule.

    A string that looks like a date and is not a real one — ``2021-13-01`` —
    is refused rather than left as a string, because a caller who wrote it
    meant a date and silently getting a string back is the outcome this
    function exists to prevent.
    """
    coerced = []
    for i, row in enumerate(rows):
        out = {}
        for field, value in row.items():
            if isinstance(value, str) and _ISO_DATE.match(value):
                try:
                    value = _dt.date.fromisoformat(value)
                except ValueError as exc:
                    raise InvalidRequestError(
                        f"modelpoints[{i}].{field}: {value!r} is shaped like "
                        f"a date and is not one ({exc})"
                    ) from exc
            out[field] = value
        coerced.append(out)
    return coerced


def builder(models: dict | None = None) -> Callable[[dict], dict]:
    """A ``build`` for :class:`engine.api.store.RunStore`, over ``models``."""
    resolved = catalogue() if models is None else dict(models)

    def build(request: dict) -> dict:
        return build_run(request, resolved)

    return build
