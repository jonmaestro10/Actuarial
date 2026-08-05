"""Auto-generated model documentation — RFC-030.

PLAN §7's "auto-generated model documentation from ``@var`` docstrings +
dependency graph visualizer". The graph half was RFC-001's; this pins the
other half, and the trap that comes with generating documentation from a
graph that has to be *run* rather than parsed.
"""

import importlib
import inspect
import pkgutil

import pytest

import engine.library as library
from engine.core.model import Model, var
from engine.core.modeldoc import (
    ModelDoc, VariableDoc, document, documented, graph_is_settled,
    library_coverage,
)
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint
from engine.library.term_life import TermLife

ASSUMPTIONS = Assumptions(mortality=MortalityTable.flat(0.01), lapse=0.05,
                          interest=0.03)
POINT = ModelPoint(id="T1", age_at_entry=40, term_years=20,
                   sum_assured=250_000.0, annual_premium=1_200.0, init_pols=1)


def _template_classes():
    """Every ``Model`` subclass the library ships, found by walking it."""
    found = []
    for module_info in pkgutil.iter_modules(library.__path__):
        module = importlib.import_module(f"engine.library.{module_info.name}")
        for cls in vars(module).values():
            if (inspect.isclass(cls) and issubclass(cls, Model)
                    and cls is not Model and cls.__module__ == module.__name__
                    and cls.var_names()):
                found.append(cls)
    return found


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------

def test_a_document_carries_the_docstring_the_assumption_and_the_formula():
    doc = document(TermLife, TermLife.trace(POINT, ASSUMPTIONS))
    by_name = {v.name: v for v in doc.variables}
    claims = by_name["claims"]
    assert claims.documented
    assert "Death claims" in claims.doc
    assert "def claims" in claims.source
    assert by_name["commission_clawback"].assumption == "commission"
    # And the model's own docstring heads the document.
    assert doc.name == "TermLife"
    assert doc.doc is TermLife.__doc__


def test_variables_appear_in_evaluation_order():
    graph = TermLife.trace(POINT, ASSUMPTIONS)
    doc = document(TermLife, graph)
    assert tuple(v.name for v in doc.variables) == graph.order()


def test_the_document_states_what_reads_what_in_both_directions():
    graph = TermLife.trace(POINT, ASSUMPTIONS)
    doc = document(TermLife, graph)
    by_name = {v.name: v for v in doc.variables}
    assert by_name["claims"].reads == tuple(sorted(graph.reads("claims")))
    assert by_name["claims"].read_by == tuple(sorted(graph.read_by("claims")))
    rendered = by_name["claims"].to_markdown()
    assert "**Reads:**" in rendered and "**Read by:**" in rendered


def test_a_cross_period_edge_keeps_its_offset_in_the_rendering():
    """Flattening ``pols_if [t-1]`` to ``pols_if`` would lose exactly the
    thing a formula browser exists to reveal — the recursion a projection is
    built on is invisible in the source of any single variable."""
    graph = TermLife.trace(POINT, ASSUMPTIONS)
    doc = document(TermLife, graph)
    pols_if = next(v for v in doc.variables if v.name == "pols_if")
    assert pols_if.recursive
    assert ("pols_if", -1) in pols_if.reads
    assert "[t-1]" in pols_if.to_markdown()


def test_the_markdown_carries_a_mermaid_diagram_and_the_order():
    doc = document(TermLife, TermLife.trace(POINT, ASSUMPTIONS))
    rendered = doc.to_markdown()
    assert rendered.startswith("# TermLife")
    assert "```mermaid" in rendered
    assert "graph TD" in rendered
    assert "## Evaluation order" in rendered
    assert "## Variables" in rendered
    assert rendered.endswith("\n")


def test_a_document_without_a_graph_still_carries_every_formula():
    """The honest degenerate case: the docstrings are static and the graph
    is not, so a document can be built without running anything — it simply
    says nothing about dependencies."""
    doc = document(TermLife)
    assert len(doc.variables) == len(TermLife.var_names())
    assert all(v.reads == () and v.read_by == () for v in doc.variables)
    assert "```mermaid" not in doc.to_markdown()
    assert doc.coverage > 0.9


def test_an_undocumented_variable_says_so_rather_than_being_omitted():
    class Bare(Model):
        @var
        def thing(self, t):
            return 1.0

    doc = document(Bare)
    assert doc.undocumented() == ("thing",)
    assert doc.coverage == 0.0
    assert "*No docstring.*" in doc.to_markdown()


def test_a_pooled_variable_is_marked_as_one():
    from engine.library.variable_payout_annuity import VariablePayoutAnnuity

    doc = documented(VariablePayoutAnnuity)
    pooled = [v for v in doc.variables if v.pooled]
    assert pooled
    assert "*(pooled)*" in pooled[0].to_markdown()


def test_a_model_with_no_variables_is_fully_covered_vacuously():
    assert ModelDoc(name="Empty").coverage == 1.0
    assert VariableDoc(name="x").documented is False


# --------------------------------------------------------------------------
# The finding: a short trace documents a recursion as a constant
# --------------------------------------------------------------------------

