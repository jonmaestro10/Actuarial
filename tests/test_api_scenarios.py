"""RFC-068 — the bound scenario set in the request schema.

Three things are under test here and they fail in different ways.

The **format** is ordinary schema work: three kinds, their refusals, and the
index-versus-return trap that :mod:`engine.data.esg` already knows about.

The **identity** is not. A scenario set has two of them — the values, which
:meth:`engine.data.scenarios.ScenarioSet.__fingerprint__` covers and the run
record cites, and the request, which :meth:`engine.api.store.RunStore.identify`
covers. For an explicit set those are the same question. For a generated one
the request holds a *recipe*, and NumPy makes no promise that ``default_rng``
gives the same stream in a future feature release. So the digest of the
generated set the worked examples use is pinned below: a NumPy upgrade that
moved the stream would otherwise revalue four templates and change nothing
that any test looks at.

The **equivalence class** is RFC-068's third one. A template that reads
``self.scenarios`` cannot be handed ``None``, so it runs under the stochastic
executor and under no other — which puts it outside both of RFC-061's
classes rather than in breach of either. The bridge that holds it is the
exact analogue of RFC-061's pool of one: a scenario run alone must reproduce
its column of the slab, bitwise.
"""

import math

import numpy as np
import pytest

from engine.api.catalogue import (
    SCENARIO_KINDS, SCENARIO_MEASURES, InvalidRequestError, build_assumptions,
    build_index_credit, build_run, build_scenarios, catalogue,
    index_credit_designs,
)
from engine.api.examples import EXAMPLES
from engine.core.fingerprint import fingerprint
from engine.core.registry import record_run
from engine.data.index_credit import (
    AnnualPointToPoint, IndexCredit, MonthlyAverage, MonthlySum,
)
from engine.data.scenarios import ScenarioSet

#: The templates whose worked example binds a scenario set. Listed rather
#: than discovered so that a template silently losing its scenarios shows up
#: as a red test here instead of as a shorter loop that still passes.
SCENARIO_BOUND = ("FamilyTakaful", "FixedIndexedAnnuity", "UnitLinkedGMDB",
                  "UnitLinkedGMxB", "VariablePayoutAnnuity")


# --- the three kinds -------------------------------------------------------


def test_a_flat_set_is_the_rate_it_names():
    built = build_scenarios(
        {"kind": "flat", "rate": 0.04, "n_scenarios": 3, "horizon": 5})
    assert built.returns.shape == (3, 5)
    assert np.array_equal(built.returns, np.full((3, 5), 0.04))
    assert built.primary == "return"


def test_an_explicit_set_is_the_numbers_it_carries():
    rows = [[0.05, -0.02, 0.11], [0.01, 0.03, -0.04]]
    built = build_scenarios({"kind": "explicit", "returns": rows})
    assert np.array_equal(built.returns, np.asarray(rows))
    assert (built.n_scenarios, built.horizon) == (2, 3)


def test_named_series_survive_and_the_primary_says_which_one_is_read():
    """`ScenarioSet` carries several series and templates read one of them.
    The schema has to keep both facts or it would flatten a multi-series ESG
    extract into whichever series happened to come first."""
    built = build_scenarios({
        "kind": "explicit",
        "series": {"equity": [[0.08, -0.03]], "bond": [[0.02, 0.02]]},
        "primary": "bond",
    })
    assert built.names == ("bond", "equity")
    assert np.array_equal(built.ret(0), [0.02])
    assert np.array_equal(built.with_primary("equity").ret(0), [0.08])


def test_a_generated_set_is_the_class_method_it_names():
    """The schema must not re-derive the generator. Same parameters, same
    stream — asserted against `ScenarioSet.lognormal` itself, so a builder
    that quietly rolled its own would diverge here rather than in a
    reconciliation somebody runs a month later."""
    spec = {"kind": "lognormal", "n_scenarios": 7, "horizon": 4,
            "drift": math.log(1.04), "vol": 0.2, "seed": 11}
    built = build_scenarios(spec)
    direct = ScenarioSet.lognormal(7, 4, drift=math.log(1.04), vol=0.2,
                                   seed=11)
    assert np.array_equal(built.returns, direct.returns)
    assert fingerprint(built) == fingerprint(direct)


# --- the two identities ----------------------------------------------------


