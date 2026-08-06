"""The spreadsheet as a client, and the lie a refresh can tell.

RFC-056. E2 wrote a workbook *after* a run; this makes the spreadsheet a
first-class client of the API — a request built from named ranges, submitted
over REST, polled by fingerprint, and pulled back into cells with the run
fingerprint and assumption digests stamped beside the numbers, exactly as
RFC-047's workbooks stamp their sheets.

**The add-in cannot compute anything.** It imports no executor, no template
and no assumption object; it speaks HTTP to a deployment and formats what
comes back. That is worth stating as a property rather than a habit, and
``tests/test_excel_addin.py`` asserts it: a number this add-in put in a cell
came from a registered run, because there is no other place it could have
come from. It is RFC-032's rule for the page, applied to the spreadsheet.

**The lie a refresh can tell.** The failure this module is built around is
not authentication and not formatting — it is a *stale stamp beside fresh
numbers*, and its nastiest form is subtler still. Pull a 60-period run into
a sheet, then refresh with a 20-period one: the new block is written over
the first twenty rows and **rows 21 to 60 of the old run stay exactly where
they were**, under the new run's heading, in the new run's column, wearing
the new run's stamp. Nothing is out of place on the screen. Everything below
row 20 is from a different projection.

So a block records its own extent in its stamp, and writing a block
**clears the extent the previous one recorded** before writing the new one.
The stamp and the values are one write, never two. :func:`read_block` reads
the stamp back, so a sheet can be checked rather than trusted.

**Idempotency is what makes Refresh cheap.** A run identifier is a
fingerprint of the request (RFC-003), so refreshing an unchanged sheet
returns the same identifier and does no computation anywhere. When
something *has* changed, the fingerprint changes — and the stamp beside the
numbers is where that shows up. The spreadsheet's own "has anything moved?"
question is answered by a string comparison.

Behind the same ``[excel]`` extra as RFC-047, with xlwings imported only by
:class:`XlwingsBook`. Everything above that boundary works against the small
:class:`BookPort` protocol, which is why the suite exercises all of it with
no Excel anywhere.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

# The one thing shared with RFC-047: how a value becomes a cell. A
# non-finite number is text here for the same reason it is text there —
# openpyxl and xlwings both write a blank otherwise, and a blank in a claims
# column reads as zero.
from engine.excel.workbook import _cell

#: The named ranges a sheet must carry to describe a run, and the ones it
#: may. Deliberately a fixed vocabulary rather than a convention: a name
#: this module does not know about is a name somebody misspelled, and
#: guessing at it is how a run gets submitted with the wrong horizon.
REQUIRED_NAMES = ("engine.model", "engine.proj_len", "engine.modelpoints")
OPTIONAL_NAMES = ("engine.assumptions", "engine.outputs", "engine.executor")

#: Where a pulled block goes, unless the caller says otherwise.
DEFAULT_ANCHOR = "A1"

STAMP_LABELS = ("run fingerprint", "assumptions digest", "results digest",
                "pulled", "block")


def _now() -> str:
    """When a block was pulled, to the second and in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AddInError(RuntimeError):
    """Something the add-in will not do to a spreadsheet.

    A missing named range, a run that failed, a block written where another
    run's block already sits. Every one of them is a case where carrying on
    would leave numbers in cells that look like they belong together.
    """


# --------------------------------------------------------------------------
# The transport
# --------------------------------------------------------------------------

Transport = Callable[[str, str, bytes | None, Mapping[str, str]],
                     tuple[int, bytes]]


def urllib_transport(method: str, url: str, body: bytes | None,
                     headers: Mapping[str, str]) -> tuple[int, bytes]:
    """One HTTP request, on the standard library.

    No httpx, no requests: the add-in runs on an actuary's laptop next to
    Excel, and the fewer things that have to be installed there the more
    likely it is to be installed at all. ``[excel]`` stays openpyxl and
    xlwings.
    """
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=dict(headers))
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        # An error body is the interesting half of an error: the API says
        # which variable, which model point, which role. Swallowing it and
        # showing the status code would make the add-in the least helpful
        # client of an API that goes out of its way to explain itself.
        return error.code, error.read()


