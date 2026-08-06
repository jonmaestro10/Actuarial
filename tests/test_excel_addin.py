"""The spreadsheet as a client, and the lie a refresh can tell.

RFC-056. Two things are being tested and one of them is the point.

The ordinary half: a request is built from named ranges, submitted, polled
and pulled back, against a real API instance — no Excel, because xlwings is
mocked at the boundary by a fake workbook implementing the four-method
``BookPort``.

The half that matters: **a refresh that writes a shorter block must not
leave the tail of the previous one behind.** Pull a 60-period run, refresh
with a 20-period one, and rows 21–60 of the old run sit under the new run's
heading wearing the new run's stamp, looking like part of it. That is the
worst outcome this module can produce — every other failure is visible, and
this one is invisible — so the suite asserts the tail is gone, and asserts
it from the sheet rather than from the writer's own bookkeeping.

The refusals get the same treatment as the grants: a missing named range
names itself, a fractional projection length is refused rather than
narrowed, a failed run does not get written into cells, a stamp with an
unreadable extent stops rather than guessing what to clear.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the [api] extra")
from fastapi.testclient import TestClient  # noqa: E402

from engine.api.examples import example  # noqa: E402
from engine.excel.addin import (  # noqa: E402
    OPTIONAL_NAMES,
    REQUIRED_NAMES,
    AddIn,
    AddInError,
    Block,
    EngineClient,
    parse_anchor,
    read_block,
    read_request,
    write_block,
)


# --------------------------------------------------------------------------
# The boundary: a workbook with no Excel in it
# --------------------------------------------------------------------------

class FakeBook:
    """A ``BookPort`` over two dictionaries.

    This is the mock the acceptance criterion asks for, and it is small
    because the port is: named ranges in, a sparse grid out. Everything the
    add-in does above this line is ordinary Python.
    """

    def __init__(self, names: dict | None = None):
        self.names = dict(names or {})
        self.cells: dict[tuple[str, int, int], object] = {}

    def has_name(self, name):
        return name in self.names

    def read_name(self, name):
        return self.names[name]

    def write(self, sheet, row, col, rows):
        for r, line in enumerate(rows):
            for c, value in enumerate(line):
                key = (sheet, row + r, col + c)
                if value is None:
                    self.cells.pop(key, None)
                else:
                    self.cells[key] = value

    def read(self, sheet, row, col, n_rows, n_cols):
        return [[self.cells.get((sheet, row + r, col + c))
                 for c in range(n_cols)] for r in range(n_rows)]

    # -- test helpers ------------------------------------------------------

    def column(self, sheet, col, row_from, row_to):
        return [self.cells.get((sheet, r, col))
                for r in range(row_from, row_to + 1)]

    def used_rows(self, sheet, col):
        return sorted(r for (s, r, c) in self.cells if s == sheet and c == col)


def sheet_names(request: dict, *, proj_len: int | None = None) -> dict:
    """The named ranges a workbook would carry for this run request."""
    points = request["modelpoints"]
    header = list(points[0])
    table = [header] + [[point.get(name) for name in header]
                        for point in points]
    assumptions = []
    for key, value in request.get("assumptions", {}).items():
        if isinstance(value, dict):
            for inner, item in value.items():
                assumptions.append([f"{key}.{inner}", item])
        else:
            assumptions.append([key, value])
    return {
        "engine.model": request["model"],
        "engine.proj_len": float(proj_len if proj_len is not None
                                 else request["proj_len"]),
        "engine.modelpoints": table,
        "engine.assumptions": assumptions,
        "engine.outputs": [[name] for name in request.get("outputs", [])],
    }


# --------------------------------------------------------------------------
# The transport: a real API, no socket
# --------------------------------------------------------------------------

@pytest.fixture
def app():
    from engine.api import create_app

    application = create_app(max_workers=1)
    yield application
    application.state.store.shutdown(wait=True)


@pytest.fixture
def client(app):
    """An ``EngineClient`` whose transport is a real ASGI app.

    The add-in's own HTTP path (``urllib_transport``) is exercised
    separately; here the point is the API's real behaviour — its 422s, its
    202, its ``partial`` flag — rather than the socket.
    """
    with TestClient(app) as http:
        def transport(method, url, body, headers):
            path = url.split("http://engine", 1)[-1]
            response = http.request(method, path, content=body,
                                    headers=dict(headers))
            return response.status_code, response.content

        yield EngineClient("http://engine", transport=transport)


@pytest.fixture
def book():
    return FakeBook(sheet_names(example("TermLife")["request"]))


@pytest.fixture
def addin(client, book):
    ticks = iter([f"2026-01-0{i}T00:00:00+00:00" for i in range(1, 9)])
    return AddIn(client=client, book=book, sheet="Results", anchor="A1",
                 clock=lambda: next(ticks))


# --------------------------------------------------------------------------
# Reading the request off the sheet
# --------------------------------------------------------------------------

def test_a_request_is_built_out_of_the_named_ranges(book):
    request = read_request(book)
    original = example("TermLife")["request"]
    assert request["model"] == "TermLife"
    assert request["proj_len"] == original["proj_len"]
    assert len(request["modelpoints"]) == len(original["modelpoints"])
    assert request["modelpoints"][0] == original["modelpoints"][0]
    assert request["assumptions"]["interest"] == \
        original["assumptions"]["interest"]
    # A dotted key rebuilds the tree a spreadsheet cannot hold.
    assert request["assumptions"]["mortality"] == \
        original["assumptions"]["mortality"]


def test_a_missing_named_range_names_itself(book):
    """The ordinary failure: somebody inserted a row and a range slid. The
    message has to say which name to go and look at, not arrive as a 422
    from the far end about a field nobody typed."""
    del book.names["engine.proj_len"]
    with pytest.raises(AddInError, match="engine.proj_len"):
        read_request(book)
    assert set(REQUIRED_NAMES) == {"engine.model", "engine.proj_len",
                                   "engine.modelpoints"}


def test_the_optional_ranges_are_optional(book):
    for name in OPTIONAL_NAMES:
        book.names.pop(name, None)
    request = read_request(book)
    assert "assumptions" not in request and "outputs" not in request
    assert request["model"] == "TermLife"


def test_a_fractional_projection_length_is_refused_not_narrowed(book):
    """Excel has one numeric type, so 20 arrives as 20.0 and must be
    narrowed. Narrowing 20.5 the same way would submit a horizon nobody
    typed."""
    book.names["engine.proj_len"] = 20.0
    assert read_request(book)["proj_len"] == 20
    book.names["engine.proj_len"] = 20.5
    with pytest.raises(AddInError, match="whole number"):
        read_request(book)


def test_trailing_blank_rows_are_dropped_and_half_filled_ones_are_not(book):
    """A named range almost always reaches past the data. A row with *some*
    cells filled is a data problem the engine should complain about, not
    something this discards on its way past."""
    table = [list(row) for row in book.names["engine.modelpoints"]]
    width = len(table[0])
    n_points = len(table) - 1
    table.append([None] * width)
    table.append([None] * width)
    book.names["engine.modelpoints"] = table
    assert len(read_request(book)["modelpoints"]) == n_points

    half = [None] * width
    half[0] = "PARTIAL"
    table.append(half)
    book.names["engine.modelpoints"] = table
    points = read_request(book)["modelpoints"]
    assert len(points) == n_points + 1
    assert points[-1][list(points[-1])[0]] == "PARTIAL"


def test_a_table_without_a_header_or_without_rows_is_refused(book):
    book.names["engine.modelpoints"] = [["id", "age_at_entry"]]
    with pytest.raises(AddInError, match="at least one model point"):
        read_request(book)
    book.names["engine.modelpoints"] = [[None, None], ["x", 1]]
    with pytest.raises(AddInError, match="header"):
        read_request(book)


def test_an_anchor_that_is_not_a_cell_reference_is_refused():
    assert parse_anchor("A1") == (1, 1)
    assert parse_anchor("C4") == (4, 3)
    assert parse_anchor("AA10") == (10, 27)
    with pytest.raises(AddInError, match="not a cell reference"):
        parse_anchor("nonsense")


# --------------------------------------------------------------------------
# Submitting and pulling
# --------------------------------------------------------------------------

def test_a_sheet_submits_polls_and_lands_stamped(addin, book):
    block = addin.refresh()
    assert block.run_id
    assert block.n_rows > 5 and block.n_cols >= 2

    stamped = read_block(book, "Results", "A1")
    assert stamped is not None
    assert stamped.run_id == block.run_id
    assert stamped.assumptions_digest == block.assumptions_digest
    assert stamped.results_digest == block.results_digest
    # The stamp is in the cells, so a reviewer with the workbook and no
    # add-in still knows which run these numbers came from.
    assert book.cells[("Results", 1, 1)] == "run fingerprint"
    assert book.cells[("Results", 1, 2)] == block.run_id


def test_refreshing_an_unchanged_sheet_is_the_same_run(addin, book):
    """Idempotency is what makes the button cheap: the identifier is a
    fingerprint of the request, so pressing Refresh twice starts nothing."""
    first = addin.refresh()
    second = addin.refresh()
    assert first.run_id == second.run_id
    assert first.results_digest == second.results_digest


def test_changing_the_sheet_changes_the_fingerprint_in_the_cells(addin, book):
    """The spreadsheet's own "has anything moved?" question, answered by a
    string comparison beside the numbers."""
    before = addin.refresh()
    book.names["engine.assumptions"] = [
        [key, (value + 0.01 if key == "interest" else value)]
        for key, value in book.names["engine.assumptions"]
    ]
    after = addin.refresh()
    assert after.run_id != before.run_id
    assert after.assumptions_digest != before.assumptions_digest
    assert book.cells[("Results", 1, 2)] == after.run_id
    assert book.cells[("Results", 2, 2)] == after.assumptions_digest


def test_the_numbers_written_are_the_engines_aggregate(addin, book, client):
    block = addin.refresh()
    payload = client.results(block.run_id, aggregate=True)
    heading_row = next(r for r in range(1, block.n_rows + 1)
                       if book.cells.get(("Results", r, 1)) == "t")
    variables = [book.cells[("Results", heading_row, c)]
                 for c in range(2, 2 + len(payload["outputs"]))]
    assert variables == payload["outputs"]
    for index, name in enumerate(variables, start=2):
        column = book.column("Results", index, heading_row + 1,
                             heading_row + len(payload["results"][name]))
        assert column == payload["results"][name]


# --------------------------------------------------------------------------
# The lie a refresh can tell
# --------------------------------------------------------------------------

def test_a_shorter_refresh_leaves_no_tail_of_the_longer_run(addin, book):
    """The failure this module is built around, and the only invisible one.

    A 60-period pull followed by a 20-period pull would otherwise leave rows
    21–60 of the first run sitting under the second run's heading, in the
    second run's column, wearing the second run's stamp. Nothing looks
    wrong. Everything below row 20 is a different projection.
    """
    book.names["engine.proj_len"] = 40.0
    long_block = addin.refresh()
    long_rows = book.used_rows("Results", 2)
    assert long_block.n_rows == len(long_rows) or long_rows

    book.names["engine.proj_len"] = 10.0
    short_block = addin.refresh()
    assert short_block.run_id != long_block.run_id
    assert short_block.n_rows < long_block.n_rows

    # Nothing survives below the new block, in any column it ever touched.
    for col in range(1, long_block.n_cols + 1):
        beyond = book.column("Results", col,
                             short_block.row + short_block.n_rows,
                             long_block.row + long_block.n_rows)
        assert beyond == [None] * len(beyond), f"column {col} kept a tail"

    # And what is left is one block, stamped with the short run.
    assert read_block(book, "Results", "A1").run_id == short_block.run_id


def test_a_narrower_refresh_leaves_no_orphan_column(addin, book):
    """The same failure sideways: fewer variables must not leave the last
    run's rightmost series in place."""
    wide = addin.refresh()
    book.names["engine.outputs"] = [["claims"]]
    narrow = addin.refresh()
    assert narrow.n_cols < wide.n_cols
    for col in range(narrow.col + narrow.n_cols, wide.col + wide.n_cols):
        column = book.column("Results", col, wide.row,
                             wide.row + wide.n_rows)
        assert column == [None] * len(column), f"column {col} orphaned"