#: The digest of the values ``engine.api.examples._lognormal(21)`` builds.
#: Recorded, not derived — the point is that nothing else in the suite looks
#: at these numbers. Regenerate it **only** on a deliberate decision that the
#: four scenario-bound specimens are allowed to move.
EXAMPLE_SCENARIO_DIGEST = "3c1be990acee4108ad587aca002fbc66"


def test_the_generated_specimen_set_has_not_moved():
    """A seed pins NumPy's stream *within* a version. NumPy's own policy
    freezes only the legacy `RandomState`; `default_rng` may change stream
    in a feature release. So the request digest of a `lognormal` scenario
    set identifies a recipe, and this asserts the **values** the recipe
    currently produces.

    Without it, a NumPy upgrade would revalue `UnitLinkedGMDB`,
    `UnitLinkedGMxB`, `FixedIndexedAnnuity`, `VariablePayoutAnnuity` and
    `FamilyTakaful` while every other test in the suite stayed green — the request is
    unchanged, the shapes are unchanged, and nothing else looks at the
    numbers."""
    built = build_scenarios(EXAMPLES["UnitLinkedGMDB"]["request"]["scenarios"])
    assert (built.n_scenarios, built.horizon) == (64, 21)
    assert fingerprint(built) == EXAMPLE_SCENARIO_DIGEST


def test_an_explicit_set_is_identified_by_its_numbers_and_a_recipe_is_not():
    """The distinction the module docstring turns on, asserted rather than
    described. Two explicit specs holding the same numbers written
    differently are the same set; two generated specs differing only in seed
    are different sets *and* different requests, which is the case the
    request digest gets right."""
    a = build_scenarios({"kind": "explicit", "returns": [[0.04, 0.04]]})
    b = build_scenarios({"kind": "explicit", "returns": [[4e-2, 0.0400]]})
    assert fingerprint(a) == fingerprint(b)

    base = {"kind": "lognormal", "n_scenarios": 4, "horizon": 3,
            "drift": 0.03, "vol": 0.15, "seed": 1}
    assert fingerprint(build_scenarios(base)) != fingerprint(
        build_scenarios({**base, "seed": 2}))


def test_a_flat_set_and_the_explicit_set_that_repeats_it_are_one_set():
    """Provenance is not identity — `ScenarioSet.__fingerprint__` covers the
    values and the primary name, and nothing else. Two requests that spell
    the same rectangle differently therefore build one set and would collide
    in the run registry, which is the behaviour the registry wants."""
    generated = build_scenarios(
        {"kind": "flat", "rate": 0.03, "n_scenarios": 2, "horizon": 3})
    spelled = build_scenarios(
        {"kind": "explicit", "returns": [[0.03] * 3] * 2})
    assert fingerprint(generated) == fingerprint(spelled)


# --- an index is not a return ----------------------------------------------


def test_an_index_is_converted_and_a_return_is_left_alone():
    """The silent catastrophe `engine.data.esg` exists to refuse. A
    cumulative index fed to a template that compounds it is a factor of a
    hundred, and it passes every validation `ScenarioSet` has — 100.0 is a
    perfectly legal return of 9,900%."""
    levels = [[105.0, 100.8, 110.88]]
    built = build_scenarios({"kind": "explicit", "returns": levels,
                             "values_are": "index", "index_base": 100.0})
    assert built.returns.tolist() == [pytest.approx([0.05, -0.04, 0.10])]
    # The same numbers read as returns are left exactly as written.
    plain = build_scenarios({"kind": "explicit", "returns": levels})
    assert np.array_equal(plain.returns, np.asarray(levels))


def test_an_index_without_a_base_is_refused_rather_than_guessed():
    """A generator publishing on 100.0 and one publishing on 1.0 give
    identical-looking files. `returns_from_index` refuses to pick; this
    asserts the refusal survives the trip through the schema instead of
    being caught and defaulted here."""
    with pytest.raises(InvalidRequestError, match="level at time zero"):
        build_scenarios({"kind": "explicit", "returns": [[105.0, 110.0]],
                         "values_are": "index"})


def test_a_period_zero_column_replaces_the_base_and_cannot_join_it():
    built = build_scenarios({"kind": "explicit",
                             "returns": [[100.0, 105.0, 100.8]],
                             "values_are": "index", "starts_at": 0})
    assert built.returns.tolist() == [pytest.approx([0.05, -0.04])]
    with pytest.raises(InvalidRequestError, match="redundant"):
        build_scenarios({"kind": "explicit", "returns": [[100.0, 105.0]],
                         "values_are": "index", "starts_at": 0,
                         "index_base": 100.0})


