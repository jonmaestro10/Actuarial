"""A starting point that tells the truth about what it does not know.

RFC-036. Given what RFC-034 read — a client's model points — and the list of
variables their results file carries, this emits a Python module: a
:class:`~engine.core.model.Model` subclass with one ``@var`` stub per
incumbent variable, each stub carrying the nearest variable in the shipped
library as a *suggestion*, plus a ready-to-run parity mapping over the same
names.

It is not a converter. Nothing here reads an incumbent formula, and a tool
that claimed to would be the most expensive kind of wrong: a model that runs,
produces numbers, and is subtly not the client's product. What it does is
remove the mechanical half of the porting job — enumerating the variables,
naming them legally, wiring the reconciliation — and hand the actuarial half
back with a table saying exactly where it guessed.

**Why guessing is allowed here and forbidden in the reader.** RFC-034 refuses
to map a model-point field by name similarity, because that guess is *inert
to the eye and live to the arithmetic*: nobody sees `DURATION_IF_M` become
`duration_in_force`, and the projection is wrong. The guesses here are the
other way round. They land in generated source that a human must edit before
it computes anything at all — every stub raises — and each one is labelled
with its confidence in the docstring and again in the mapping table. A
suggestion that has to be read before it can matter is a suggestion that can
be wrong safely.
"""

from __future__ import annotations

import difflib
import functools
import importlib
import inspect
import keyword
import pkgutil
import re
import textwrap
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from engine.core.model import Model

#: Prophet's conventional result-variable names against the library's, where
#: the two are the same quantity under different spellings. Consulted before
#: string similarity, because ``DEATHS`` and ``pols_death`` do not look alike
#: and are the same thing, while ``DEATHS`` and ``pols_lapse`` look more
#: alike than either deserves.
DEFAULT_VARIABLE_ALIASES: Mapping[str, str] = {
    "POLS_IF": "pols_if",
    "POLS_INFORCE": "pols_if",
    "NO_POLS_IF": "pols_if",
    "DEATHS": "pols_death",
    "NO_DEATHS": "pols_death",
    "DTH_CLAIM": "claims",
    "DEATH_CLAIMS": "claims",
    "CLAIMS": "claims",
    "SURRENDERS": "pols_lapse",
    "LAPSES": "pols_lapse",
    "PREM_INCOME": "premiums",
    "PREMIUM_INCOME": "premiums",
    "PREMIUMS": "premiums",
    "EXPENSES": "expenses",
    "INIT_EXPENSES": "initial_expenses",
    "COMMISSION": "commission",
    "RESERVE": "reserve",
    "DISC_FACTOR": "v",
    "PROFIT": "profit_before_tax",
}

#: Similarity at or above which a suggestion is called *close* rather than
#: *weak*. Both are suggestions; the band is what a reviewer triages by.
CLOSE = 0.75
WEAK = 0.5


@functools.lru_cache(maxsize=1)
def library_variables() -> dict[str, tuple[str, ...]]:
    """Every ``@var`` the shipped templates define, and which define it.

    Read off the library rather than curated, for the same reason RFC-034
    reads the model-point catalogue off it: a suggestion list that has gone
    stale suggests variables that no longer exist.
    """
    import engine.library as library

    found: dict[str, set[str]] = {}
    for module_info in pkgutil.iter_modules(library.__path__):
        module = importlib.import_module(f"engine.library.{module_info.name}")
        for cls in vars(module).values():
            if (inspect.isclass(cls) and issubclass(cls, Model)
                    and cls is not Model and cls.__module__ == module.__name__):
                for name in cls.var_names():
                    found.setdefault(name, set()).add(cls.__qualname__)
    return {name: tuple(sorted(owners)) for name, owners in sorted(found.items())}


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def identifier(name: str, taken: Iterable[str] = ()) -> str:
    """A legal, non-colliding Python name for an incumbent variable.

    Incumbent names carry spaces, brackets and leading digits, and some of
    them collide with :class:`~engine.core.model.Model`'s own API. All three
    are renamed rather than allowed to fail at class-creation time — and the
    rename is reported, because a variable whose name changed is a variable
    somebody has to be able to find again.
    """
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if not cleaned:
        cleaned = "variable"
    if cleaned[0].isdigit():
        cleaned = f"v_{cleaned}"
    if keyword.iskeyword(cleaned) or hasattr(Model, cleaned):
        cleaned = f"{cleaned}_"
    candidate, n = cleaned, 2
    taken = set(taken)
    while candidate in taken:
        candidate, n = f"{cleaned}_{n}", n + 1
    return candidate


@dataclass(frozen=True)
class VariableSuggestion:
    """One incumbent variable, and the nearest thing the library ships.

    ``confidence`` is the whole point: *exact* (an alias or the same name),
    *close*, *weak*, or *none* — and a reviewer's attention goes to the
    bottom of that list, which is where the table puts it.
    """

    external: str
    stub: str
    suggested: str | None
    templates: tuple[str, ...]
    score: float
    confidence: str

    @property
    def needs_review(self) -> bool:
        return self.confidence in ("weak", "none")

    def __fingerprint__(self):
        return {"external": self.external, "stub": self.stub,
                "suggested": self.suggested, "score": self.score,
                "confidence": self.confidence}


