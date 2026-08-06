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

Two object-valued *fields* are carried too. ``IncomeProtection`` binds a
:class:`~engine.data.multistate.TransitionMatrix` on
:class:`~engine.data.assumptions.Assumptions` alongside ``interest``, and
``FixedIndexedAnnuity`` binds an
:class:`~engine.data.index_credit.IndexCredit` in the same place, so neither
needs a new ``kind`` — they need ``assumptions.transitions`` and
``assumptions.index_credit``, which the scalar kind now takes. Both builders
are deliberately thin: ``TransitionMatrix`` already refuses a row that does
not sum to one, a probability outside ``[0, 1]``, and an absorbing state
whose row lets the population leave, and ``IndexCredit`` already refuses a
floor above its cap and a monthly design on an annual step. The schema
constructs and lets the class do the arguing. A second validation here would
be a second opinion about the same object, and the two would drift.

``assumptions.index_credit`` needs no discriminator of its own invention:
:meth:`engine.data.index_credit.IndexCredit.__fingerprint__` already
publishes ``{"kind": type(self).__name__, ...}``, so the request says the
class name the object already says about itself, and the designs are
*discovered* from the module the same way :func:`catalogue` discovers
templates.

The scenario set is not an assumption
-------------------------------------
Three templates were unavailable for wanting a **bound scenario set** and a
fourth for wanting an index-crediting rule that reads one. That is now
carried, and the interesting part is *where*.

RFC-066 left a rule behind: a basis is a ``kind``, a field is a field. A
scenario set is neither, and the engine already says so.
:func:`~engine.core.registry.record_run` takes ``scenarios`` as a sibling of
``assumptions``, and :class:`~engine.core.registry.RunRecord` carries
``scenarios_digest`` *beside* ``assumptions_digest`` rather than inside it.
So it is a **top-level request key**, next to ``modelpoints``. Filing it
under ``assumptions`` would have been a category error the run record
already refuses to make.

``scenarios`` is a discriminated union on ``kind``:

- ``"explicit"`` — the numbers themselves, one or more named series;
- ``"flat"`` — a constant rate, which is
  :meth:`~engine.data.scenarios.ScenarioSet.flat`;
- ``"lognormal"`` — parameters and a seed, which is
  :meth:`~engine.data.scenarios.ScenarioSet.lognormal`.

**Two identities, and only one of them is over the numbers.**
:meth:`engine.data.scenarios.ScenarioSet.__fingerprint__` covers the values;
:meth:`engine.api.store.RunStore.identify` covers the *request*. For
``"explicit"`` those are the same question. For a generated set they are
not: the request digest covers the parameters and the seed, and NumPy does
not promise that ``default_rng`` produces the same stream across feature
releases — only the legacy ``RandomState`` is frozen. So a request that
names a seed is identified by a recipe, and the run record's
``scenarios_digest`` — which is over the values — is the identity that is
safe to cite. ``tests/test_api_scenarios.py`` pins the digest of the
generated set the worked examples use, so a NumPy upgrade that moves the
stream fails the suite loudly rather than revaluing four templates in
silence.

**An index is not a return**, and the explicit form can say which it has.
``values_are`` is ``"return"`` by default and ``"index"`` converts through
:func:`engine.data.esg.returns_from_index`, which refuses to guess the level
at time zero. It is not spelled ``kind`` because ``kind`` is already the
union's discriminator here, and one word answering two questions in the same
object is how a schema starts lying. A generated set has no such field: a
generator produces returns by construction.

``source`` is deliberately **not** carried. It is outside
``ScenarioSet.__fingerprint__`` on purpose — two sets holding the same
numbers are the same set whatever file they came from — so admitting it
would let two requests with different digests build one run, which is the
one thing the request digest exists to prevent.

What is still out of scope, and now for a reason of its own
-----------------------------------------------------------
:class:`~engine.data.account.AccountBasis` stays out, and no longer under
the general heading. It is not one settled class but five —
``Corridor``, ``SurrenderCharge``, ``CreditingBasis``, ``CostOfInsurance``
and ``NoLapseGuarantee`` — and a request key spelled ``account`` that
carried the surrender-charge schedule alone would name the whole basis and
mean a fifth of one. The default basis is the identity (it deducts nothing
and credits nothing), so a request omitting it gets a contract with no
surrender charge — which the ``FixedIndexedAnnuity`` specimen says out loud
rather than leaving to be discovered from a cash value that equals the
account.

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
from engine.data.scenarios import PRIMARY, ScenarioSet

