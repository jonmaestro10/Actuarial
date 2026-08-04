"""The variable dependency graph.

PLAN §2.1 says the engine resolves calculation order from the dependency
graph, §4.2 wants that graph traced and topologically sorted before anything
can be fused into a kernel, and §7 wants every number traceable to the
formula that produced it. The engine had been getting by without the graph
as an object: evaluation order emerged from recursive calls into a memo,
which works but leaves it implicit.

Three things this file pins:

**A cycle is caught, named, and cheap to find.** A variable that reads
itself within a period used to exhaust the Python stack a thousand frames
later, with a traceback about recursion rather than about the mistake.

**A cross-period self-reference is not a cycle.** ``pols_if`` reading
``pols_if(t-1)`` is what a projection *is*. The distinction is the time
offset on the edge, and getting it wrong either way would make the graph
useless — rejecting every template, or accepting a model that cannot be
evaluated.

**Tracing changes no number.** The graph is recorded by running the model,
so the recording must not perturb what it records. Asserted bitwise.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from engine.core.graph import CyclicModelError, DependencyGraph
from engine.core.model import Model, var
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.fixed_annuity import FixedAnnuity
from engine.library.term_life import TermLife
from engine.library.unit_linked import UnitLinkedGMDB, UnitLinkedGMxB

POINT = ModelPoint(id="T1", age_at_entry=40, term_years=20,
                   sum_assured=250_000.0, annual_premium=1_200.0, init_pols=1)
ASSUMPTIONS = Assumptions(mortality=MortalityTable.flat(0.01), lapse=0.05,
                          interest=0.03, expense_per_policy=50.0)


# --- cycles --------------------------------------------------------------


class SelfReferential(Model):
    @var
    def a(self, t):
        return self.a(t) + 1.0


class MutuallyReferential(Model):
    @var
    def a(self, t):
        return self.b(t) * 2.0

    @var
    def b(self, t):
        return self.a(t) + 1.0


class ThroughAHelper(Model):
    """The cycle a static source scan would miss: the loop closes inside a
    plain method, not inside a ``@var`` body."""

    def _helper(self, t):
        return self.a(t) * 3.0

    @var
    def a(self, t):
        return self._helper(t)


class LooksRecursiveButIsNot(Model):
    @var
    def a(self, t):
        if t == 0:
            return 1.0
        return self.a(t - 1) * 0.9


@pytest.mark.parametrize("cls", [SelfReferential, MutuallyReferential,
                                 ThroughAHelper])
def test_a_same_period_cycle_is_caught_and_named(cls):
    model = cls(POINT, ASSUMPTIONS, 3)
    with pytest.raises(CyclicModelError) as exc:
        model.a(1)
    message = str(exc.value)
    assert "depends on itself within one period" in message
    assert "a(1)" in message
    # The chain, not just the name.
    assert "->" in message


def test_a_cycle_is_caught_at_depth_two_not_by_the_recursion_limit():
    """The point of detecting it at all. A ``RecursionError`` a thousand
    frames deep names the recursion; this names the mistake."""
    import sys

    model = MutuallyReferential(POINT, ASSUMPTIONS, 3)
    before = sys.getrecursionlimit()
    sys.setrecursionlimit(60)          # far below what a blown stack needs
    try:
        with pytest.raises(CyclicModelError):
            model.a(0)
    finally:
        sys.setrecursionlimit(before)


def test_reading_an_earlier_period_of_yourself_is_not_a_cycle():
    """It is what a projection is."""
    model = LooksRecursiveButIsNot(POINT, ASSUMPTIONS, 5)
    assert model.a(3) == pytest.approx(0.9 ** 3)
    graph = LooksRecursiveButIsNot.trace(POINT, ASSUMPTIONS)
    assert graph.reads("a") == (("a", -1),)
    assert graph.reads("a", offset=0) == ()
    assert graph.order() == ("a",)


def test_a_hand_built_cyclic_graph_refuses_to_order():
    graph = DependencyGraph({"a": {("b", 0)}, "b": {("a", 0)}})
    with pytest.raises(CyclicModelError, match="same-period dependency cycle"):
        graph.order()


# --- what the templates actually look like -------------------------------


def term_graph() -> DependencyGraph:
    return TermLife.trace(POINT, ASSUMPTIONS)


def test_the_traced_graph_is_the_model_as_written():
    graph = term_graph()
    assert graph.reads("claims", offset=0) == ("pols_death",)
    assert graph.reads("net_claims", offset=0) == ("claims", "reinsurance_recovery")
    assert graph.reads("profit_after_tax", offset=0) == (
        "profit_before_tax", "tax"
    )
    # The recursion, at the right offset.
    assert ("pols_if", -1) in graph.reads("pols_if")
    assert "pols_if" not in graph.reads("pols_if", offset=0)


def test_the_graph_sees_through_helper_methods():
    """``pols_if`` calls ``self._survivors(t-1)``, which calls
    ``self._decrements(t)``, which is where ``q_x`` is actually read. A
    static scan of the ``pols_if`` body sees a helper and nothing else."""
    graph = term_graph()
    assert ("q_x", -1) in graph.reads("pols_if")
    assert ("lapse_rate", -1) in graph.reads("pols_if")
    assert graph.reads("pols_death", offset=0) == (
        "lapse_rate", "pols_if", "q_x"
    )


@pytest.mark.parametrize("cls,scenarios", [
    (TermLife, None),
    (FixedAnnuity, None),
    (UnitLinkedGMDB, ScenarioSet.flat(0.05, 1, 10)),
    (UnitLinkedGMxB, ScenarioSet.flat(0.05, 1, 10)),
])
def test_every_template_is_acyclic_and_looks_back_one_period(cls, scenarios):
    point = ModelPoint(
        id="X", age_at_entry=45, term_years=15, defer_years=5,
        sum_assured=250_000.0, annual_premium=1_200.0, premium=100_000.0,
        annual_payment=9_000.0, init_pols=1, gmdb_guarantee=100_000.0,
        gmab_guarantee=110_000.0, gmwb_base=100_000.0, gmwb_rate=0.05,
        gmwb_ratchet=1.0,
    )
    graph = cls.trace(point, ASSUMPTIONS, scenarios=scenarios)
    order = graph.order()
    assert set(order) == set(graph.variables)
    assert graph.horizon() == 1, "a wider window would change what a compiled loop must keep alive"


@pytest.mark.parametrize("cls,scenarios", [
    (TermLife, None),
    (UnitLinkedGMxB, ScenarioSet.flat(0.05, 1, 10)),
])
def test_the_order_puts_every_same_period_dependency_first(cls, scenarios):
    """The property a compiler emitting a forward loop over ``t`` needs, and
    the only thing ``order`` promises."""
    point = ModelPoint(
        id="X", age_at_entry=45, term_years=15, sum_assured=250_000.0,
        annual_premium=1_200.0, premium=100_000.0, init_pols=1,
        gmdb_guarantee=100_000.0, gmab_guarantee=110_000.0,
        gmwb_base=100_000.0, gmwb_rate=0.05, gmwb_ratchet=1.0,
    )
    graph = cls.trace(point, ASSUMPTIONS, scenarios=scenarios)
    order = graph.order()
    position = {name: i for i, name in enumerate(order)}
    for name in order:
        for dep in graph.reads(name, offset=0):
            assert position[dep] < position[name], f"{dep} after {name}"


def test_the_order_is_deterministic():
    """A compilation step that reordered itself between runs would defeat
    the reproducibility guarantee in RFC-003 before it started."""
    first, second = term_graph().order(), term_graph().order()
    assert first == second
    assert first == tuple(first)          # a tuple, not a mutable list


def test_the_trace_length_does_not_change_the_graph():
    """Three periods is enough: it exercises both a variable's ``t == 0``
    branch and its recursive one, and a ``@var`` body may not branch on
    model-point data."""
    assert TermLife.trace(POINT, ASSUMPTIONS, proj_len=3) == TermLife.trace(
        POINT, ASSUMPTIONS, proj_len=9
    )


# --- lineage -------------------------------------------------------------


def test_lineage_runs_both_ways_and_transitively():
    graph = term_graph()
    upstream = graph.inputs_of("claims")
    assert "pols_death" in upstream          # direct
    assert "q_x" in upstream                 # through pols_death
    assert "age" in upstream                 # through q_x
    assert "claims" not in upstream          # not its own input

    downstream = graph.affected_by("q_x")
    assert "claims" in downstream
    assert "profit_after_tax" in downstream  # the far end of the model
    assert "q_x" not in downstream


def test_lineage_answers_the_question_a_reviewer_asks():
    """"I am about to change the mortality basis — what moves?" Everything
    downstream of ``q_x``, which on this template is most of the model."""
    graph = term_graph()
    moved = set(graph.affected_by("q_x"))
    assert "premiums" in moved               # through pols_if
    assert "v" not in moved                  # discounting is not
    assert "in_term" not in moved            # nor is the term indicator


def test_leaves_and_roots():
    graph = term_graph()
    assert "in_term" in graph.leaves()       # reads nothing
    assert "v" in graph.leaves()
    assert "profit_after_tax" in graph.roots()   # nothing reads it
    assert "pols_if" not in graph.roots()


def test_an_unknown_variable_lists_the_ones_that_exist():
    with pytest.raises(KeyError, match="no variable 'nope'"):
        term_graph().reads("nope")


# --- rendering -----------------------------------------------------------


def test_mermaid_marks_cross_period_edges_as_such():
    text = term_graph().to_mermaid()
    assert text.startswith("graph TD")
    assert "pols_death --> claims" in text
    assert "pols_if -. t-1 .-> pols_if" in text


def test_describe_lists_variables_in_evaluation_order():
    lines = term_graph().describe().splitlines()
    names = [line.split(" <- ")[0].replace(" (pooled)", "") for line in lines]
    assert names == list(term_graph().order())
    assert any("[t-1]" in line for line in lines)


def test_a_pooled_variable_is_marked():
    from engine.data.basis import ValuationBasis
    from engine.data.mortality import MortalityBasis
    from engine.data.rates import YieldCurve
    from engine.library.variable_payout_annuity import VariablePayoutAnnuity

    basis = ValuationBasis(
        mortality=MortalityBasis({"M": {a: min(0.0005 * 1.1 ** (a - 20), 1.0)
                                        for a in range(20, 121)}},
                                 year_start=2020, use_improvement=False),
        curve=YieldCurve([0.03], freq=1),
    )
    point = ModelPoint(id="M1", dob=dt.date(1955, 1, 1), sex="M",
                       valuation=dt.date(2021, 1, 1), pension=1_200.0,
                       account_value=15_000.0, init_lives=1)
    graph = VariablePayoutAnnuity.trace(
        point, basis, proj_len=3, scenarios=ScenarioSet.flat(0.03, 1, 10)
    )
    assert "adjustment" in graph.pooled
    assert '(["adjustment"])' in graph.to_mermaid()
    assert " (pooled)" in graph.describe()
    graph.order()                              # still acyclic with a pool in it


# --- recording changes nothing -------------------------------------------


def test_recording_the_graph_does_not_perturb_what_it_records():
    """The graph is built by running the model, so the recording has to be
    invisible to the numbers. Bitwise, not to tolerance."""
    points = from_dicts([
        {"id": f"T{i}", "age_at_entry": 35 + 5 * i, "term_years": 20,
         "sum_assured": 250_000.0 * (i + 1), "annual_premium": 1_200.0,
         "init_pols": 1}
        for i in range(4)
    ])
    outputs = ["pols_if", "claims", "premiums", "expenses", "profit_before_tax"]
    plain = run_vectorized(TermLife, points, ASSUMPTIONS, 25, outputs=outputs)
    traced = {}
    for mp in points:
        model = TermLife(mp, ASSUMPTIONS, 25, record_graph=True)
        for name in outputs:
            traced.setdefault(name, []).append(model.series(name))
    for name in outputs:
        assert np.array_equal(
            np.asarray(plain.array(name)),
            np.array(traced[name]).T.reshape(26, 4),
        ), name


def test_asking_for_a_graph_that_was_never_recorded_says_how_to_get_one():
    model = TermLife(POINT, ASSUMPTIONS, 5)
    model.series("claims")
    with pytest.raises(RuntimeError, match="TermLife.trace"):
        model.graph()
    assert TermLife(POINT, ASSUMPTIONS, 5, record_graph=True).graph() is not None


def test_the_graph_records_only_what_ran():
    """It is a trace, not a declaration. Asking for one variable records
    that variable's cone and nothing else — which is also what makes it a
    usable answer to "what does this output depend on"."""
    graph = TermLife.trace(POINT, ASSUMPTIONS, names=["claims"])
    assert graph.reads("claims", offset=0) == ("pols_death",)
    assert "tax" not in graph.variables
    assert "v" not in graph.variables