def test_an_index_base_on_returns_is_refused():
    """Not ignored. A caller who wrote `index_base` meant their numbers to
    be levels, and silently treating them as returns is precisely the
    hundredfold error."""
    with pytest.raises(InvalidRequestError, match="only apply to"):
        build_scenarios({"kind": "explicit", "returns": [[0.05]],
                         "index_base": 100.0})


# --- refusals --------------------------------------------------------------


def test_the_kind_has_no_default():
    """Unlike `assumptions.kind`, which defaults to `"scalar"` because every
    request written before it existed already meant that. No request ever
    carried a scenario set, so there is no prior meaning to preserve — and
    the three kinds are identified differently, so a default would pick the
    identity on the caller's behalf."""
    with pytest.raises(InvalidRequestError, match="scenarios.kind must be"):
        build_scenarios({"n_scenarios": 3, "horizon": 4, "rate": 0.04})
    with pytest.raises(InvalidRequestError, match="scenarios.kind must be"):
        build_scenarios({"kind": "gaussian", "n_scenarios": 3})
    assert list(SCENARIO_KINDS) == ["explicit", "flat", "lognormal"]
    assert list(SCENARIO_MEASURES) == ["return", "index"]


def test_a_ragged_set_is_refused_in_the_schemas_own_words():
    """`np.asarray` on a ragged list raises about inhomogeneous shapes and
    object dtypes, which describes NumPy rather than the request."""
    with pytest.raises(InvalidRequestError, match="same number of periods"):
        build_scenarios({"kind": "explicit",
                         "returns": [[0.01, 0.02], [0.03]]})
    with pytest.raises(InvalidRequestError, match="must be a number"):
        build_scenarios({"kind": "explicit", "returns": [["0.01"]]})
    with pytest.raises(InvalidRequestError, match="non-empty list"):
        build_scenarios({"kind": "explicit", "returns": []})


def test_named_series_without_a_primary_are_refused():
    with pytest.raises(InvalidRequestError, match="scenarios.primary"):
        build_scenarios({"kind": "explicit",
                         "series": {"equity": [[0.08]], "bond": [[0.02]]}})
    with pytest.raises(InvalidRequestError, match="exactly one of"):
        build_scenarios({"kind": "explicit", "returns": [[0.01]],
                         "series": {"equity": [[0.08]]}})


def test_a_generated_set_without_a_seed_is_refused():
    """A set that differs every time it is submitted would make the run
    registry report an engine that cannot repeat itself, which is the one
    thing the registry exists to detect."""
    spec = {"kind": "lognormal", "n_scenarios": 4, "horizon": 3,
            "drift": 0.03, "vol": 0.15}
    with pytest.raises(InvalidRequestError, match="seed"):
        build_scenarios(spec)
    with pytest.raises(InvalidRequestError, match="seed"):
        build_scenarios({**spec, "seed": -1})
    with pytest.raises(InvalidRequestError, match="seed"):
        build_scenarios({**spec, "seed": True})


def test_counts_must_be_positive_integers_and_unknown_fields_are_refused():
    base = {"kind": "flat", "rate": 0.04, "n_scenarios": 3, "horizon": 5}
    for field in ("n_scenarios", "horizon"):
        with pytest.raises(InvalidRequestError, match="positive integer"):
            build_scenarios({**base, field: 0})
        with pytest.raises(InvalidRequestError, match="positive integer"):
            build_scenarios({**base, field: 2.5})
    with pytest.raises(InvalidRequestError, match="unsupported"):
        build_scenarios({**base, "vol": 0.2})
    with pytest.raises(InvalidRequestError, match="must be an object"):
        build_scenarios([[0.04]])


def test_the_class_still_does_its_own_arguing():
    """A return at or below -100% is refused by `ScenarioSet`, not by a
    second opinion here. The message is the class's."""
    with pytest.raises(InvalidRequestError, match="-100%"):
        build_scenarios({"kind": "explicit", "returns": [[-1.0]]})
    with pytest.raises(InvalidRequestError, match="not among"):
        build_scenarios({"kind": "explicit", "series": {"a": [[0.1]]},
                         "primary": "b"})


# --- the scenario key and the executor -------------------------------------


