#!/usr/bin/env python3
r"""A number moved and the changelog did not: the one review a diff cannot do.

RFC-080. §3.5 asks that every change to a numeric result carry an
expected-change note, and the reason is that a code diff and a numeric
consequence are not the same information. A reviewer looking at
``rate = 0.0415`` where ``0.0410`` used to be can see the edit and cannot see
what it did to a reserve. Only the author knows that, and only at the moment
of making it.

So this gate asks one question: **did a golden expected value move without
``CHANGELOG.md`` moving too?**

What counts as a golden value, exactly
--------------------------------------
A numeric literal on a changed line in ``tests/``. Not "a changed test" — the
suite is edited constantly for reasons that move nothing — and not "a changed
line in ``engine/``", because the engine changing is the ordinary case and the
question is whether the *answers* changed.

That is a heuristic and it is stated as one. It errs toward asking: renaming a
variable on a line that happens to contain ``1e-12`` will trip it. That is the
right direction to err, and the escape is to write the note, which costs a
sentence — ``No expected change`` is a legitimate and useful sentence, because
it records that the author considered the question.

It does **not** catch a golden value that moved without any test file being
touched, which happens when a fixture in ``tests/conftest.py`` feeds a computed
expectation. Nothing textual can catch that; the suite failing is what catches
it, and then the fix touches a test line and this gate sees it.

Usage::

    python scripts/changelog_gate.py --base origin/main
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = "CHANGELOG.md"

#: A numeric literal with a decimal point or an exponent. Bare integers are
#: excluded deliberately: `range(5)`, `proj_len=20` and an index are integers,
#: and a gate that fired on every one of them would fire on everything and be
#: turned off within a week.
_NUMERIC = re.compile(r"(?<![\w.])\d+\.\d+(?:[eE][-+]?\d+)?(?![\w.])"
                      r"|(?<![\w.])\d+[eE][-+]?\d+(?![\w.])")

#: Lines a unified diff uses for context and metadata rather than content.
_METADATA = ("+++", "---", "@@", "diff ", "index ", "new file", "deleted file",
             "similarity ", "rename ")


class GateError(RuntimeError):
    """The gate could not be evaluated, which is not the same as passing."""


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise GateError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def changed_files(base: str) -> list[str]:
    """Paths that differ from ``base``."""
    return [line for line in _git("diff", "--name-only", f"{base}...HEAD",
                                  ).splitlines() if line]


def moved_numbers(diff: str) -> dict:
    """Test files whose changed lines carry a numeric literal, and which.

    Both sides of the diff are read. A golden value that was *removed* is as
    much a moved number as one that was added — deleting the assertion that
    pinned a reserve changes what the suite guarantees.
    """
    found: dict = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if any(line.startswith(prefix) for prefix in _METADATA):
            continue
        if not line or line[0] not in "+-":
            continue
        if current is None or not current.startswith("tests/"):
            continue
        if not current.endswith(".py"):
            continue
        literals = _NUMERIC.findall(line[1:])
        if literals:
            found.setdefault(current, set()).update(literals)
    return {path: sorted(values) for path, values in sorted(found.items())}


def evaluate(base: str) -> tuple[bool, str]:
    """``(ok, message)``. Never raises for an ordinary failing diff."""
    files = changed_files(base)
    if not files:
        return True, f"no changes against {base}"

    diff = _git("diff", "--unified=0", f"{base}...HEAD", "--", "tests/")
    moved = moved_numbers(diff)
    if not moved:
        return True, "no golden expected value changed"
    if CHANGELOG in files:
        total = sum(len(v) for v in moved.values())
        return True, (f"{total} numeric literal(s) changed across "
                      f"{len(moved)} test file(s), and {CHANGELOG} was "
                      f"updated")

    detail = "\n".join(f"    {path}: {', '.join(values[:6])}"
                       + (" …" if len(values) > 6 else "")
                       for path, values in moved.items())
    return False, (
        f"golden expected values changed and {CHANGELOG} did not:\n{detail}\n"
        f"\n"
        f"A code diff shows what changed; it cannot show what a reserve did in\n"
        f"response, and a reviewer cannot derive one from the other. Add an\n"
        f"entry under 'Expected change to numbers'. 'No expected change' is a\n"
        f"legitimate entry — it records that the question was considered.\n"
        f"\n"
        f"If the literal is incidental (a renamed variable on a line that\n"
        f"happens to contain a tolerance), say so in the note. This gate errs\n"
        f"toward asking, on purpose."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main",
                        help="the ref to diff against (default: origin/main)")
    args = parser.parse_args(argv)

    try:
        ok, message = evaluate(args.base)
    except GateError as exc:
        # Refusing rather than passing. A gate that cannot see the base ref —
        # a shallow clone, a missing remote — would otherwise report "no
        # golden value changed" about a comparison it never made, which is
        # indistinguishable from a clean run.
        print(f"changelog gate could not run: {exc}", file=sys.stderr)
        print("This is not a pass. Fetch the base ref (actions/checkout with "
              "fetch-depth: 0) and try again.", file=sys.stderr)
        return 2

    print(("changelog gate: " if ok else "changelog gate FAILED: ") + message,
          file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
