"""Re-extract VM-22 Tables 6.9, 6.10 and 6.11 from the Valuation Manual PDF.

Standalone. Nothing here is imported by the repo, and it takes no repo
imports itself, so it can be pointed at a later edition without carrying
this one's assumptions along.

    pip install pymupdf
    curl -sSLo vm.pdf https://content.naic.org/sites/default/files/pbr_data_valuation_manual_current_edition.pdf
    python docs/sources/scripts/extract_fx_structured.py vm.pdf

What it prints: the calibration against the two already-carried Fx tables,
then each structured-settlement table as Python tuples in the form
`engine/report/vm22_prescribed.py` stores them, then a second independent
read for cross-check.

**Why it does the calibration first.** Tables 6.7 and 6.8 sit three pages
earlier, are already transcribed in the module, and are read by the same code
path. If that path reproduces them cell for cell it has earned the right to
be believed on a table nothing can check it against. This is what caught a
running-text regex disagreeing with the carried Table 6.7 at ages 64, 68, 69,
76 and 83 while the coordinate reader matched exactly.

**Why every page is checked for its own banner.** Each of these tables spans
four PDF pages, and two of those pages carry the *tail* of one table and the
*head* of the next. A reader that takes whole pages attributes 25 rows to the
wrong table — which is not a hypothetical: the first version of the
cross-check below did exactly that, and the disagreement is how it was found.

Locations are resolved by heading text, never by page number, because
**VM-21 has its own Tables 6.5 to 6.9**. In the 2026 edition its "Table 6.7:
Standard Table B for Hybrid GMIB Annuitization" is on PDF page 177 and its
Table 6.9 — a different Fx entirely, for variable annuities with guaranteed
living benefits — is on 179. The full heading is what tells them apart.
"""
from __future__ import annotations

import hashlib
import re
import sys
from collections import defaultdict

import fitz  # PyMuPDF

PCT = re.compile(r"^(\d+(?:\.\d+)?)%$")
AGE = re.compile(r"^(?:<=|≤)(\d+)$|^(?:>=|≥)(\d+)$|^(\d+)$")

# (short key, full heading, banner, bands, value columns, constant name)
STRUCTURED = [
    ("Table 6.9:",
     "Table 6.9: Fx for Structured Settlement Contracts with Standard lives",
     "Structured Settlements – Standard Lives",
     ["Contract Years 1 to 5", "Contract Years 6 to 10",
      "Contract Years ≥11"],
     6, "_FX_SS_STANDARD"),
    ("Table 6.10:",
     "Table 6.10: Fx for Structured Settlement Contracts for Substandard "
     "lives with age rate-ups of 1-20 years",
     "Structured Settlements – Substandard Lives, Rate-Ups 1-20 Years",
     ["Contract Years 1 to 10", "Contract Years 11 to 20",
      "Contract Years 21 to 30", "Contract Years ≥31"],
     8, "_FX_SS_SUBSTANDARD_1_20"),
    ("Table 6.11:",
     "Table 6.11: Fx for Structured Settlement Contracts for Substandard "
     "lives with age rate-ups of ≥21 years",
     "Structured Settlements – Substandard Lives, Rate-Ups ≥21 Years",
     ["Contract Years 1 to 10", "Contract Years 11 to 20",
      "Contract Years 21 to 30", "Contract Years ≥31"],
     8, "_FX_SS_SUBSTANDARD_21_PLUS"),
]

# Tables 6.7 and 6.8, for calibration only.
CARRIED = [
    ("Table 6.7:",
     "Table 6.7: Fx for Individual Annuities in Accumulation Reserving "
     "Category", 4),
    ("Table 6.8:",
     "Table 6.8: Fx for Individual Annuities in Payout Annuity Reserving "
     "Category", 2),
]


#: SHA-256 (first 16 hex) of Tables 6.7 and 6.8 as this script extracts them,
#: over the same rows and in the same form ``vm22_prescribed.py`` stores —
#: cap row excluded, factors as fractions rounded to 4 places. The module's
#: `_FX_ACCUMULATION` and `_FX_PAYOUT` hash to exactly these, so a run that
#: reproduces them has demonstrated the reader agrees with a transcription
#: that was checked by hand. A run that does not must be believed about
#: nothing else.
CALIBRATION_DIGESTS = {
    "Table 6.7:": "0fa3da345ac0925c",
    "Table 6.8:": "5ba42513de0f2dbf",
}


def clean(cell) -> str:
    return re.sub(r"\s+", " ", cell or "").strip()


def digest(rows) -> str:
    body = tuple((age, *[round(v, 4) for v in vals])
                 for label, age, vals in rows
                 if not label.startswith((">=", "≥")))
    return hashlib.sha256(repr(body).encode()).hexdigest()[:16]


def find_heading(doc, key, title):
    """(page, y) of the sole occurrence of a table heading.

    ``key`` is the short label ("Table 6.9:") — a long title wraps, and
    ``search_for`` returns one rectangle per line for a wrapped match, so
    searching the whole title counts a single heading several times.
    ``title`` then disambiguates: "Table 6.7:" occurs twice in the 2026
    edition, at chapter page 22-36 and on PDF page 177, which is a different
    chapter entirely and carries a different heading beneath the same label.
    """
    hits = [(p, r.y0) for p in range(doc.page_count)
            for r in doc[p].search_for(key)
            if re.sub(r"\s+", " ", doc[p].get_text()).find(title) >= 0]
    if len(hits) != 1:
        raise SystemExit(f"heading {key!r} ({title!r}) found {len(hits)} "
                         f"times, not once")
    return hits[0]