@dataclass
class EngineClient:
    """The documented REST API, from a spreadsheet.

    ``token`` is an RFC-043 bearer token; without one the client works
    against a deployment that has no principals configured, which is the
    local and library case.
    """

    base_url: str
    token: str | None = None
    transport: Transport = urllib_transport
    timeout: float = 30.0

    def _call(self, method: str, path: str, payload: Any = None) -> Any:
        headers = {"accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        status, raw = self.transport(method, self.base_url.rstrip("/") + path,
                                     body, headers)
        text = raw.decode("utf-8") if raw else ""
        try:
            parsed = json.loads(text) if text else None
        except ValueError:
            parsed = text
        if status >= 400:
            detail = parsed.get("detail") if isinstance(parsed, dict) \
                else parsed
            raise AddInError(f"{method} {path} → {status}: {detail}")
        return parsed

    def submit(self, request: dict) -> dict:
        """``POST /runs``. Returns the run summary, fingerprint included."""
        return self._call("POST", "/runs", request)

    def run(self, run_id: str) -> dict:
        return self._call("GET", f"/runs/{run_id}")

    def results(self, run_id: str, *, aggregate: bool = True,
                variable: str | None = None,
                modelpoint: str | None = None) -> dict:
        query = []
        if aggregate and modelpoint is None:
            query.append("aggregate=true")
        if variable:
            query.append(f"variable={variable}")
        if modelpoint is not None:
            query.append(f"modelpoint={modelpoint}")
        suffix = ("?" + "&".join(query)) if query else ""
        return self._call("GET", f"/runs/{run_id}/results{suffix}")

    def wait(self, run_id: str, *, timeout: float = 300.0,
             interval: float = 1.0,
             sleep: Callable[[float], None] = time.sleep) -> dict:
        """Poll until the run is finished, or say that it is not.

        Blocking, and Excel is frozen while it blocks — which is why
        :meth:`submit` and this are separate calls and why the timeout is an
        argument rather than a constant. A sixty-year run of a hundred
        thousand policies is a *submit now, refresh later* workflow, and
        RFC-056 documents it as one rather than pretending a spreadsheet can
        wait for it.
        """
        deadline = time.monotonic() + timeout
        while True:
            summary = self.run(run_id)
            state = summary.get("state")
            if state == "succeeded":
                return summary
            if state == "failed":
                reason = summary.get("error") or "no reason given"
                raise AddInError(f"run {run_id} failed: {reason}")
            if time.monotonic() >= deadline:
                raise AddInError(
                    f"run {run_id} is still {state} after {timeout:g}s. It "
                    f"has not failed — refresh again later; the fingerprint "
                    f"is stable, so nothing is recomputed."
                )
            sleep(interval)


# --------------------------------------------------------------------------
# The spreadsheet boundary
# --------------------------------------------------------------------------

class BookPort(Protocol):
    """The four things this module does to a workbook.

    Small on purpose. Everything above it is ordinary Python and is tested
    with no Excel present; :class:`XlwingsBook` is the only code that has
    ever heard of a COM object.
    """

    def read_name(self, name: str) -> Any:
        """The value of a named range: a scalar, a list, or a list of rows."""

    def has_name(self, name: str) -> bool:
        ...

    def write(self, sheet: str, row: int, col: int,
              rows: Sequence[Sequence[Any]]) -> None:
        """Write a rectangle with its top-left at 1-based ``(row, col)``."""

    def read(self, sheet: str, row: int, col: int, n_rows: int,
             n_cols: int) -> list[list[Any]]:
        ...