#: The assumption fields a request may set. Everything else on
#: :class:`Assumptions` is an object rather than a scalar and is out of
#: scope — see the module docstring.
SCALAR_ASSUMPTIONS = (
    "lapse", "interest", "expense_per_policy", "crediting_rate", "amc",
    "gmdb_fee", "gmab_fee", "gmwb_fee", "glwb_fee", "freq", "base_year",
    "fractional_ages",
)

#: Assumption fields that are **objects** rather than scalars, and are
#: nonetheless expressible: each is a settled class with a shape a JSON
#: object maps onto directly. Everything else on :class:`Assumptions` stays
#: out — see the module docstring.
OBJECT_ASSUMPTIONS = ("transitions", "index_credit")

EXECUTORS = ("auto", "vectorized", "interpreted", "stochastic")

#: What ``assumptions.kind`` may say. ``"scalar"`` is the default and is
#: what every request written before this existed already means.
ASSUMPTION_KINDS = ("scalar", "valuation_basis", "longevity_swap_basis")

#: What ``scenarios.kind`` may say. There is **no default**: a request that
#: bothers to carry a scenario set has already decided whether it is
#: supplying numbers or a recipe for them, and the two are identified
#: differently (module docstring). Guessing would pick the identity for the
#: caller.
SCENARIO_KINDS = ("explicit", "flat", "lognormal")

#: Whether the explicit form's numbers are per-period returns or a
#: cumulative index. Spelled ``values_are`` rather than ``kind`` because
#: ``kind`` is this object's union discriminator; :mod:`engine.data.esg`
#: calls the same distinction ``kind`` and has no union to collide with.
SCENARIO_MEASURES = ("return", "index")

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


def build_transitions(spec: Any) -> "TransitionMatrix":
    """A :class:`~engine.data.multistate.TransitionMatrix` from JSON.

    ``states`` is ``{"names": [...], "absorbing": [...]}`` and ``matrix`` is
    either ``(n, n)`` for an age-independent chain or ``(n_ages, n, n)`` with
    ``min_age`` naming the first row.

    Thin on purpose. ``TransitionMatrix`` already refuses a row that does not
    sum to one, a probability outside ``[0, 1]``, and an absorbing state whose
    row lets the population leave — and its messages say *which* row and by
    how much. Re-checking any of that here would be a second opinion about
    the same matrix, and the two would drift.
    """
    from engine.data.multistate import StateSpace, TransitionMatrix

    if not isinstance(spec, dict):
        raise InvalidRequestError("assumptions.transitions must be an object")
    spec = dict(spec)
    states = spec.pop("states", None)
    matrix = spec.pop("matrix", None)
    if not isinstance(states, dict) or matrix is None:
        raise InvalidRequestError(
            "assumptions.transitions needs both 'states' and 'matrix'. The "
            "matrix is meaningless without the state order it was written "
            "for — row 2 is not 'sick' unless something says so."
        )
    unknown = sorted(set(spec) - {"min_age"})
    if unknown:
        raise InvalidRequestError(
            f"unsupported transitions fields {unknown}"
        )
    names = states.get("names")
    if not isinstance(names, list):
        raise InvalidRequestError(
            "assumptions.transitions.states.names must be a list, in the "
            "order the matrix rows are written"
        )
    try:
        space = StateSpace(names, states.get("absorbing", ()))
        return TransitionMatrix(matrix, space, **spec)
    except (TypeError, ValueError, KeyError) as exc:
        raise InvalidRequestError(f"assumptions.transitions: {exc}") from exc


def index_credit_designs() -> dict:
    """Every crediting design :mod:`engine.data.index_credit` ships, by name.

    Discovered rather than listed, the same move :func:`catalogue` makes for
    templates: a design is exposed by existing. The abstract base is left
    out — it raises :class:`NotImplementedError` from both of its methods,
    so a request naming it would build an object that fails at the first
    anniversary rather than at the boundary.
    """
    from engine.data import index_credit as module

    return {
        name: cls for name, cls in sorted(vars(module).items())
        if (inspect.isclass(cls) and issubclass(cls, module.IndexCredit)
            and cls is not module.IndexCredit)
    }