def test_the_stamp_records_the_extent_that_was_actually_written(addin, book):
    """The extent is what a later refresh clears, so a stamp that described
    a rectangle other than the one on the sheet would be the bug rather than
    the guard against it."""
    block = addin.refresh()
    stamped = read_block(book, "Results", "A1")
    assert (stamped.n_rows, stamped.n_cols) == (block.n_rows, block.n_cols)
    written = book.used_rows("Results", 1)
    assert max(written) <= block.row + block.n_rows - 1


def test_an_unreadable_extent_stops_rather_than_guessing(book):
    """Clearing a guessed rectangle is worse than refusing: the guess is
    either too small, which leaves a tail, or too big, which eats a
    neighbour's cells."""
    book.write("Results", 1, 1, [
        ["run fingerprint", "abc123"],
        ["assumptions digest", "def456"],
        ["results digest", "789abc"],
        ["pulled", "2026-01-01T00:00:00+00:00"],
        ["block", "not-an-extent"],
    ])
    with pytest.raises(AddInError, match="unreadable"):
        read_block(book, "Results", "A1")


def test_an_empty_anchor_reads_as_no_block(book):
    assert read_block(book, "Results", "A1") is None


# --------------------------------------------------------------------------
# Drill-down, and what a partial block says
# --------------------------------------------------------------------------