class ReachesBackLater(Model):
    """Legal, and a trap. A ``@var`` may branch on ``t``, and ``t`` is not
    model-point data, so a variable can start reaching back long after a
    short trace has stopped looking."""

    @var
    def slow(self, t):
        """One until period six, then whatever it was six periods ago."""
        return 1.0 if t < 6 else self.slow(t - 6)


def test_a_three_period_trace_can_miss_every_edge_a_variable_has():
    """:meth:`Model.trace` defaults to three periods and its docstring says
    "a longer trace cannot find new edges in a well-formed model". That is
    true of every template in the library and **false in general**.

    Measured: at three periods this variable is reported with **no
    dependencies at all**. It reads itself six periods back. A document
    generated from that trace describes a recursion as a constant, and
    nothing raises.
    """
    short = ReachesBackLater.trace(POINT, ASSUMPTIONS, proj_len=3)
    long = ReachesBackLater.trace(POINT, ASSUMPTIONS, proj_len=12)
    assert short.reads("slow") == ()
    assert short.horizon() == 0
    assert long.reads("slow") == (("slow", -6),)
    assert long.horizon() == 6

    documented_short = document(ReachesBackLater, short).variables[0]
    documented_long = document(ReachesBackLater, long).variables[0]
    assert not documented_short.recursive
    assert documented_long.recursive


def test_the_edge_appears_exactly_when_the_trace_reaches_the_branch():
    """Not gradually — the edge is invisible at five periods and complete at
    six, because that is the period the branch first fires in."""
    seen = {n: ReachesBackLater.trace(POINT, ASSUMPTIONS,
                                      proj_len=n).reads("slow")
            for n in (3, 5, 6, 7, 20)}
    assert seen[3] == seen[5] == ()
    assert seen[6] == seen[7] == seen[20] == (("slow", -6),)


def test_the_settled_check_catches_it_and_the_document_reports_it():
    """Which is the point of generating the document rather than writing
    it: the generator can say how far it looked, and whether that was
    enough."""
    assert graph_is_settled(TermLife, POINT, ASSUMPTIONS)
    assert not graph_is_settled(ReachesBackLater, POINT, ASSUMPTIONS)
    unsettled = document(
        ReachesBackLater, ReachesBackLater.trace(POINT, ASSUMPTIONS),
        trace_length=3, settled=False)
    rendered = unsettled.to_markdown()
    assert "traced over 3 periods" in rendered
    assert "**not settled**" in rendered
    settled = document(TermLife, TermLife.trace(POINT, ASSUMPTIONS),
                       trace_length=3, settled=True)
    assert "periods; settled." in settled.to_markdown()


def test_a_longer_trace_has_to_be_longer():
    with pytest.raises(ValueError, match="must be longer"):
        graph_is_settled(TermLife, POINT, ASSUMPTIONS, short=5, long=5)


def test_every_template_that_traces_from_a_common_point_settles():
    """The claim :meth:`Model.trace` makes, checked against the library it
    is made about.

    Only the five templates whose model points need nothing product-specific
    can be traced from one generic point, so that is what this covers and
    that is what it is called. ``tests/test_windowed.py`` makes the same
    check under a heading that says "every template" and then tests one —
    naming the scope is the difference between evidence and a claim.
    """
    checked = []
    for cls in _template_classes():
        try:
            settled = graph_is_settled(cls, POINT, ASSUMPTIONS)
        except AttributeError:
            continue  # needs a product-specific model point
        checked.append(cls.__name__)
        assert settled, cls.__name__
    assert len(checked) >= 5
    assert "TermLife" in checked and "WithProfitsEndowment" in checked


# --------------------------------------------------------------------------
# The finding: coverage is a number, and it has a tail
# --------------------------------------------------------------------------

def test_library_coverage_is_measured_rather_than_required():
    """Generating documentation makes the gap visible for the first time.
    Across every template the library ships, 80.3% of variables carry a
    docstring — and the shortfall is concentrated, not spread: the
    best-covered nine templates are at 100% and the worst at 50%.

    Asserted as a floor rather than an equality so that adding a template
    does not break the suite, and so that the number can only go up.
    """
    coverage = library_coverage(*_template_classes())
    covered, total = coverage["TOTAL"]
    assert total > 250
    assert covered / total >= 0.80
    per_model = {k: d / n for k, (d, n) in coverage.items() if k != "TOTAL"}
    assert max(per_model.values()) == 1.0
    assert min(per_model.values()) >= 0.5
    # Nine templates are complete.
    assert sum(1 for share in per_model.values() if share == 1.0) >= 6


def test_coverage_counts_the_variables_a_model_actually_has():
    class Half(Model):
        @var
        def documented_one(self, t):
            """Has a docstring."""
            return 1.0

        @var
        def bare_one(self, t):
            return 2.0

    assert library_coverage(Half) == {"Half": (1, 2), "TOTAL": (1, 2)}
    assert documented(Half).coverage == 0.5


def test_a_model_with_no_variables_is_left_out_of_the_totals():
    class Nothing(Model):
        pass

    assert library_coverage(Nothing) == {"TOTAL": (0, 0)}
