"""The scaffold: a starting point that tells the truth about what it guessed.

RFC-036. The scaffold is allowed to guess where RFC-034's reader is not,
because its guesses land in source a human must edit before anything
computes — so what these tests hold it to is not accuracy but *disclosure*:

- every input variable appears in the mapping table, with its confidence;
- every stub raises, so a half-ported model cannot quietly produce numbers;
- a name the library has nothing for is listed as such rather than matched
  to whatever scored highest;
- illegal and colliding names are renamed, and the rename is reported.

The acceptance test at the end is the one A4 asks for: the emitted parity
spec, over the emitted mapping, reconciles a run against the RFC-034 fixture
extract — with the scaffold's own suggestions supplying which engine variable
each stub stands for.
"""

import importlib.util
from pathlib import Path

import pytest

from engine.core.model import Model
from engine.core.registry import record_run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.library.term_life import TermLife
from engine.migrate import (
    read_modelpoints,
    read_results,
    scaffold,
    scaffold_from_results,
    suggest,
)
from engine.migrate.scaffold import WEAK, identifier, library_variables
from engine.parity import Tolerance, TolerancePolicy, diff

FIXTURES = Path(__file__).parent / "fixtures" / "prophet"
MPF = FIXTURES / "term_life.pro"
RESULTS = FIXTURES / "term_life_results.csv"

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                          interest=0.03, expense_per_policy=50.0)
PROJ_LEN = 25


def load(source: str, path: Path, name: str = "scaffolded"):
    """Import generated source as a module, the way a user would."""
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_scaffold():
    return scaffold_from_results(
        RESULTS, class_name="ConvertedTermLife",
        modelpoints=read_modelpoints(MPF),
        id_column="POL_NUMBER", time_column="T",
    )


# --------------------------------------------------------------------------
# What comes out is a module
# --------------------------------------------------------------------------

def test_the_scaffold_imports_and_subclasses_model(fixture_scaffold, tmp_path):
    module = load(fixture_scaffold.source, tmp_path / "converted.py")
    cls = module.ConvertedTermLife
    assert issubclass(cls, Model)
    assert set(cls.var_names()) == {"pols_if", "deaths", "claims", "premiums"}
    assert module.ID_COLUMN == "POL_NUMBER"
    assert module.TIME_COLUMN == "T"


def test_every_stub_raises_until_somebody_ports_it(fixture_scaffold, tmp_path):
    """A stub that returned zero would let a half-converted model produce a
    plausible, wrong reconciliation."""
    module = load(fixture_scaffold.source, tmp_path / "converted.py")
    instance = module.ConvertedTermLife.__new__(module.ConvertedTermLife)
    with pytest.raises(NotImplementedError, match="POLS_IF"):
        module.ConvertedTermLife.pols_if(instance, 0)


def test_the_mapping_table_covers_every_input_variable(fixture_scaffold):
    table = read_results(RESULTS)
    variables = [n for n in table.names if n not in ("POL_NUMBER", "T")]
    assert {s.external for s in fixture_scaffold.suggestions} == set(variables)
    assert set(fixture_scaffold.mapping) == set(variables)
    markdown = fixture_scaffold.to_markdown()
    for name in variables:
        assert f"`{name}`" in markdown
    assert "every one a stub that raises" in markdown


def test_the_key_columns_never_become_variables(fixture_scaffold):
    """A ``@var`` called ``t`` would be a model that cannot run."""
    assert "t" not in fixture_scaffold.mapping.values()
    assert "POL_NUMBER" not in fixture_scaffold.mapping


def test_the_model_point_fields_the_client_actually_has_are_recorded(
        fixture_scaffold):
    assert "age_at_entry" in fixture_scaffold.modelpoint_fields
    assert "sum_assured" in fixture_scaffold.modelpoint_fields
    assert "age_at_entry" in fixture_scaffold.source


def test_the_same_inputs_scaffold_the_same_source(fixture_scaffold):
    again = scaffold_from_results(
        RESULTS, class_name="ConvertedTermLife",
        modelpoints=read_modelpoints(MPF),
        id_column="POL_NUMBER", time_column="T",
    )
    assert again.source == fixture_scaffold.source


# --------------------------------------------------------------------------
# What it says about its guesses
# --------------------------------------------------------------------------

def test_an_alias_beats_string_similarity(fixture_scaffold):
    """``DEATHS`` and ``pols_death`` share almost no characters, and
    ``DEATHS`` against ``pols_lapse`` looks better than it is."""
    suggested, templates, score, confidence = suggest("DEATHS")
    assert suggested == "pols_death"
    assert confidence == "exact"
    assert templates and score == 1.0
    by_name = {s.external: s for s in fixture_scaffold.suggestions}
    assert by_name["DEATHS"].suggested == "pols_death"