def test_a_model_point_pull_is_marked_partial(addin, book, client):
    """E3's rule, carried into the sheet: the results digest covers the
    whole block, and a one-policy pull is not the whole block."""
    whole = addin.refresh()
    assert whole.partial is False

    mp = client.results(whole.run_id, aggregate=False)["modelpoints"][0]
    one = addin.refresh(run_id=whole.run_id, variable="claims", modelpoint=mp)
    assert one.partial is True
    assert one.run_id == whole.run_id


# --------------------------------------------------------------------------
# Refusals from the far end
# --------------------------------------------------------------------------

def test_a_rejected_request_carries_the_apis_own_explanation(addin, book):
    """The API goes out of its way to say which variable and which model
    point. A client that showed the status code and dropped the body would
    be the least helpful consumer of that effort."""
    book.names["engine.outputs"] = [["no_such_variable"]]
    with pytest.raises(AddInError, match="no_such_variable"):
        addin.refresh()


def test_an_unknown_model_is_refused_before_anything_is_written(addin, book):
    book.names["engine.model"] = "NoSuchTemplate"
    with pytest.raises(AddInError, match="NoSuchTemplate"):
        addin.refresh()
    assert book.cells == {}


def test_a_run_that_never_finishes_says_so_without_writing(client, book):
    """A timeout is not a failure and must not read as one: the fingerprint
    is stable, so the honest message is "refresh again later"."""
    class Pending(EngineClient):
        def submit(self, request):
            return {"run_id": "f" * 32, "state": "queued"}

        def run(self, run_id):
            return {"run_id": run_id, "state": "running"}

    addin = AddIn(client=Pending("http://engine", transport=client.transport),
                  book=book, sheet="Results")
    with pytest.raises(AddInError, match="refresh again later"):
        addin.refresh(timeout=0.0)
    assert book.cells == {}