def build_index_credit(spec: Any) -> Any:
    """An :class:`~engine.data.index_credit.IndexCredit` from JSON.

    ``kind`` is the **class name**, which is what
    :meth:`~engine.data.index_credit.IndexCredit.__fingerprint__` already
    publishes as the design's discriminator. Mirroring it means the request
    and the digest agree about what the thing is called, rather than the
    schema inventing a second vocabulary for the same three classes.

    Thin, like :func:`build_transitions`. The class refuses a floor above
    its cap, a non-positive cap or participation rate, a negative spread,
    and — through
    :meth:`~engine.data.index_credit.IndexCredit.check_freq`, called from
    :class:`~engine.data.assumptions.Assumptions` — a monthly design on an
    annual projection. All of those arrive here as an
    :class:`InvalidRequestError` with the class's own message.
    """
    designs = index_credit_designs()
    if not isinstance(spec, dict):
        raise InvalidRequestError("assumptions.index_credit must be an object")
    spec = dict(spec)
    kind = spec.pop("kind", None)
    if kind not in designs:
        raise InvalidRequestError(
            f"assumptions.index_credit.kind must be one of {sorted(designs)}, "
            f"got {kind!r}. The three designs are different products, not "
            f"parameters of one — a monthly-sum cap is not an annual cap."
        )
    unknown = sorted(set(spec) - {"cap", "participation", "spread", "floor"})
    if unknown:
        raise InvalidRequestError(
            f"unsupported index_credit fields {unknown}"
        )
    try:
        return designs[kind](**spec)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestError(f"assumptions.index_credit: {exc}") from exc


def _rectangle(values: Any, where: str) -> list:
    """A JSON list-of-lists, checked for being one before NumPy sees it.

    ``np.asarray`` on a ragged list raises a message about inhomogeneous
    shapes and object dtypes, which describes NumPy rather than the request.
    """
    if not isinstance(values, list) or not values:
        raise InvalidRequestError(
            f"{where} must be a non-empty list of scenario rows"
        )
    widths = set()
    for i, row in enumerate(values):
        if not isinstance(row, list) or not row:
            raise InvalidRequestError(
                f"{where}[{i}] must be a non-empty list of period values"
            )
        widths.add(len(row))
        for j, value in enumerate(row):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidRequestError(
                    f"{where}[{i}][{j}] must be a number, got {value!r}"
                )
    if len(widths) != 1:
        raise InvalidRequestError(
            f"{where}: every scenario must cover the same number of periods; "
            f"got lengths {sorted(widths)}"
        )
    return values


def _explicit_series(spec: dict) -> tuple[dict, str]:
    """``(series, primary)`` from the explicit form's two accepted shapes.

    ``returns`` is the single-series shorthand every template that reads
    ``ret(t)`` wants; ``series`` is the general form, and is the only one
    that can carry a second series at all.
    """
    returns = spec.pop("returns", None)
    series = spec.pop("series", None)
    if (returns is None) == (series is None):
        raise InvalidRequestError(
            "an explicit scenario set needs exactly one of 'returns' (one "
            "series) or 'series' (an object of named series)"
        )
    if returns is not None:
        return {PRIMARY: _rectangle(returns, "scenarios.returns")}, PRIMARY
    if not isinstance(series, dict) or not series:
        raise InvalidRequestError(
            "scenarios.series must be a non-empty object of name to rows"
        )
    built = {name: _rectangle(rows, f"scenarios.series.{name}")
             for name, rows in series.items()}
    primary = spec.pop("primary", None)
    if primary is None:
        raise InvalidRequestError(
            f"scenarios.primary is required with named series: it says which "
            f"of {sorted(built)} the templates' `ret(t)` reads, and there is "
            f"no sensible guess between an equity and a bond series"
        )
    return built, primary


