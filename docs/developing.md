# Developing

*Install it, run it, extend it. For the actuary rather than the engineer, see
[`user-guide.md`](user-guide.md); for the conventions, see
[`../CLAUDE.md`](../CLAUDE.md).*

---

## Install

```bash
pip install -e ".[test,data,api,excel]"
python -m pytest -q
```

The engine itself needs **only NumPy**. Everything else is an optional extra,
and that separation is load-bearing rather than tidy — it is what lets
`engine/core`, `data`, `library` and `report` run on a Python release the API
layer cannot.

| extra | brings | needed for |
|---|---|---|
| `test` | pytest, python-dateutil | the suite |
| `data` | pyarrow | the results warehouse |
| `api` | fastapi, httpx, uvicorn | REST, the UI, the worked examples |
| `excel` | openpyxl, xlwings | workbooks and the live add-in |
| `compile` | numba | the compiled executor and the bitwise measurement |
| `gpu` | cupy | the device executor |

`[compile]` pins `numpy<2.5`, so installing it holds NumPy back a minor
release. That is why it is not in the default set.

---

## Running the suite

```bash
python -m pytest -q                       # everything installed here
python -m pytest tests/test_vm22.py -q    # one module
```

Tests **skip** rather than fail when an extra is absent, with one deliberate
exception: the bitwise-boundary CI job sets `REQUIRE_COMPILE_EXTRA=1`, which
turns "compiler missing → skip" into "compiler missing → fail". A skipped
measurement reads exactly like a passing one in the summary line.

### Before you push — all four, every time

```bash
python -m pytest -q
python scripts/evidence_pack.py --out /tmp/ev1
python scripts/evidence_pack.py --out /tmp/ev2 && diff -r /tmp/ev1 /tmp/ev2
find . -name __pycache__ -exec rm -rf {} + ; python -W error::SyntaxWarning -m pytest -q
```

The evidence pack must rebuild **byte-identically** — the output directory is
named by the pack digest, so `diff -r` fails if any section moved. The cache
clear is not superstition: a `SyntaxWarning` hidden behind a stale `.pyc`
reached CI once.

### Before you merge — the whole matrix, locally

**CI triggers on a pull request into `main`, and nothing else** — there is no
push trigger, so merging runs nothing. Run this before you open the PR and you
find out on your machine instead of on a runner.

```bash
python scripts/local_matrix.py            # every job in ci.yml, every version
python scripts/local_matrix.py --list     # what would run, and on what
python scripts/local_matrix.py --job test # one job
```

This reads `.github/workflows/ci.yml` and runs each job's own commands, under
each interpreter that file names, in a throwaway virtualenv. It takes several
minutes; the four checks above take about two.

**A version it cannot find is a failure, not a footnote.** If `python3.13` is
not on your PATH the run exits non-zero and names it, because a machine with
one Python must not be able to print a report that looks like a full matrix —
the same failure shape as a parametrised test over an empty list.
`--allow-uncovered` waives *absence* only, never a real failure, and says so
in the summary next to the verdict.

**`test-arm64` is different, and reports `CI ONLY`.** On an x86 box it is not a
gap you can close — it is not runnable here at any point — so it does not fail
the run. Making it fatal would mean the local gate could never pass, and
`--allow-uncovered` would become reflexive, waiving the fixable case above
along with it. It is printed in the verdict every run instead.

It is **not CI**: one machine, one architecture, one libm. `np.exp` and `**`
are not bit-portable across microarchitectures, so a difference that lives
there is invisible here — RFC-072's correction is the worked example, an
assertion about AVX-512 dispatch that survived an entire item because it was
only ever measured in one place. See RFC-077, and §1.9a of the execution plan
for what an item verified this way may claim.

---

## Benchmarks

Not tests — they print, they do not assert, and none of them gates a commit.

```bash
python scripts/benchmark.py             # the vectorized executor
python scripts/benchmark_monthly.py     # frequency conversion
python scripts/benchmark_parallel.py    # sharding across cores
python scripts/benchmark_nested.py      # nested stochastic
python scripts/benchmark_lsmc.py        # the proxy model
python scripts/benchmark_compiled.py    # compiled vs vectorized
python scripts/benchmark_gpu.py         # the device workload
python scripts/benchmark_m2.py          # milestone M2's published claim
```

Two of them report a **split** rather than one number, deliberately.
`benchmark_compiled` separates the fused kernel from the hoist pre-pass
because they scale differently and quoting either alone would mislead.
`benchmark_gpu` prints what it could *not* measure on a machine without a
device — a benchmark that silently omitted the device row would read as
though the device were slow.

---

## Adding things

### A product template

1. `engine/library/<product>.py`, subclassing `Model`, one `@var` per
   quantity. **Indicator style**: no `if` on model-point data. A conditional
   one batch never enters is a defect that survives every test written
   against that batch.
2. Golden tests in `tests/`: closed-form or hand-computed, exact `==` where
   the mathematics is exact.
3. It joins the executor equivalence class automatically — the catalogue is
   discovered by walking `engine.library`, so a template is exposed by
   existing. Check which class it lands in
   ([architecture §3](architecture.md)); a `@pool` variable puts it in the
   block class rather than breaking the per-policy one.
4. A worked example in `engine/api/examples.py`, which is what puts it in the
   evidence pack.

### A dated regulatory set

Regulatory data is not configuration — a valuation is performed under a
*text*, and texts are amended.

1. Record the source in `docs/sources/` with full provenance: URL, retrieval
   date, digest, and how it was machine-read. Extracts, not PDFs.
2. Assert the figures in `tests/test_published_sources.py`, with shared
   fixture data behind the `published` fixture in `tests/conftest.py`.
3. Carry it as a **dated object** with its own provenance string, and
   **derive** any coverage claim from the data rather than restating it. The
   VM-22 string once said two tables were carried while seven were, and the
   test asserting it was enforcing the error.
4. A figure the text brackets is `Provisional`, so its standing travels with
   the value.

### A finding

`docs/findings/<slug>.md` plus `scripts/findings/<slug>.py`, one-to-one and
both directions enforced. The script exposes `FINDING` and `demonstrate()`
and **asserts nothing** — the claim is asserted in `tests/test_findings.py`,
because a script checking its own claim would pass while proving nothing.
Add the slug to `CATALOGUED`.

### An RFC

One per item, written first. A titled essay, a `Status:` line naming module
and test paths, a `## Summary` quoting the line it discharges, then the two or
three genuinely interesting design decisions — **not a routing inventory**.
Take the next free number.

---

## Reading the primary text

For regulatory PDFs, use **PyMuPDF** (`pip install pymupdf`) — not pdfminer or
pypdf. Find sections by **heading text**, bound the window at the next
heading, and use `page.find_tables()` rather than a regex over reading order:
a regex silently mis-assigns columns.

Calibrate the reader against something already transcribed before trusting it
on something new. `docs/sources/scripts/extract_fx_structured.py` does this by
digest and refuses to print a table if the calibration fails — a reader that
disagrees with a hand-checked transcription has demonstrated it should not be
believed about a table nothing can check it against.

Two traps that reader encodes: a chapter can reuse another's table numbers
(VM-21 and VM-22 both have a "Table 6.9"), and a table spanning pages can
share a page with the next one — so require every page to repeat its own
banner.