def test_a_failed_run_is_not_written_into_cells(client, book):
    class Failing(EngineClient):
        def submit(self, request):
            return {"run_id": "e" * 32, "state": "queued"}

        def run(self, run_id):
            return {"run_id": run_id, "state": "failed",
                    "error": "age_at_entry missing on model point 3"}

    addin = AddIn(client=Failing("http://engine", transport=client.transport),
                  book=book, sheet="Results")
    with pytest.raises(AddInError, match="age_at_entry missing"):
        addin.refresh()
    assert book.cells == {}


# --------------------------------------------------------------------------
# The transport, and what the add-in is not
# --------------------------------------------------------------------------

def test_the_token_travels_as_a_bearer_header():
    seen = {}

    def transport(method, url, body, headers):
        seen.update({"method": method, "url": url, "headers": dict(headers)})
        return 200, b'{"run_id": "x"}'

    EngineClient("http://engine/", token="s3cret",
                 transport=transport).submit({"model": "TermLife"})
    assert seen["headers"]["authorization"] == "Bearer s3cret"
    assert seen["url"] == "http://engine/runs"
    assert seen["method"] == "POST"


def test_no_token_means_no_header():
    seen = {}

    def transport(method, url, body, headers):
        seen.update(dict(headers))
        return 200, b"{}"

    EngineClient("http://engine", transport=transport).run("abc")
    assert "authorization" not in seen


