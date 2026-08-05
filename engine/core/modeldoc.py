"""Model documentation, generated from the model.

PLAN.md §7 asks for

    Auto-generated model documentation from ``@var`` docstrings + dependency
    graph visualizer (this replaces Prophet's formula browser).

The graph half has existed since RFC-001 —
:class:`engine.core.graph.DependencyGraph` renders Mermaid and describes
evaluation order. This is the other half: pull every ``@var``'s docstring,
its declared assumption, its **source**, and its place in the graph, and
write the lot out as Markdown.

Markdown rather than a viewer, deliberately. A formula browser you have to
launch is a thing you consult when you already suspect a problem. A Markdown
file is diffable, reviewable in a pull request, and greppable — and §7's
first bullet asks for *git-native model versioning*, which a generated
document only participates in if git can read it. GitHub renders the Mermaid
block natively, so the diagram comes for free.

The trap: a short trace documents a recursion as a constant
-----------------------------------------------------------
The dependency graph is discovered by **running** the model, not by parsing
it, because a ``@var`` body is ordinary Python. :meth:`Model.trace` runs
three periods by default and its docstring says a longer trace cannot find
new edges. That is true of every template in the library and false in
general — a ``@var`` may branch on ``t``, and ``t`` is not model-point data,
so a variable that first reaches back six periods at ``t = 6`` is invisible
to a three-period trace.

Measured on exactly such a variable: a three-period trace reports **no
dependencies at all** for a variable that reads itself six periods back. A
document generated from it would describe a recursion as a constant, and
nothing would raise.

So :func:`document` records the trace length it used in the output, and
:func:`graph_is_settled` re-traces at a longer length and compares. The
document says how far it looked, because a document that does not say is
not evidence of anything.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass

from engine.core.graph import DependencyGraph


@dataclass(frozen=True)
class ModelPointFields:
    """What a template reads from its model points.

    ``required`` is read directly and has nowhere to fall back to;
    ``optional`` is read through a ``getattr`` that supplies a default, so
    a model point without it still runs. ``reflective`` says the scan
    found a read it could not resolve to a name, and therefore that
    ``required`` is a lower bound rather than the answer.
    """

    required: tuple = ()
    optional: tuple = ()
    reflective: bool = False

    def __iter__(self):
        """Iterating gives the required fields — the common question."""
        return iter(self.required)


def _mp_read(node) -> bool:
    """Is this expression ``self.mp``?"""
    return (isinstance(node, ast.Attribute) and node.attr == "mp"
            and isinstance(node.value, ast.Name) and node.value.id == "self")


def modelpoint_fields(model_cls) -> ModelPointFields:
    """Which model-point fields a template reads, found by reading it.

    The dependency graph answers what a variable reads from *the model*;
    this answers what the model reads from *its data*, which is the other
    half of the question a formula browser is asked and the one a API
    caller needs before it can supply a model point at all. Nothing else in
    the engine knows: :class:`~engine.data.modelpoints.ModelPoint` is an
    open attribute bag with no schema, so a missing field surfaces as an
    ``AttributeError`` from inside a projection rather than as a rejected
    input.

    Found by parsing rather than by running, deliberately — the opposite of
    :meth:`~engine.core.model.Model.trace`. A trace discovers the fields
    *this* model point led the code to read, so a field wanted only on a
    branch the specimen did not take is invisible to it. The source has
    every branch in it.

    Required against optional is the distinction that makes the answer
    usable, and the source carries it: ``self.mp.sum_assured`` has nowhere
    to fall back to, while ``getattr(self.mp, "sex", None)`` says in its
    own third argument that the field may be absent. Every template in the
    library uses the second form for its options, so the split is read off
    the code rather than curated.

    What parsing cannot see is the read whose *name* is computed:
    :class:`~engine.library.unit_linked.UnitLinkedGMxB` collects its rider
    parameters out of ``self.mp.__dict__``, and no static scan can say what
    is in there. That sets ``reflective``, which is the scan reporting the
    limit of what it knows rather than a caller having to discover it from
    a projection that fails.
    """
    required: set = set()
    optional: set = set()
    reflective = False
    for klass in model_cls.__mro__:
        try:
            source = inspect.getsource(klass)
        except (OSError, TypeError):  # pragma: no cover - built without source
            continue
        try:
            tree = ast.parse(textwrap.dedent(source))
        except (SyntaxError, IndentationError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and _mp_read(node.value):
                if node.attr.startswith("__"):
                    # ``self.mp.__dict__``: the whole bag, by name unknown.
                    reflective = True
                else:
                    required.add(node.attr)
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and node.args and _mp_read(node.args[0])):
                name = node.args[1] if len(node.args) > 1 else None
                if not isinstance(name, ast.Constant) \
                        or not isinstance(name.value, str):
                    reflective = True
                elif len(node.args) > 2:
                    optional.add(name.value)
                else:
                    required.add(name.value)
    return ModelPointFields(
        required=tuple(sorted(required)),
        optional=tuple(sorted(optional - required)),
        reflective=reflective,
    )


def _source_of(fn) -> str:
    """The body of a ``@var``, with its decorator and indentation removed."""
    try:
        raw = inspect.getsource(fn)
    except (OSError, TypeError):  # pragma: no cover - built without source
        return ""
    lines = textwrap.dedent(raw).splitlines()
    while lines and lines[0].lstrip().startswith("@"):
        lines.pop(0)
    return "\n".join(lines).rstrip()


@dataclass
class VariableDoc:
    """One ``@var``: what it is, what it reads, and what reads it."""

    name: str
    doc: str | None = None
    assumption: str | None = None
    pooled: bool = False
    source: str = ""
    reads: tuple = ()
    read_by: tuple = ()

    @property
    def documented(self) -> bool:
        return bool(self.doc and self.doc.strip())

    @property
    def recursive(self) -> bool:
        """Does it read itself in an earlier period?"""
        return any(dep == self.name and offset < 0
                   for dep, offset in self.reads)

    @staticmethod
    def _reads_list(edges) -> str:
        """``reads`` comes back as ``(name, offset)`` pairs, so the offset
        is shown — a cross-period edge is what makes a projection a
        projection, and flattening it to a name would lose exactly the
        thing a formula browser exists to reveal."""
        return ", ".join(
            f"`{dep}`" if offset == 0 else f"`{dep}` [t{offset:+d}]"
            for dep, offset in sorted(edges)
        ) or "—"

    @staticmethod
    def _names_list(names) -> str:
        """``read_by`` comes back as plain names: the graph records that a
        caller read a variable, not the offset it read it at."""
        return ", ".join(f"`{name}`" for name in sorted(names)) or "—"

    def to_markdown(self) -> str:
        head = f"### `{self.name}`"
        if self.pooled:
            head += "  *(pooled)*"
        out = [head, ""]
        if self.assumption:
            out.append(f"**Assumption:** `{self.assumption}`")
            out.append("")
        out.append(inspect.cleandoc(self.doc) if self.documented
                   else "*No docstring.*")
        out.append("")
        out.append(f"**Reads:** {self._reads_list(self.reads)}")
        out.append("")
        out.append(f"**Read by:** {self._names_list(self.read_by)}")
        if self.source:
            out += ["", "```python", self.source, "```"]
        return "\n".join(out)


@dataclass
class ModelDoc:
    """A whole model, ready to write out."""

    name: str
    doc: str | None = None
    variables: tuple = ()
    graph: DependencyGraph | None = None
    trace_length: int | None = None
    settled: bool | None = None

    @property
    def coverage(self) -> float:
        """The share of variables carrying a docstring."""
        if not self.variables:
            return 1.0
        return sum(v.documented for v in self.variables) / len(self.variables)

    def undocumented(self) -> tuple:
        return tuple(v.name for v in self.variables if not v.documented)

    def to_markdown(self) -> str:
        out = [f"# {self.name}", ""]
        if self.doc:
            out += [inspect.cleandoc(self.doc), ""]
        documented = sum(v.documented for v in self.variables)
        out.append(f"{len(self.variables)} variables, {documented} documented "
                   f"({self.coverage:.0%}).")
        if self.trace_length is not None:
            settled = {True: "settled", False: "**not settled**",
                       None: "not checked"}[self.settled]
            out.append("")
            out.append(f"Dependency graph traced over {self.trace_length} "
                       f"periods; {settled}.")
        if self.graph is not None:
            out += ["", "## Evaluation order", "", "```",
                    self.graph.describe(), "```",
                    "", "## Dependency graph", "", "```mermaid",
                    self.graph.to_mermaid(), "```"]
        out += ["", "## Variables", ""]
        for variable in self.variables:
            out += [variable.to_markdown(), ""]
        return "\n".join(out).rstrip() + "\n"


def document(model_cls, graph: DependencyGraph | None = None, *,
             trace_length: int | None = None,
             settled: bool | None = None,
             include_source: bool = True) -> ModelDoc:
    """Build a :class:`ModelDoc` for a model class.

    ``graph`` is optional: without it the document still carries every
    docstring, assumption and formula, and simply says nothing about
    dependencies. That is the honest degenerate case — the docstrings are
    static and the graph is not.
    """
    order = (graph.order() if graph is not None
             else tuple(model_cls.var_names()))
    pooled = set(model_cls.pooled_names())
    variables = []
    for name in order:
        fn = getattr(model_cls, name, None)
        spec = getattr(fn, "__var_spec__", None)
        variables.append(VariableDoc(
            name=name,
            doc=spec.doc if spec else None,
            assumption=spec.assumption if spec else None,
            pooled=name in pooled,
            source=_source_of(fn) if include_source and fn else "",
            reads=tuple(sorted(graph.reads(name))) if graph else (),
            read_by=tuple(sorted(graph.read_by(name))) if graph else (),
        ))
    return ModelDoc(name=model_cls.__name__, doc=model_cls.__doc__,
                    variables=tuple(variables), graph=graph,
                    trace_length=trace_length, settled=settled)


def graph_is_settled(model_cls, mp, assumptions, *, short: int = 3,
                     long: int = 12, scenarios=None) -> bool:
    """Does a longer trace find the same graph as a shorter one?

    :meth:`Model.trace` defaults to three periods on the grounds that a
    ``@var`` may not branch on model-point data. It may branch on ``t``,
    and a variable that first reaches back six periods at ``t = 6`` is
    invisible to a three-period trace — so this is the check that the
    default was enough for *this* model, rather than the assumption that it
    was enough for every model.
    """
    if long <= short:
        raise ValueError(
            f"the long trace ({long}) must be longer than the short one "
            f"({short}) for the comparison to mean anything"
        )
    a = model_cls.trace(mp, assumptions, proj_len=short, scenarios=scenarios)
    b = model_cls.trace(mp, assumptions, proj_len=long, scenarios=scenarios)
    return a == b


def documented(model_cls) -> ModelDoc:
    """The static half only — every docstring, no graph, no trace.

    Enough to measure coverage across a library without constructing a
    model point for every template, which is what
    :func:`library_coverage` does.
    """
    return document(model_cls, include_source=False)


def library_coverage(*model_classes) -> dict:
    """Docstring coverage per model, and over the lot.

    Returns ``{name: (documented, total)}`` with a ``"TOTAL"`` entry. A
    number rather than a rule: the point of generating documentation is to
    find out how much of it there is.
    """
    out = {}
    total = covered = 0
    for cls in model_classes:
        doc = documented(cls)
        n = len(doc.variables)
        d = sum(v.documented for v in doc.variables)
        if n:
            out[cls.__name__] = (d, n)
            total += n
            covered += d
    out["TOTAL"] = (covered, total)
    return out