class XlwingsBook:
    """A live Excel workbook, through xlwings.

    The import is here and nowhere else, so importing
    :mod:`engine.excel.addin` on a machine with no Excel — every CI machine
    this repo has — costs nothing and fails nowhere.
    """

    def __init__(self, book: Any = None, path: str | None = None):
        try:
            import xlwings
        except ImportError as exc:  # pragma: no cover - exercised by the extra
            raise ImportError(
                "the Excel add-in needs xlwings: pip install -e '.[excel]'"
            ) from exc
        if book is None:
            book = (xlwings.Book(path) if path is not None
                    else xlwings.books.active)
        self.book = book

    def has_name(self, name: str) -> bool:  # pragma: no cover - needs Excel
        try:
            self.book.names[name]
            return True
        except Exception:
            return False

    def read_name(self, name: str) -> Any:  # pragma: no cover - needs Excel
        return self.book.names[name].refers_to_range.value

    def write(self, sheet, row, col, rows):  # pragma: no cover - needs Excel
        target = self.book.sheets[sheet]
        target.range((row, col)).value = [list(r) for r in rows]

    def read(self, sheet, row, col, n_rows, n_cols):  # pragma: no cover
        target = self.book.sheets[sheet]
        value = target.range((row, col), (row + n_rows - 1,
                                          col + n_cols - 1)).value
        if n_rows == 1:
            return [list(value) if isinstance(value, list) else [value]]
        return [list(r) if isinstance(r, list) else [r] for r in value]


# --------------------------------------------------------------------------
# Reading a request off the sheet
# --------------------------------------------------------------------------

def _as_rows(value: Any) -> list[list[Any]]:
    """Whatever a named range gave us, as a rectangle."""
    if value is None:
        return []
    if not isinstance(value, list):
        return [[value]]
    if not value:
        return []
    if not isinstance(value[0], list):
        return [[item] for item in value]
    return [list(row) for row in value]


def _scalar(value: Any) -> Any:
    rows = _as_rows(value)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise AddInError(
            f"expected a single cell, got a {len(rows)}×"
            f"{len(rows[0]) if rows else 0} range"
        )
    return rows[0][0]


def _flat(value: Any) -> list[Any]:
    return [cell for row in _as_rows(value) for cell in row
            if cell is not None and cell != ""]


def _int(value: Any, what: str) -> int:
    """An Excel number as an integer, refusing one that is not whole.

    Excel has one numeric type, so ``20`` arrives as ``20.0`` and must be
    narrowed. ``20.5`` also arrives, and narrowing *that* silently would
    submit a projection length nobody typed.
    """
    number = float(value)
    if number != int(number):
        raise AddInError(f"{what} must be a whole number, got {value!r}")
    return int(number)


def read_request(book: BookPort) -> dict:
    """Build a run request from the sheet's named ranges.

    The vocabulary is fixed (:data:`REQUIRED_NAMES`, :data:`OPTIONAL_NAMES`)
    and a missing required name is named in the error. A spreadsheet where
    somebody inserted a row and a range slid is the ordinary failure here,
    and the ordinary failure has to produce a message that says which name
    to go and look at — not a 422 from the far end about a field the person
    at the keyboard never typed.
    """
    missing = [name for name in REQUIRED_NAMES if not book.has_name(name)]
    if missing:
        raise AddInError(
            f"the workbook has no named range(s) {missing}. A run needs "
            f"{list(REQUIRED_NAMES)}; {list(OPTIONAL_NAMES)} are optional."
        )

    request: dict[str, Any] = {
        "model": str(_scalar(book.read_name("engine.model"))).strip(),
        "proj_len": _int(_scalar(book.read_name("engine.proj_len")),
                         "engine.proj_len"),
        "modelpoints": _table(book.read_name("engine.modelpoints"),
                              "engine.modelpoints"),
    }
    if not request["model"]:
        raise AddInError("engine.model is empty")
    if book.has_name("engine.assumptions"):
        request["assumptions"] = _pairs(book.read_name("engine.assumptions"))
    if book.has_name("engine.outputs"):
        outputs = [str(name).strip() for name in
                   _flat(book.read_name("engine.outputs"))]
        if outputs:
            request["outputs"] = outputs
    if book.has_name("engine.executor"):
        executor = _scalar(book.read_name("engine.executor"))
        if executor:
            request["executor"] = str(executor).strip()
    return request