def build_scenarios(spec: Any) -> ScenarioSet:
    """A :class:`~engine.data.scenarios.ScenarioSet` from JSON.

    A discriminated union on ``kind`` with no default — see the module
    docstring for why, and for the difference between the identity of an
    explicit set and the identity of a generated one.
    """
    if not isinstance(spec, dict):
        raise InvalidRequestError("scenarios must be an object")
    spec = dict(spec)
    kind = spec.pop("kind", None)
    if kind not in SCENARIO_KINDS:
        raise InvalidRequestError(
            f"scenarios.kind must be one of {list(SCENARIO_KINDS)}, got "
            f"{kind!r}"
        )
    try:
        if kind == "flat":
            _reject_extra(spec, {"rate", "n_scenarios", "horizon"},
                          "scenarios")
            return ScenarioSet.flat(
                _number(spec, "rate", "scenarios"),
                _count(spec, "n_scenarios", "scenarios"),
                _count(spec, "horizon", "scenarios"),
            )
        if kind == "lognormal":
            _reject_extra(
                spec, {"n_scenarios", "horizon", "drift", "vol", "seed"},
                "scenarios",
            )
            seed = spec.get("seed")
            if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
                raise InvalidRequestError(
                    "scenarios.seed is required and must be a non-negative "
                    "integer: a generated set without one is a different set "
                    "every time it is submitted, and the run registry would "
                    "record that as an engine that cannot repeat itself"
                )
            return ScenarioSet.lognormal(
                _count(spec, "n_scenarios", "scenarios"),
                _count(spec, "horizon", "scenarios"),
                drift=_number(spec, "drift", "scenarios"),
                vol=_number(spec, "vol", "scenarios"),
                seed=seed,
            )
        series, primary = _explicit_series(spec)
        measure = spec.pop("values_are", "return")
        if measure not in SCENARIO_MEASURES:
            raise InvalidRequestError(
                f"scenarios.values_are must be one of "
                f"{list(SCENARIO_MEASURES)}, got {measure!r}"
            )
        index_base = spec.pop("index_base", None)
        starts_at = spec.pop("starts_at", 1)
        _reject_extra(spec, set(), "scenarios")
        if measure == "index":
            from engine.data.esg import returns_from_index

            series = {
                name: returns_from_index(rows, index_base=index_base,
                                         starts_at=starts_at)
                for name, rows in series.items()
            }
        elif index_base is not None or starts_at != 1:
            raise InvalidRequestError(
                "scenarios.index_base and scenarios.starts_at only apply to "
                "values_are='index'; a per-period return has no level at "
                "time zero to be quoted against"
            )
        return ScenarioSet(series=series, primary=primary)
    except InvalidRequestError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise InvalidRequestError(f"scenarios: {exc}") from exc


def _reject_extra(spec: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise InvalidRequestError(f"unsupported {where} fields {unknown}")


def _number(spec: dict, field: str, where: str) -> float:
    value = spec.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequestError(f"{where}.{field} must be a number")
    return float(value)


def _count(spec: dict, field: str, where: str) -> int:
    value = spec.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidRequestError(f"{where}.{field} must be a positive integer")
    return value


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
    objects = {}
    for field, build in (("transitions", build_transitions),
                         ("index_credit", build_index_credit)):
        value = spec.pop(field, None)
        if value is not None:
            objects[field] = build(value)
    unknown = set(spec) - set(SCALAR_ASSUMPTIONS)
    if unknown:
        raise InvalidRequestError(
            f"unsupported assumption fields {sorted(unknown)}; this API "
            f"carries {list(SCALAR_ASSUMPTIONS)} plus mortality and "
            f"{list(OBJECT_ASSUMPTIONS)}"
        )
    spec.update(objects)
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

    scenarios = request.get("scenarios")
    scenarios = None if scenarios is None else build_scenarios(scenarios)
    # ``record_run`` refuses both of these too, but it refuses them as a
    # *failed run* — queued, started, and then broken — where they are plainly
    # a malformed request. Same rule as everywhere else here: a 422 before
    # anything is queued beats a run that dies at its first step.
    if scenarios is not None and executor not in ("auto", "stochastic"):
        raise InvalidRequestError(
            f"a bound scenario set runs under the stochastic executor; "
            f"executor={executor!r} cannot see one. Drop the executor and it "
            f"is chosen for you."
        )
    if scenarios is None and executor == "stochastic":
        raise InvalidRequestError(
            "the stochastic executor needs a scenario set; add `scenarios` "
            "or drop the executor"
        )
    if scenarios is not None and scenarios.horizon < proj_len:
        raise InvalidRequestError(
            f"scenario horizon {scenarios.horizon} is shorter than proj_len "
            f"{proj_len}; the projection would run off the end of the "
            f"scenario set"
        )

    return {
        "model_cls": models[name],
        "modelpoints": from_dicts(coerce_dates(rows)),
        "assumptions": build_assumptions(request.get("assumptions", {})),
        "proj_len": proj_len,
        "outputs": outputs,
        "scenarios": scenarios,
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