def _gmdb_request(**over):
    request = {k: v for k, v in EXAMPLES["UnitLinkedGMDB"]["request"].items()}
    request.update(over)
    return request


def test_a_bound_set_chooses_the_stochastic_executor():
    built = build_run(_gmdb_request())
    assert built["scenarios"].n_scenarios == 64
    _, record = record_run(**built)
    assert record.executor == "stochastic"
    assert record.n_scenarios == 64 and record.scenario_horizon == 21


def test_the_executor_and_the_scenario_key_must_agree_at_the_boundary():
    """`record_run` refuses both of these too — but as a *failed run*,
    queued and then broken. They are plainly a malformed request, so they
    are a 422 before anything is queued."""
    with pytest.raises(InvalidRequestError, match="stochastic executor"):
        build_run(_gmdb_request(executor="vectorized"))
    with pytest.raises(InvalidRequestError, match="stochastic executor"):
        build_run(_gmdb_request(executor="interpreted"))
    request = dict(EXAMPLES["TermLife"]["request"], executor="stochastic")
    with pytest.raises(InvalidRequestError, match="needs a scenario set"):
        build_run(request)


def test_a_projection_longer_than_the_horizon_is_refused_before_it_runs():
    with pytest.raises(InvalidRequestError, match="shorter than proj_len"):
        build_run(_gmdb_request(proj_len=40))


def test_a_request_with_no_scenarios_is_unchanged_in_every_particular():
    """The RFC-066 pattern, and the reason it exists: asserted by
    **fingerprint** rather than by type. A key that appeared with a default
    would leave `scenarios=None` type-checking perfectly while the run it
    produced had quietly acquired a scenario axis."""
    built = build_run(EXAMPLES["TermLife"]["request"])
    assert built["scenarios"] is None
    _, record = record_run(**built)
    assert record.executor == "vectorized"
    assert record.scenarios_digest is None
    assert record.n_scenarios is None


# --- the crediting rule, which is a field ----------------------------------


def test_the_designs_are_discovered_and_the_base_class_is_not_one():
    """`IndexCredit` raises `NotImplementedError` from both of its methods,
    so a request naming it would build an object that fails at the first
    anniversary rather than at the boundary."""
    designs = index_credit_designs()
    assert designs == {"AnnualPointToPoint": AnnualPointToPoint,
                       "MonthlyAverage": MonthlyAverage,
                       "MonthlySum": MonthlySum}
    assert IndexCredit not in designs.values()
    with pytest.raises(InvalidRequestError, match="must be one of"):
        build_index_credit({"kind": "IndexCredit", "cap": 0.06})


def test_the_kind_is_the_name_the_class_already_publishes():
    """No second vocabulary. `IndexCredit.__fingerprint__` discriminates on
    the class name, so the request and the digest agree about what the thing
    is called."""
    for name, cls in index_credit_designs().items():
        built = build_index_credit({"kind": name, "cap": 0.06})
        assert isinstance(built, cls)
        assert built.__fingerprint__()["kind"] == name


def test_a_crediting_rule_arrives_on_the_assumptions_as_a_field():
    """A basis is a `kind`, a field is a field — RFC-066's rule, and an
    index-credit rule is the second field to take it."""
    built = build_assumptions(
        EXAMPLES["FixedIndexedAnnuity"]["request"]["assumptions"])
    assert isinstance(built.index_credit, AnnualPointToPoint)
    assert built.index_credit.cap == 0.06
    assert built.glwb_fee == 0.0105


def test_the_crediting_rule_makes_its_own_refusals():
    """A monthly design on an annual projection is a basis that cannot be
    run at all, and `Assumptions` says so at construction rather than at the
    first anniversary. The message is the class's, not the schema's."""
    spec = {"mortality": 0.01, "freq": 1,
            "index_credit": {"kind": "MonthlySum", "cap": 0.02}}
    with pytest.raises(InvalidRequestError, match="at least that fine"):
        build_assumptions(spec)
    with pytest.raises(InvalidRequestError, match="above the cap"):
        build_index_credit({"kind": "AnnualPointToPoint", "cap": 0.05,
                            "floor": 0.06})
    with pytest.raises(InvalidRequestError, match="required keyword-only"):
        build_index_credit({"kind": "MonthlySum"})
    with pytest.raises(InvalidRequestError, match="unsupported"):
        build_index_credit({"kind": "AnnualPointToPoint", "cap": 0.05,
                            "ratchet": True})