def numeric_rows(doc, first_page, ncols, banner=None, bands=None,
                 stop=None):
    """Rows of one table, page by page, each page checked to be that table.

    ``stop`` is the (page, y) of the next table's heading; rows at or below
    it on that page belong to the next table and are not this one's.
    """
    rows, page = [], first_page
    while page < doc.page_count:
        tables = doc[page].find_tables()
        taken = 0
        for tb in tables:
            extracted = [[clean(c) for c in r] for r in tb.extract()]
            if len(extracted[0]) != ncols + 1:
                continue
            if banner is not None:
                if extracted[0][1] != banner:
                    continue
                if any(c for c in extracted[0][2:]):
                    raise SystemExit(f"page {page}: banner does not span")
                if [c for c in extracted[1][1:]] != [
                        v for b in bands for v in (b, "")]:
                    raise SystemExit(f"page {page}: bands {extracted[1]}")
                if extracted[2][1:] != ["Female", "Male"] * len(bands):
                    raise SystemExit(f"page {page}: sexes {extracted[2]}")
            if stop is not None and page == stop[0] and tb.bbox[1] >= stop[1]:
                continue
            for r in extracted:
                m = AGE.match(r[0]) if r else None
                if not m or not all(PCT.match(c) for c in r[1:]):
                    continue
                age = int(m.group(1) or m.group(2) or m.group(3))
                rows.append((r[0], age,
                             [float(PCT.match(c).group(1)) / 100.0
                              for c in r[1:]]))
                taken += 1
        if not taken:
            break
        page += 1
    return rows


def check_contiguous(name, rows):
    labels = [r[0] for r in rows]
    ages = [r[1] for r in rows]
    floors = [l for l in labels if l.startswith(("<=", "≤"))]
    caps = [l for l in labels if l.startswith((">=", "≥"))]
    if len(floors) != 1 or len(caps) != 1:
        raise SystemExit(f"{name}: {len(floors)} floor rows, {len(caps)} caps")
    if ages != list(range(ages[0], ages[-1] + 1)):
        raise SystemExit(f"{name}: ages are not contiguous")
    if any(v != 1.0 for v in rows[-1][2]):
        raise SystemExit(f"{name}: cap row is not 100%")
    return ages[0], ages[-1]


def by_word_position(doc, first_page, ncols, start_y, stop):
    """A second reading that never consults ``find_tables``' grid.

    Words are clustered by their y coordinate and sorted by x. Independent of
    the ruled grid, and therefore an actual second opinion rather than the
    same opinion twice.
    """
    out, page = {}, first_page
    while page < doc.page_count:
        if stop is not None and page > stop[0]:
            break          # past the next table's first page; those rows are
        lines = defaultdict(list)     # the next table's, at the same width
        for x0, y0, _x1, _y1, word, *_ in doc[page].get_text("words"):
            lo = start_y if page == first_page else 0.0
            hi = stop[1] if (stop and page == stop[0]) else float("inf")
            if lo <= y0 < hi:
                lines[round(y0, 1)].append((x0, word))
        found = 0
        for y in sorted(lines):
            words = [w for _, w in sorted(lines[y])]
            if len(words) != ncols + 1:
                continue
            m, vals = AGE.match(words[0]), [PCT.match(w) for w in words[1:]]
            if m and all(vals):
                out[int(m.group(1) or m.group(2) or m.group(3))] = [
                    float(v.group(1)) / 100.0 for v in vals]
                found += 1
        if not found:
            break
        page += 1
    return out


def main(path):
    doc = fitz.open(path)
    print(f"{path}: {doc.page_count} pages, "
          f"{doc.metadata.get('title', '')!r}\n")

    print("calibration against the tables already carried")
    for key, heading, ncols in CARRIED:
        page, _ = find_heading(doc, key, heading)
        rows = numeric_rows(doc, page, ncols)
        lo, hi = check_contiguous(key, rows)
        got = digest(rows)
        want = CALIBRATION_DIGESTS[key]
        if got != want:
            raise SystemExit(
                f"{key} extracted as {got}, not {want} — this reader "
                f"disagrees with a transcription that was checked by hand, "
                f"so nothing below it can be trusted. Either the reader is "
                f"wrong or the edition has changed; find out which."
            )
        print(f"  {key} {len(rows)} rows, ages {lo}-{hi}, {ncols} columns, "
              f"digest {got} — matches the carried transcription")
    print()

    headings = [find_heading(doc, k, h) for k, h, *_ in STRUCTURED]
    for i, (_key, heading, banner, bands, ncols, const) in enumerate(
            STRUCTURED):
        page, y = headings[i]
        stop = headings[i + 1] if i + 1 < len(headings) else None
        rows = numeric_rows(doc, page, ncols, banner, bands, stop)
        lo, hi = check_contiguous(const, rows)
        cross = by_word_position(doc, page, ncols, y, stop)
        disagree = [a for _, a, v in rows
                    if a in cross and
                    [round(x, 6) for x in cross[a]] != [round(x, 6) for x in v]]
        missing = sorted({a for _, a, _ in rows} - set(cross))
        if disagree or missing:
            raise SystemExit(
                f"{const}: the two readings do not agree — disagreements "
                f"{disagree}, missing {missing}. Do not transcribe this."
            )
        print(f"# {heading}")
        print(f"# ages {lo} to {hi}, {ncols} value columns, {len(rows)} rows; "
              f"word-position cross-check: {len(cross)} ages read, "
              f"disagreements {disagree or 'none'}, missing "
              f"{missing or 'none'}")
        print(f"{const} = (")
        for label, age, vals in rows:
            if label.startswith((">=", "≥")):
                continue          # the cap row is the code's, not the data's
            print(f"    ({age}, " + ", ".join(f"{v:.4f}" for v in vals) + "),")
        print(")\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "vm.pdf")
