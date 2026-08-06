"""Reading somebody else's model office, and refusing to guess.

RFC-034. Two fixtures stand in for a client's files: a model point file in
the documented MPF layout and a results extract. Both are synthetic and
hand-authored — the repo holds no proprietary Prophet data — so what these
tests can prove is the reader's behaviour, not format coverage, and the RFC
says so.

The reconciliation at the end is the one that matters. The fixture results
are **not** the engine's own output written back out: they are recomputed
here by an independent term-assurance projection written from the product
definition, asserted cell by cell against the committed file (so a stale
fixture fails loudly), and then reconciled against a real engine run through
RFC-033's parity core. Two implementations, one document, 1e-12.
"""

from pathlib import Path

import pytest

from engine.core.registry import record_run
from engine.data.assumptions import Assumptions, MortalityTable
from engine.migrate import (
    MPF_DIALECT,
    RESULTS_DIALECT,
    ProphetDialect,
    ProphetFormatError,
    read_modelpoints,
    read_results,
    read_table,
)
from engine.library.term_life import TermLife
from engine.parity import ParitySpec, Tolerance, TolerancePolicy, diff

FIXTURES = Path(__file__).parent / "fixtures" / "prophet"
MPF = FIXTURES / "term_life.pro"
RESULTS = FIXTURES / "term_life_results.csv"

QX = {age: min(0.0006 * 1.095 ** (age - 20), 0.5) for age in range(20, 111)}
ASSUMPTIONS = Assumptions(mortality=MortalityTable(QX), lapse=0.04,
                          interest=0.03, expense_per_policy=50.0)
PROJ_LEN = 25
OUTPUTS = ["pols_if", "pols_death", "claims", "premiums"]

#: External column → engine variable. Written out, never inferred.
RESULT_MAPPING = {
    "POLS_IF": "pols_if",
    "DEATHS": "pols_death",
    "CLAIMS": "claims",
    "PREMIUMS": "premiums",
}


def naive_projection(mp, proj_len=PROJ_LEN):
    """A term assurance projected from the product definition, not the engine.

    Level premiums from in-force policies at the start of each period, death
    claims at the end, mortality then lapse in that order on whoever is left
    — the sequential decrement basis, written out in three lines because at
    this size it can be.
    """
    rows = []
    pols = float(mp.init_pols)
    for t in range(proj_len + 1):
        in_term = 1.0 if t < mp.term_years else 0.0
        q = QX[mp.age_at_entry + t] * in_term
        deaths = pols * q
        lapses = (pols - deaths) * 0.04 * in_term
        rows.append({
            "POL_NUMBER": mp.id, "T": t, "POLS_IF": pols, "DEATHS": deaths,
            "CLAIMS": deaths * mp.sum_assured,
            "PREMIUMS": pols * mp.annual_premium * in_term,
        })
        survivors = pols - deaths - lapses
        pols = survivors if t + 1 <= mp.term_years - 1 else 0.0
    return rows


@pytest.fixture(scope="module")
def mpf():
    return read_modelpoints(MPF)


# --------------------------------------------------------------------------
# The model point file
# --------------------------------------------------------------------------

def test_the_fixture_round_trips_to_model_points_with_its_types(mpf):
    assert len(mpf) == 4
    first = mpf[0]
    assert first.id == "MP0001"
    assert (first.age_at_entry, first.term_years) == (45, 20)
    assert isinstance(first.age_at_entry, int)      # VARIABLE_TYPES said I
    assert isinstance(first.sum_assured, float)     # and N here
    assert first.sum_assured == 250_000.0
    assert first.sex == "M"
    assert [mp.id for mp in mpf] == ["MP0001", "MP0002", "MP0003", "MP0004"]
    assert mpf.file.metadata["Output_Format"] == "4"
    assert mpf.file.metadata["NUMLINES"] == "4"


def test_every_incumbent_field_appears_in_the_mapping_report(mpf):
    report = mpf.mapping
    assert {f.source for f in report.fields} == set(mpf.file.names)
    by_source = {f.source: f for f in report.fields}
    assert by_source["POL_NUMBER"].target == "id"
    assert by_source["POL_NUMBER"].action == "renamed"
    assert by_source["SUM_ASSURED"].action == "consumed"   # already our name
    assert report.n_rows == 4
    markdown = report.to_markdown()
    for name in mpf.file.names:
        assert f"`{name}`" in markdown