def test_assumptions_without_a_crediting_rule_fingerprint_as_they_did():
    """The same guard the `kind` default gets, for the same reason: a
    request that omitted the field and quietly got a different basis than it
    did last week would be a silent revaluation."""
    spec = {"mortality": 0.01, "lapse": 0.05, "interest": 0.035}
    plain = build_assumptions(spec)
    assert plain.index_credit is None
    with_rule = build_assumptions(
        {**spec, "index_credit": {"kind": "AnnualPointToPoint", "cap": 0.06}})
    assert fingerprint(plain) != fingerprint(with_rule)
    # And omitting it is exactly the same object as never having had it.
    from engine.data.assumptions import Assumptions, MortalityTable

    assert fingerprint(plain) == fingerprint(Assumptions(
        mortality=MortalityTable.flat(0.01), lapse=0.05, interest=0.035))


# --- the third equivalence class -------------------------------------------


@pytest.mark.parametrize("name", SCENARIO_BOUND)
def test_a_scenario_bound_template_runs_only_under_the_stochastic_executor(
        name):
    """Outside RFC-061's two classes rather than in breach of either. A
    template reading `self.scenarios` cannot be handed `None`, and both
    deterministic executors hand it exactly that — so the refusal is a
    property of the product, the same way pooling is."""
    built = build_run(EXAMPLES[name]["request"])
    assert built["scenarios"] is not None
    for executor in ("vectorized", "interpreted"):
        with pytest.raises(Exception):
            record_run(**{**built, "executor": executor})


@pytest.mark.parametrize("name", SCENARIO_BOUND)
def test_one_scenario_alone_is_its_column_of_the_slab(name):
    """RFC-068's bridge, and the exact analogue of RFC-061's pool of one.

    The stochastic executor evaluates a `(model point x scenario)` slab. If
    that slab is what it claims to be, scenario `s` run on its own is column
    `s` of it, bitwise — so a template that let one scenario see another
    would break here and nowhere else. `VariablePayoutAnnuity` and
    `FamilyTakaful` are the ones that make the claim non-trivial: both
    reduce across the *model-point* axis every period, and the assertion is
    that the reduction does not reach across the scenario axis as well.

    Every scenario is checked here. The evidence pack samples eight and
    reports how many, because it runs on every build."""
    built = build_run(EXAMPLES[name]["request"])
    scenarios = built["scenarios"]
    slab, _ = record_run(**built)
    names = built["outputs"]
    for s in range(scenarios.n_scenarios):
        alone, _ = record_run(**{**built, "scenarios": scenarios.single(s)})
        for var in names:
            assert np.array_equal(
                np.asarray(slab.array(var))[:, :, s],
                np.asarray(alone.array(var))[:, :, 0],
            ), f"{name} scenario {s} var {var}"


@pytest.mark.parametrize("name", SCENARIO_BOUND)
def test_a_scenario_bound_specimen_repeats_itself(name):
    """The weaker claim the class can still support, and the one the run
    registry is built to detect the absence of."""
    built = build_run(EXAMPLES[name]["request"], catalogue())
    first = record_run(**built)[1]
    second = record_run(**built)[1]
    assert first.run_id == second.run_id
    assert first.results_digest == second.results_digest


def test_the_pack_reports_the_third_class_rather_than_erroring():
    """Before RFC-068 the evidence pack ran every specimen under the two
    deterministic executors; a scenario-bound one would have landed in the
    pack as `error: ValueError`, which reads as a broken engine rather than
    as a template outside the class. The section has to make the claim it
    can support instead."""
    from engine.report.evidence import executor_equivalence

    section = executor_equivalence()
    rows = {row["template"]: row for row in section.content["templates"]}
    assert section.content["n_scenario_bound"] == len(SCENARIO_BOUND)
    for name in SCENARIO_BOUND:
        row = rows[name]
        assert row["error"] is None
        assert row["in_equivalence_class"] is False
        assert "scenario set" in row["excluded_because"]
        assert row["repeats_deterministically"] is True
        bridge = row["bitwise_on_one_scenario"]
        assert bridge["bitwise"] is True
        # The sample is reported, not implied. A bounded check that reads as
        # an exhaustive one is the overclaim the pack exists to avoid.
        assert 0 < bridge["scenarios_checked"] <= bridge["of_scenarios"]
        assert bridge["of_scenarios"] == row["n_scenarios"]