def suggest(name: str, *, aliases: Mapping[str, str] | None = None
            ) -> tuple[str | None, tuple[str, ...], float, str]:
    """The nearest library variable to an incumbent name, and how near."""
    catalogue = library_variables()
    aliases = DEFAULT_VARIABLE_ALIASES if aliases is None else aliases
    alias = aliases.get(name.upper())
    if alias and alias in catalogue:
        return alias, catalogue[alias], 1.0, "exact"
    target = _normalise(name)
    if not target:
        return None, (), 0.0, "none"
    best, score = None, 0.0
    for candidate in catalogue:
        ratio = difflib.SequenceMatcher(None, target, _normalise(candidate)).ratio()
        if ratio > score or (ratio == score and best is not None
                             and candidate < best):
            best, score = candidate, ratio
    if best is not None and _normalise(best) == target:
        return best, catalogue[best], 1.0, "exact"
    if score >= CLOSE:
        return best, catalogue[best], score, "close"
    if score >= WEAK:
        return best, catalogue[best], score, "weak"
    return None, (), score, "none"


@dataclass(frozen=True)
class Scaffold:
    """A generated module, its mapping table, and the parity wiring."""

    class_name: str
    source: str
    suggestions: tuple[VariableSuggestion, ...]
    mapping: Mapping[str, str]
    modelpoint_fields: tuple[str, ...]
    id_column: str
    time_column: str
    origin: str | None = None

    @property
    def unmapped(self) -> tuple[str, ...]:
        """Incumbent variables the library had nothing to offer for."""
        return tuple(s.external for s in self.suggestions
                     if s.suggested is None)

    @property
    def needs_review(self) -> tuple[str, ...]:
        return tuple(s.external for s in self.suggestions if s.needs_review)

    def to_markdown(self) -> str:
        """The mapping table — every input variable, one row, no exceptions."""
        out = ["# Conversion scaffold", ""]
        if self.origin:
            out += [f"Source: `{self.origin}`.", ""]
        out += [
            f"`{self.class_name}` declares {len(self.suggestions)} variables, "
            f"every one a stub that raises until somebody ports the formula.",
            "",
            "| incumbent variable | stub | nearest library variable | seen in "
            "| confidence |",
            "|---|---|---|---|---|",
        ]
        for entry in sorted(self.suggestions,
                            key=lambda s: ({"none": 0, "weak": 1, "close": 2,
                                            "exact": 3}[s.confidence],
                                           s.external)):
            suggested = f"`{entry.suggested}`" if entry.suggested else "—"
            templates = (", ".join(entry.templates[:3]) if entry.templates
                         else "—")
            out.append(f"| `{entry.external}` | `{entry.stub}` | {suggested} "
                       f"| {templates} | {entry.confidence} "
                       f"({entry.score:.2f}) |")
        if self.unmapped:
            out += ["", f"**{len(self.unmapped)} variable(s) with no "
                    f"suggestion**: "
                    + ", ".join(f"`{n}`" for n in self.unmapped)
                    + ". The library ships nothing resembling them; they are "
                      "either the product's own mechanics or named in a house "
                      "convention nothing here can read."]
        if self.modelpoint_fields:
            out += ["", "## Model point fields available", "",
                    ", ".join(f"`{name}`" for name in self.modelpoint_fields)]
        return "\n".join(out).rstrip() + "\n"

    def write(self, path, *, report: bool = True) -> None:
        """Write the module, and the mapping table beside it."""
        from pathlib import Path

        path = Path(path)
        path.write_text(self.source, encoding="utf-8")
        if report:
            path.with_suffix(".md").write_text(self.to_markdown(),
                                               encoding="utf-8")

    def __fingerprint__(self):
        return {"class_name": self.class_name, "source": self.source,
                "suggestions": list(self.suggestions),
                "mapping": dict(self.mapping),
                "modelpoint_fields": list(self.modelpoint_fields)}


_HEADER = '''"""{class_name} — scaffold generated from {origin}.

NOT A CONVERSION. Every variable below is a stub that raises: the incumbent
formulas were never read, and no number this module produces would mean
anything until somebody ports them one at a time. What is done for you is the
mechanical part — the variable list, legal names for it, the nearest library
variable to start from, and a parity mapping over the same names so a
reconciliation can be run the moment the first variable is real.

Suggested starting points are in the docstring of each stub and in the
mapping table written alongside this file. Confidence is stated. Check it.

Model point fields available from the incumbent extract:
{fields}
"""

from engine.core.model import Model, var


class {class_name}(Model):
    """A model in name only, until each stub below is a formula."""
'''