def test_a_name_the_library_has_nothing_for_is_listed_not_matched():
    """The sentinel has to stay alien, and the library keeps growing.

    This test used to pin ``HOUSE_CODE_XQ``, which matched nothing until
    ``LongTermCare`` arrived with a ``home_care`` variable — at which point
    the stub scored 0.526 against a ``WEAK`` threshold of 0.5 and the
    "matches nothing" case quietly stopped being one. The machinery was
    right; the name had simply stopped being unmatched.

    So the margin is now asserted rather than assumed. A future template
    that drags this score toward the threshold fails *here*, saying the
    sentinel needs replacing, instead of silently turning this into a test
    about weak matches.
    """
    alien = "XQJ_ZKVW"
    made_up = scaffold([alien, "POLS_IF"])
    by_name = {s.external: s for s in made_up.suggestions}
    assert by_name[alien].suggested is None
    assert by_name[alien].confidence == "none"
    assert by_name[alien].score < WEAK - 0.15, (
        f"{alien} now scores {by_name[alien].score:.3f} against a WEAK "
        f"threshold of {WEAK}; the library has grown a variable it "
        f"resembles, so this test needs a name that is still alien"
    )
    assert made_up.unmapped == (alien,)
    assert "no suggestion" in made_up.to_markdown()
    assert "Nothing in the library resembles this name" in made_up.source


def test_a_weak_match_is_labelled_weak_and_sorted_to_the_top():
    """The reviewer's attention belongs on the guesses, so the table leads
    with the ones nobody should trust."""
    made_up = scaffold(["WIDGET_FACTOR", "POLS_IF"])
    by_name = {s.external: s for s in made_up.suggestions}
    assert by_name["WIDGET_FACTOR"].confidence in ("weak", "none")
    assert by_name["WIDGET_FACTOR"].needs_review
    assert not by_name["POLS_IF"].needs_review
    rows = [line for line in made_up.to_markdown().splitlines()
            if line.startswith("| `")]
    assert "WIDGET_FACTOR" in rows[0]
    assert "POLS_IF" in rows[-1]


def test_illegal_and_colliding_names_are_renamed_and_reported(tmp_path):
    made_up = scaffold(["SPECIAL RIDER (2)", "1ST_YEAR", "trace", "POLS_IF",
                        "POLS IF"])
    stubs = {s.external: s.stub for s in made_up.suggestions}
    assert stubs["SPECIAL RIDER (2)"] == "special_rider_2"
    assert stubs["1ST_YEAR"] == "v_1st_year"
    assert stubs["trace"] == "trace_"          # Model.trace already exists
    assert stubs["POLS IF"] == "pols_if_2"     # and this one collides
    module = load(made_up.source, tmp_path / "odd.py", name="odd")
    assert set(module.ConvertedModel.var_names()) == set(stubs.values())
    assert module.MAPPING["SPECIAL RIDER (2)"] == "special_rider_2"


def test_identifier_handles_the_degenerate_name():
    assert identifier("!!!") == "variable"
    assert identifier("class") == "class_"


def test_the_variable_catalogue_is_read_off_the_library():
    catalogue = library_variables()
    assert "pols_if" in catalogue
    assert "TermLife" in catalogue["pols_if"]
    assert "claims" in catalogue


def test_an_empty_or_duplicated_variable_list_raises():
    with pytest.raises(ValueError, match="no variables"):
        scaffold([], id_column="id", time_column="t")
    with pytest.raises(ValueError, match="duplicate"):
        scaffold(["A", "A"])


def test_write_puts_the_table_beside_the_module(fixture_scaffold, tmp_path):
    path = tmp_path / "converted.py"
    fixture_scaffold.write(path)
    assert path.read_text(encoding="utf-8") == fixture_scaffold.source
    assert path.with_suffix(".md").read_text(encoding="utf-8").startswith(
        "# Conversion scaffold"
    )


# --------------------------------------------------------------------------
# A4's acceptance: the emitted spec runs under A1
# --------------------------------------------------------------------------

def test_the_emitted_parity_spec_reconciles_the_fixture(fixture_scaffold,
                                                        tmp_path):
    """Standing in for the porting work: each stub is taken to be the
    variable the scaffold suggested for it, and the emitted ``parity_spec``
    is then run exactly as a converter would run it. That the reconciliation
    comes out clean is a statement about the suggestions as well as the
    wiring."""
    module = load(fixture_scaffold.source, tmp_path / "converted.py")
    ported = {s.stub: s.suggested for s in fixture_scaffold.suggestions}
    assert all(ported.values()), ported

    result, record = record_run(
        TermLife, list(read_modelpoints(MPF)), ASSUMPTIONS, PROJ_LEN,
        outputs=sorted(set(ported.values())),
    )

    class PortedRun:
        """A run of the scaffolded model, as it would be once ported."""

        mp_ids = result.mp_ids

        def array(self, name):
            return result.array(ported[name])

    spec = module.parity_spec(
        PortedRun(), read_results(RESULTS),
        tolerance=TolerancePolicy(Tolerance(relative=1e-12)),
    )
    report = diff(spec, results_digest=record.results_digest)
    assert report.ok, report.to_markdown()
    assert report.coverage == 1.0
    assert set(report.mapping) == set(fixture_scaffold.mapping)