def _table(value: Any, what: str) -> list[dict]:
    """A header row plus data rows, as a list of model-point objects.

    Blank trailing rows are dropped — a spreadsheet range almost always
    reaches past the data — but a row with *some* cells filled is kept and
    its blanks are kept as nulls, because a half-filled model point is a
    data problem the engine should be allowed to complain about rather than
    something this quietly discards.
    """
    rows = _as_rows(value)
    if len(rows) < 2:
        raise AddInError(
            f"{what} needs a header row and at least one model point; it "
            f"has {len(rows)} row(s)"
        )
    header = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
    if not any(header):
        raise AddInError(f"{what}'s first row is blank; it must be the header")
    unnamed = [i for i, name in enumerate(header) if not name]
    out = []
    for row in rows[1:]:
        if all(cell is None or cell == "" for cell in row):
            continue
        point = {}
        for i, name in enumerate(header):
            if i in unnamed:
                continue
            point[name] = row[i] if i < len(row) else None
        out.append(point)
    if not out:
        raise AddInError(f"{what} has a header but no model points")
    return out


def _pairs(value: Any) -> dict:
    """A two-column ``key | value`` range, as a mapping.

    Nested keys are dotted — ``mortality.90`` — because an assumption spec
    is a tree and a spreadsheet is not.
    """
    out: dict[str, Any] = {}
    for row in _as_rows(value):
        if not row or row[0] is None or str(row[0]).strip() == "":
            continue
        key = str(row[0]).strip()
        cell = row[1] if len(row) > 1 else None
        target = out
        parts = key.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise AddInError(
                    f"assumption key {key!r} nests under {part!r}, which "
                    f"already has a value"
                )
        target[parts[-1]] = cell
    return out


# --------------------------------------------------------------------------
# Writing a block back, stamped
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    """A written block: where it is, how big it is, and which run it is."""

    sheet: str
    row: int
    col: int
    n_rows: int
    n_cols: int
    run_id: str
    assumptions_digest: str | None = None
    results_digest: str | None = None
    partial: bool = False

    def extent(self) -> str:
        return f"{self.n_rows}x{self.n_cols}"


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def parse_anchor(anchor: str) -> tuple[int, int]:
    """``"C4"`` → ``(4, 3)``. 1-based, the way a spreadsheet counts."""
    letters = "".join(c for c in anchor if c.isalpha()).upper()
    digits = "".join(c for c in anchor if c.isdigit())
    if not letters or not digits:
        raise AddInError(f"{anchor!r} is not a cell reference like 'C4'")
    col = 0
    for char in letters:
        col = col * 26 + (ord(char) - 64)
    return int(digits), col


def _stamp_rows(summary: Mapping[str, Any], pulled: str, extent: str
                ) -> list[list[Any]]:
    return [
        ["run fingerprint", summary.get("run_id")],
        ["assumptions digest", summary.get("assumptions_digest")],
        ["results digest", summary.get("results_digest")],
        ["pulled", pulled],
        ["block", extent],
    ]


def write_block(book: BookPort, sheet: str, anchor: str,
                summary: Mapping[str, Any], payload: Mapping[str, Any], *,
                pulled_at: str, previous: Block | None = None,
                label: str | None = None) -> Block:
    """Write one stamped block of results, clearing what it replaces.

    ``previous`` is the block this one replaces — normally the one
    :func:`read_block` just read out of the sheet. Its extent is cleared
    *before* the new block is written, which is the whole point: a refresh
    that writes fewer rows than last time leaves the tail of the old run in
    the cells below, and that tail is indistinguishable from part of the new
    block.

    The stamp is written in the same call as the values. There is no state
    in which this sheet holds one run's numbers under another run's
    fingerprint.
    """
    row, col = parse_anchor(anchor)
    variables = list(payload.get("outputs") or [])
    results = payload.get("results") or {}
    n_steps = max((len(results[name]) for name in variables), default=0)

    body: list[list[Any]] = []
    heading = ["t"] + variables
    body.append([_cell(v) for v in heading])
    for t in range(n_steps):
        body.append([t] + [_cell(results[name][t])
                           if t < len(results[name]) else None
                           for name in variables])

    stamp = _stamp_rows(summary, pulled_at,
                        f"{len(body) + len(STAMP_LABELS) + 1}x"
                        f"{max(len(heading), 2)}")
    if label:
        stamp.insert(0, ["block label", label])
    rows: list[list[Any]] = list(stamp) + [[]] + body
    width = max(len(r) for r in rows)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    # The extent recorded in the stamp is the extent actually written, so a
    # later refresh clears exactly this and not an estimate of it.
    for line in rows:
        if line and line[0] == "block":
            line[1] = f"{len(rows)}x{width}"

    if previous is not None:
        _clear(book, previous)
    book.write(sheet, row, col, rows)
    return Block(sheet=sheet, row=row, col=col, n_rows=len(rows),
                 n_cols=width, run_id=str(summary.get("run_id")),
                 assumptions_digest=summary.get("assumptions_digest"),
                 results_digest=summary.get("results_digest"),
                 partial=bool(payload.get("partial")))