_STUB = '''
    @var
    def {stub}(self, t):
        """{external}{suggestion}

        {guidance}
        """
        raise NotImplementedError(
            "{stub}: port the incumbent formula for {external!s}"
        )
'''

_FOOTER = '''

#: Incumbent results column -> variable on {class_name} above. Ready for
#: :func:`engine.parity.diff` as soon as the stubs are real.
MAPPING = {mapping}

ID_COLUMN = {id_column!r}
TIME_COLUMN = {time_column!r}


def parity_spec(result, external, **options):
    """A :class:`~engine.parity.diff.ParitySpec` over MAPPING.

    ``result`` is a run of this model, ``external`` the incumbent extract
    read by :func:`engine.migrate.prophet.read_results`.
    """
    from engine.parity import ParitySpec

    return ParitySpec.from_results(
        result, external, MAPPING, id_column=ID_COLUMN,
        time_column=TIME_COLUMN, **options,
    )
'''


def scaffold(variables: Sequence[str], *,
             class_name: str = "ConvertedModel",
             modelpoints: Iterable | None = None,
             id_column: str = "modelpoint_id",
             time_column: str = "t",
             origin: str | None = None,
             aliases: Mapping[str, str] | None = None,
             skip: Sequence[str] = ()) -> Scaffold:
    """Emit a model skeleton and a parity mapping for an incumbent variable list.

    ``variables`` is the incumbent results file's column list — typically
    ``read_results(path).names`` with the key columns skipped, which is what
    ``skip`` is for. ``modelpoints`` is an RFC-034 read, used only to record
    in the generated module which fields the client's data actually carries;
    a scaffold without it is still a scaffold, one that says nothing about
    the data.
    """
    skipped = {name for name in (*skip, id_column, time_column)}
    names = [name for name in variables if name not in skipped]
    if not names:
        raise ValueError("no variables to scaffold")
    if len(set(names)) != len(names):
        duplicated = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"duplicate variables in the input list: {duplicated}")

    fields: tuple[str, ...] = ()
    if modelpoints is not None:
        points = list(modelpoints)
        if points:
            fields = tuple(sorted(vars(points[0])))

    suggestions: list[VariableSuggestion] = []
    taken: list[str] = []
    for name in names:
        stub = identifier(name, taken)
        taken.append(stub)
        suggested, templates, score, confidence = suggest(name, aliases=aliases)
        suggestions.append(VariableSuggestion(
            external=name, stub=stub, suggested=suggested, templates=templates,
            score=score, confidence=confidence,
        ))

    origin_text = origin or "an incumbent results extract"
    body = [_HEADER.format(
        class_name=class_name, origin=origin_text,
        fields=("  " + ", ".join(fields)) if fields else "  (none supplied)",
    )]
    for entry in suggestions:
        if entry.suggested:
            suggestion = (f" — suggested starting point: `{entry.suggested}`"
                          f" ({entry.confidence}, {entry.score:.2f})")
            guidance = (
                f"Nearest library variable `{entry.suggested}`"
                + (f", defined by {', '.join(entry.templates[:3])}."
                   if entry.templates else ".")
                + (" Similarity only — confirm it is the same quantity."
                   if entry.confidence != "exact" else "")
            )
        else:
            suggestion = " — no library variable resembles this one"
            guidance = ("Nothing in the library resembles this name. It is "
                        "either product-specific mechanics or a house naming "
                        "convention; port it from the incumbent definition.")
        body.append(_STUB.format(
            stub=entry.stub, external=entry.external, suggestion=suggestion,
            guidance="\n        ".join(textwrap.wrap(guidance, 70)),
        ))

    mapping = {entry.external: entry.stub for entry in suggestions}
    rendered = "{\n" + "".join(
        f"    {external!r}: {stub!r},\n" for external, stub in mapping.items()
    ) + "}"
    body.append(_FOOTER.format(class_name=class_name, mapping=rendered,
                               id_column=id_column, time_column=time_column))

    return Scaffold(
        class_name=class_name, source="".join(body),
        suggestions=tuple(suggestions), mapping=mapping,
        modelpoint_fields=fields, id_column=id_column,
        time_column=time_column, origin=origin,
    )


def scaffold_from_results(results_path, *, dialect=None, class_name: str =
                          "ConvertedModel", modelpoints: Iterable | None = None,
                          id_column: str = "modelpoint_id",
                          time_column: str = "t", **options) -> Scaffold:
    """Scaffold straight off a Prophet results extract.

    The columns naming the model point and the time step are the two the
    parity spec needs by name, so they are renamed on the way in and excluded
    from the variable list — a scaffold with a ``@var`` called ``t`` would be
    a scaffold that cannot run.
    """
    from engine.migrate.prophet import RESULTS_DIALECT, read_results

    table = read_results(results_path, dialect or RESULTS_DIALECT)
    return scaffold(
        table.names, class_name=class_name, modelpoints=modelpoints,
        id_column=id_column, time_column=time_column,
        origin=str(results_path), **options,
    )