def test_the_real_http_path_reads_a_body_and_an_error_body():
    """``urllib_transport`` is what runs on the actuary's laptop, so it is
    exercised against a real socket rather than assumed."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from engine.excel.addin import urllib_transport

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            failing = self.path.endswith("/boom")
            body = (json.dumps({"detail": "no such run"}).encode() if failing
                    else json.dumps({"run_id": "abc", "state": "succeeded"}
                                    ).encode())
            self.send_response(404 if failing else 200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        client = EngineClient(base, transport=urllib_transport)
        assert client.run("abc")["state"] == "succeeded"
        # An HTTPError still has to yield its body: the explanation is the
        # useful half of a 4xx.
        with pytest.raises(AddInError, match="no such run"):
            client.run("boom")
    finally:
        server.shutdown()
        server.server_close()


def test_the_add_in_cannot_compute_anything():
    """The property the whole design rests on: a number this add-in wrote
    came from a registered run, because there is nowhere else it could have
    come from. It imports no executor, no template and no assumption
    object — only the API and RFC-047's cell formatting."""
    import inspect

    import engine.excel.addin as addin_module

    source = inspect.getsource(addin_module)
    for forbidden in ("engine.core.runner", "engine.core.vector",
                      "engine.core.stochastic", "engine.library",
                      "engine.data.assumptions", "record_run"):
        assert forbidden not in source, f"the add-in reaches for {forbidden}"
    assert "from engine.excel.workbook import" in source


def test_importing_the_add_in_does_not_import_xlwings():
    """The boundary earning its keep. Every machine this suite runs on has
    no Excel; the import has to cost nothing there, or the add-in becomes
    untestable exactly where it is developed.

    Checked in a subprocess rather than by reloading the module: a reload
    would mint a second ``Block`` class and quietly break identity for every
    test after it.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import engine.excel.addin as m; "
        "print('xlwings' in sys.modules, bool(m.read_request and m.write_block))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "False True"


def test_writing_a_block_needs_no_client_at_all():
    """The writer is separable from the transport, which is what lets a
    deployment with its own HTTP stack reuse the stamping."""
    book = FakeBook()
    block = write_block(
        book, "Sheet1", "B2",
        {"run_id": "a" * 32, "assumptions_digest": "b" * 32,
         "results_digest": "c" * 32},
        {"outputs": ["claims"], "results": {"claims": [1.0, 2.0, 3.0]}},
        pulled_at="2026-01-01T00:00:00+00:00", label="year-end",
    )
    assert isinstance(block, Block)
    assert block.row == 2 and block.col == 2
    # Anchored at B2, so the block's own top-left is column 2, not column 1.
    assert book.cells[("Sheet1", 2, 2)] == "block label"
    assert ("Sheet1", 2, 1) not in book.cells
    assert read_block(book, "Sheet1", "B2").run_id == "a" * 32
