"""Write model documentation for the library.

    python scripts/model_docs.py [outdir]

One Markdown file per template that can be traced from a generic model
point, plus a coverage summary over every template the library ships —
including the ones needing a product-specific model point, whose docstrings
are static and so can be counted without running anything.

PLAN §7 wants this "git-native": the output is Markdown so that a change to
a formula shows up as a diff in a pull request rather than as a different
picture in a viewer nobody opened.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path

import engine.library as library
from engine.core.model import Model
from engine.core.modeldoc import document, graph_is_settled, library_coverage
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint

TRACE_LENGTH = 3
LONG_TRACE = 12

ASSUMPTIONS = Assumptions(mortality=MortalityTable.flat(0.01), lapse=0.05,
                          interest=0.03)
POINT = ModelPoint(id="DOC", age_at_entry=40, term_years=20,
                   sum_assured=250_000.0, annual_premium=1_200.0, init_pols=1)


def templates():
    """Every ``Model`` subclass the library ships."""
    for module_info in pkgutil.iter_modules(library.__path__):
        module = importlib.import_module(f"engine.library.{module_info.name}")
        for cls in vars(module).values():
            if (inspect.isclass(cls) and issubclass(cls, Model)
                    and cls is not Model
                    and cls.__module__ == module.__name__
                    and cls.var_names()):
                yield cls


def main(outdir: str = "docs/models") -> int:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for cls in templates():
        try:
            graph = cls.trace(POINT, ASSUMPTIONS, proj_len=TRACE_LENGTH)
            settled = graph_is_settled(cls, POINT, ASSUMPTIONS,
                                       short=TRACE_LENGTH, long=LONG_TRACE)
        except Exception as exc:  # needs a product-specific model point
            skipped.append((cls.__name__, type(exc).__name__))
            continue
        doc = document(cls, graph, trace_length=TRACE_LENGTH, settled=settled)
        path = out / f"{cls.__name__}.md"
        path.write_text(doc.to_markdown(), encoding="utf-8")
        written.append((cls.__name__, doc.coverage, settled))

    for name, coverage, settled in written:
        flag = "" if settled else "   GRAPH NOT SETTLED"
        print(f"  {name:32s} {coverage:6.1%} documented{flag}")
    for name, why in skipped:
        print(f"  {name:32s}   skipped: needs a product-specific point ({why})")

    coverage = library_coverage(*templates())
    covered, total = coverage["TOTAL"]
    print(f"\n  {len(written)} written to {out}, {len(skipped)} skipped")
    print(f"  docstring coverage over all templates: "
          f"{covered}/{total} = {covered / total:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