def test_a_field_in_the_wrong_unit_is_ignored_rather_than_converted(mpf):
    """``DURATION_IF_M`` is months and ``duration_in_force`` is years. A
    reader that mapped them would divide by twelve, or fail to, and either
    way nobody would be asked."""
    assert set(mpf.mapping.ignored) == {"SPCODE", "DURATION_IF_M"}
    assert not hasattr(mpf[3], "duration_in_force")
    assert "DURATION_IF_M" in mpf.mapping.to_markdown()
    assert "ignored" in mpf.mapping.to_markdown()


def test_the_caller_can_map_what_the_default_will_not(mpf):
    """The point of leaving it out is that a human decides — in one line,
    recorded in the report."""
    read = read_modelpoints(MPF, mapping={"DURATION_IF_M": "duration_months"})
    by_source = {f.source: f for f in read.mapping.fields}
    assert by_source["DURATION_IF_M"].action == "renamed"
    assert read[3].duration_months == 60
    assert read.mapping.ignored == ("SPCODE",)


def test_a_default_mapping_can_be_dropped(mpf):
    read = read_modelpoints(MPF, mapping={"SEX": None})
    assert not hasattr(read[0], "sex")
    assert "SEX" in read.mapping.ignored


def test_unmapped_columns_can_be_carried_instead_of_dropped():
    read = read_modelpoints(MPF, keep_unmapped=True)
    assert read[0].spcode == "TERM01"
    assert read[3].duration_if_m == 60
    assert read.mapping.ignored == ()
    assert {f.action for f in read.mapping.fields} >= {"carried"}


def test_two_columns_that_would_collide_raise(tmp_path):
    path = tmp_path / "collide.pro"
    path.write_text(
        "VARIABLE_TYPES,T,T,I\n"
        "!POL_NUMBER,POLICY_NUMBER,AGE_AT_ENTRY\n"
        "*,A,B,45\n", encoding="utf-8")
    with pytest.raises(ProphetFormatError, match="both .* map to 'id'"):
        read_modelpoints(path)


def test_a_file_with_nothing_the_engine_reads_raises(tmp_path):
    path = tmp_path / "opaque.pro"
    path.write_text("VARIABLE_TYPES,T,T\n!SPCODE,PROD_CD\n*,A,B\n",
                    encoding="utf-8")
    with pytest.raises(ProphetFormatError, match="no column maps"):
        read_modelpoints(path)


# --------------------------------------------------------------------------
# Malformed input names the line and the column
# --------------------------------------------------------------------------

def test_a_short_row_names_its_line(tmp_path):
    path = tmp_path / "short.pro"
    path.write_text(
        "VARIABLE_TYPES,T,I,N\n"
        "!POL_NUMBER,AGE_AT_ENTRY,SUM_ASSURED\n"
        "*,A,45,100\n"
        "*,B,55\n", encoding="utf-8")
    with pytest.raises(ProphetFormatError, match=r"short\.pro:4: 2 fields"):
        read_table(path)


def test_a_bad_value_names_its_line_and_column(tmp_path):
    path = tmp_path / "bad.pro"
    path.write_text(
        "VARIABLE_TYPES,T,I,N\n"
        "!POL_NUMBER,AGE_AT_ENTRY,SUM_ASSURED\n"
        "*,A,45,one hundred\n", encoding="utf-8")
    with pytest.raises(ProphetFormatError,
                       match=r"bad\.pro:3: column 'SUM_ASSURED'"):
        read_table(path)


def test_an_undefined_type_code_raises(tmp_path):
    path = tmp_path / "codes.pro"
    path.write_text(
        "VARIABLE_TYPES,T,Q\n!POL_NUMBER,AGE_AT_ENTRY\n*,A,45\n",
        encoding="utf-8")
    with pytest.raises(ProphetFormatError, match="type code 'Q'"):
        read_table(path)


def test_a_type_line_of_the_wrong_length_raises(tmp_path):
    path = tmp_path / "count.pro"
    path.write_text(
        "VARIABLE_TYPES,T\n!POL_NUMBER,AGE_AT_ENTRY\n*,A,45\n",
        encoding="utf-8")
    with pytest.raises(ProphetFormatError, match="1 types for 2 columns"):
        read_table(path)


def test_a_file_with_no_header_raises(tmp_path):
    path = tmp_path / "headless.pro"
    path.write_text("Output_Format,4\nNUMLINES,0\n", encoding="utf-8")
    with pytest.raises(ProphetFormatError, match="no header line"):
        read_table(path)


def test_a_repeated_column_name_raises(tmp_path):
    path = tmp_path / "dupe.pro"
    path.write_text("!A,A\n*,1,2\n", encoding="utf-8")
    with pytest.raises(ProphetFormatError, match=r"repeats \['A'\]"):
        read_table(path)