def _clear(book: BookPort, block: Block) -> None:
    blanks = [[None] * block.n_cols for _ in range(block.n_rows)]
    book.write(block.sheet, block.row, block.col, blanks)


def read_block(book: BookPort, sheet: str, anchor: str) -> Block | None:
    """Read a block's stamp back out of the sheet, or ``None``.

    What makes a live spreadsheet checkable rather than merely stamped: the
    fingerprint, the digests and the extent are all *in the cells*, so a
    reviewer with the workbook and no add-in can still see which run the
    numbers came from, and the add-in can see how much to clear.
    """
    row, col = parse_anchor(anchor)
    # One row taller than a stamp, because an optional label pushes it down.
    head = book.read(sheet, row, col, len(STAMP_LABELS) + 1, 2)
    found = {}
    for line in head:
        if line and line[0] is not None:
            found[str(line[0]).strip()] = line[1] if len(line) > 1 else None
    if "run fingerprint" not in found or not found["run fingerprint"]:
        return None
    extent = str(found.get("block") or "")
    try:
        n_rows, n_cols = (int(part) for part in extent.lower().split("x"))
    except ValueError:
        # A stamp whose extent nobody can read is a block whose size is
        # unknown, and clearing a guessed rectangle is worse than saying so.
        raise AddInError(
            f"the block at {sheet}!{anchor} has a stamp but its extent "
            f"({extent!r}) is unreadable; clear the block by hand"
        ) from None
    return Block(sheet=sheet, row=row, col=col, n_rows=n_rows, n_cols=n_cols,
                 run_id=str(found["run fingerprint"]),
                 assumptions_digest=found.get("assumptions digest"),
                 results_digest=found.get("results digest"))


# --------------------------------------------------------------------------
# The add-in
# --------------------------------------------------------------------------

@dataclass
class AddIn:
    """Submit from a sheet, pull back into it, stamped.

    Two buttons' worth of behaviour: :meth:`submit` and :meth:`refresh`.
    They are separate because a projection is not a request (RFC-031) and a
    spreadsheet cannot block for one.
    """

    client: EngineClient
    book: BookPort
    sheet: str = "Results"
    anchor: str = DEFAULT_ANCHOR
    #: Injectable so a test can pin it; the pull time is context beside the
    #: numbers, not part of what identifies them — the fingerprint is that.
    clock: Callable[[], str] = _now

    def submit(self) -> dict:
        """Read the named ranges, submit, return the run summary.

        Returns immediately with the fingerprint. Submitting an unchanged
        sheet returns the same fingerprint and does no work, so this is
        cheap to press twice.
        """
        return self.client.submit(read_request(self.book))

    def refresh(self, *, run_id: str | None = None, variable: str | None = None,
                modelpoint: str | None = None, timeout: float = 300.0,
                label: str | None = None) -> Block:
        """Pull a finished run into the sheet, replacing what was there.

        With no ``run_id`` this submits the sheet first, which is the
        one-button path: the fingerprint makes it idempotent, so pressing it
        on an unchanged sheet re-pulls the same run rather than starting a
        second one.
        """
        if run_id is None:
            run_id = self.submit()["run_id"]
        summary = self.client.wait(run_id, timeout=timeout)
        payload = self.client.results(
            run_id, aggregate=modelpoint is None, variable=variable,
            modelpoint=modelpoint,
        )
        previous = read_block(self.book, self.sheet, self.anchor)
        merged = dict(summary)
        merged["results_digest"] = payload.get("results_digest",
                                               summary.get("results_digest"))
        return write_block(self.book, self.sheet, self.anchor, merged, payload,
                           pulled_at=self.clock(), previous=previous,
                           label=label)
