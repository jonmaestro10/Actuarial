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
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any, Callable

import engine.library as library
from engine.core.model import Model
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import from_dicts

#: The assumption fields a request may set. Everything else on
#: :class:`Assumptions` is an object rather than a scalar and is out of
#: scope — see the module docstring.
SCALAR_ASSUMPTIONS = (
    "lapse", "interest", "expense_per_policy", "crediting_rate", "amc",
    "gmdb_fee", "gmab_fee", "gmwb_fee", "glwb_fee", "freq", "base_year",
    "fractional_ages",
)

EXECUTORS = ("auto", "vectorized", "interpreted", "stochastic")


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


def build_assumptions(spec: Any) -> Assumptions:
    """An :class:`Assumptions` from the request's ``assumptions`` object.

    ``mortality`` is either a number — a flat rate at every age — or a
    mapping of age to rate, which is what
    :class:`~engine.data.assumptions.MortalityTable` takes and which it
    already validates for contiguity and range.
    """
    if not isinstance(spec, dict):
        raise InvalidRequestError("assumptions must be an object")
    spec = dict(spec)
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
        "modelpoints": from_dicts(rows),
        "assumptions": build_assumptions(request.get("assumptions", {})),
        "proj_len": proj_len,
        "outputs": outputs,
        "executor": executor,
        "code_version": request.get("code_version"),
    }


def builder(models: dict | None = None) -> Callable[[dict], dict]:
    """A ``build`` for :class:`engine.api.store.RunStore`, over ``models``."""
    resolved = catalogue() if models is None else dict(models)

    def build(request: dict) -> dict:
        return build_run(request, resolved)

    return build