def test_the_dialect_is_load_bearing_and_says_so(tmp_path):
    """Reading a results extract with the MPF dialect is a mistake the
    reader must not absorb: its header line carries no ``!``, so under that
    dialect the file has no header at all — and a table with no column names
    is refused rather than positionally invented."""
    with pytest.raises(ProphetFormatError, match="no header line beginning"):
        read_table(RESULTS, MPF_DIALECT)


def test_a_pipe_delimited_estate_is_a_dialect_not_a_fork(tmp_path):
    path = tmp_path / "piped.pro"
    path.write_text(
        "VARIABLE_TYPES|T|I\n"
        "!POL_NUMBER|AGE_AT_ENTRY\n"
        "@|A|45\n", encoding="utf-8")
    read = read_table(path, ProphetDialect(delimiter="|", record_prefix="@"))
    assert read.columns == {"POL_NUMBER": ["A"], "AGE_AT_ENTRY": [45]}


def test_missing_markers_become_none_not_zero(tmp_path):
    path = tmp_path / "gaps.pro"
    path.write_text(
        "VARIABLE_TYPES,T,N\n!POL_NUMBER,SUM_ASSURED\n*,A,\n*,B,NA\n",
        encoding="utf-8")
    assert read_table(path).columns["SUM_ASSURED"] == [None, None]


def test_types_are_inferred_when_no_type_line_says(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text("POL_NUMBER,T,VALUE\nA,0,1.5\nA,1,2\n", encoding="utf-8")
    table = read_table(path, RESULTS_DIALECT)
    assert table.types == {"POL_NUMBER": "text", "T": "integer",
                           "VALUE": "number"}
    assert table.columns["T"] == [0, 1]


def test_a_date_column_reads_as_a_date(tmp_path):
    from datetime import date

    path = tmp_path / "dates.pro"
    path.write_text(
        "VARIABLE_TYPES,T,D\n!POL_NUMBER,DATE_OF_BIRTH\n*,A,01/02/1975\n",
        encoding="utf-8")
    assert read_modelpoints(path)[0].dob == date(1975, 2, 1)


# --------------------------------------------------------------------------
# The results extract, and the reconciliation
# --------------------------------------------------------------------------

def test_the_committed_results_fixture_is_the_independent_projection(mpf):
    """A fixture nobody recomputes is a fixture that silently goes stale."""
    table = read_results(RESULTS)
    expected = [row for mp in mpf for row in naive_projection(mp)]
    assert table.n_rows == len(expected)
    for name in ("POL_NUMBER", "T", "POLS_IF", "DEATHS", "CLAIMS", "PREMIUMS"):
        assert list(table.column(name)) == [row[name] for row in expected], name


def test_the_engine_reconciles_against_the_results_extract(mpf):
    """A2's acceptance: the fixture extract feeds A1 and reconciles."""
    result, record = record_run(TermLife, list(mpf), ASSUMPTIONS, PROJ_LEN,
                                outputs=OUTPUTS)
    table = read_results(RESULTS, rename={"POL_NUMBER": "modelpoint_id",
                                          "T": "t"})
    spec = ParitySpec.from_results(
        result, table, RESULT_MAPPING,
        tolerance=TolerancePolicy(Tolerance(relative=1e-12)),
        label="TermLife against the Prophet fixture extract",
    )
    report = diff(spec, results_digest=record.results_digest)
    assert report.ok, report.to_markdown()
    assert report.coverage == 1.0
    assert report.n_matched_rows == report.n_external_rows == 4 * (PROJ_LEN + 1)
    assert report.max_relative < 1e-12
    assert report.unmapped_columns == ()


def test_the_reconciliation_still_bites(mpf):
    """The same reconciliation must fail on a difference this small, or the
    passing one above means nothing."""
    result, _ = record_run(TermLife, list(mpf), ASSUMPTIONS, PROJ_LEN,
                           outputs=OUTPUTS)
    table = read_results(RESULTS, rename={"POL_NUMBER": "modelpoint_id",
                                          "T": "t"})
    columns = {name: list(values) for name, values in table.columns.items()}
    columns["CLAIMS"][3] *= 1.000_000_1
    from engine.parity import ExternalTable

    spec = ParitySpec.from_results(
        result, ExternalTable(columns), RESULT_MAPPING,
        tolerance=TolerancePolicy(Tolerance(relative=1e-12)),
    )
    report = diff(spec)
    assert not report.ok
    assert report.variable("claims").n_outside == 1
    assert report.variable("claims").worst_modelpoint == "MP0001"
    assert report.variable("claims").worst_t == 3


def test_renaming_a_column_that_is_not_there_raises():
    with pytest.raises(ProphetFormatError, match="cannot rename 'NOPE'"):
        read_results(RESULTS, rename={"NOPE": "modelpoint_id"})
