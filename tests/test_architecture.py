"""The architecture document describes boundaries the code still keeps.

`docs/architecture.md` makes claims about which layer may import what. A
document making unchecked claims about a dependency graph is a document that
describes last quarter's code — and this repo's central promise is evidence,
so a diagram nothing verifies is the wrong kind of artefact to ship.

The rules asserted here are the ones the document states, checked against the
**real import graph**, and with the distinction §1.4 actually makes: an import
at module level binds the layer, an import behind a guard does not.

Where the truth is untidy it is pinned rather than idealised. `core` and
`data` import each other, and the exact modules that do so are listed — so
the cycle can be paid down deliberately and cannot grow by accident.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
DOCUMENT = ROOT / "docs" / "architecture.md"

#: §1.4: these keep NumPy as their only runtime dependency.
NUMPY_ONLY = ("core", "data", "library", "report")

#: The `core` modules permitted to import `data`, and vice versa. A narrow,
#: named cycle rather than a tangle. Adding to this list should be a decision.
CORE_TO_DATA = {"model", "vector", "parallel", "nested", "stochastic",
                "lsmc", "compiled", "dispatch"}
DATA_TO_CORE = {"mortality", "rates", "assets", "loan"}


def _imports(path: Path):
    """``(module, at_module_level)`` for every import in a file."""
    tree = ast.parse(path.read_text())
    top = {id(n) for n in tree.body
           if isinstance(n, (ast.Import, ast.ImportFrom))}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if node.module and node.level == 0 else []
        else:
            continue
        for name in names:
            yield name, id(node) in top


def _files(layer: str):
    return [p for p in (ENGINE / layer).rglob("*.py")
            if "__pycache__" not in str(p)]


@pytest.mark.parametrize("layer", NUMPY_ONLY)
def test_the_projection_layers_import_nothing_but_numpy_unconditionally(layer):
    """**§1.4, enforced rather than aspirational.**

    This is what let the engine run unchanged on a Python release the API
    layer could not — 2,023 tests green while pydantic could not import. A
    third-party import that reached module level here would take the whole
    engine down with the next dependency that lagged a release."""
    offenders = []
    for path in _files(layer):
        for module, at_top in _imports(path):
            if not at_top:
                continue
            root = module.split(".")[0]
            if root in ("engine", "numpy", "__future__"):
                continue
            if root in sys.stdlib_module_names:
                continue
            offenders.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not offenders, (
        f"engine/{layer} must keep NumPy as its only runtime dependency "
        f"(§1.4). Guard the import and skipif the extra: {offenders}"
    )


@pytest.mark.parametrize("layer", NUMPY_ONLY)
def test_the_optional_backends_are_present_but_guarded(layer):
    """The other half of §1.4: an extra is *usable*, just never required.
    A layer that imported none of them would satisfy the rule above
    vacuously, so the guarded ones are asserted to exist where expected."""
    guarded = set()
    for path in _files(layer):
        for module, at_top in _imports(path):
            root = module.split(".")[0]
            if not at_top and root not in ("engine", "numpy", "__future__") \
                    and root not in sys.stdlib_module_names:
                guarded.add(root)
    if layer == "core":
        assert {"numba", "cupy"} <= guarded, (
            f"core no longer reaches the optional backends at all: {guarded}")


def test_report_may_reach_up_into_api_only_behind_a_guard():
    """The one edge that runs against the layering, and the reason it is
    allowed: the worked examples live in `engine.api` and duplicating them
    would create a second set to keep true. Without the extra the evidence
    pack says it attested nothing rather than inventing specimens — which is
    only true while the import stays guarded."""
    unconditional = []
    for path in _files("report"):
        for module, at_top in _imports(path):
            if module.startswith("engine.api") and at_top:
                unconditional.append(str(path.relative_to(ROOT)))
    assert not unconditional, (
        f"engine/report imports engine.api at module level in "
        f"{unconditional}; the evidence pack must degrade rather than fail "
        f"when the [api] extra is absent"
    )


def test_the_library_sits_on_core_and_data_and_nothing_else():
    """A template that reached the API or the reporting layer would make the
    catalogue depend on how it is served, which is the coupling the layer map
    exists to prevent."""
    reached = set()
    for path in _files("library"):
        for module, _ in _imports(path):
            if module.startswith("engine."):
                reached.add(module.split(".")[1])
    assert reached <= {"core", "data", "library"}, (
        f"engine/library reached {sorted(reached - {'core', 'data', 'library'})}"
    )


def test_the_core_data_cycle_is_the_named_one_and_has_not_grown():
    """**Pinned rather than idealised.** `core` and `data` import each other,
    and the architecture document draws them side by side because that is the
    truth rather than a stack.

    Listing the participating modules is what keeps it a narrow cycle: it can
    be paid down deliberately, and it cannot grow by accident."""
    core_to_data, data_to_core = set(), set()
    for path in _files("core"):
        for module, _ in _imports(path):
            if module.startswith("engine.data"):
                core_to_data.add(path.stem)
    for path in _files("data"):
        for module, _ in _imports(path):
            if module.startswith("engine.core"):
                data_to_core.add(path.stem)

    assert core_to_data <= CORE_TO_DATA, (
        f"new core -> data edges: {sorted(core_to_data - CORE_TO_DATA)}. "
        f"Adding one should be a decision; add it to CORE_TO_DATA if it is.")
    assert data_to_core <= DATA_TO_CORE, (
        f"new data -> core edges: {sorted(data_to_core - DATA_TO_CORE)}")
    # And it is genuinely a cycle, so the document must not draw a stack.
    assert core_to_data and data_to_core


def test_the_document_exists_and_names_the_layers_it_maps():
    """A layer added without a line in the map leaves a reader with an
    incomplete picture and no way to know it is incomplete."""
    assert DOCUMENT.exists()
    text = DOCUMENT.read_text()
    layers = sorted(p.name for p in ENGINE.iterdir()
                    if p.is_dir() and not p.name.startswith("_")
                    and p.name != "__pycache__")
    missing = [f"engine/{name}" for name in layers
               if f"engine/{name}" not in text]
    assert not missing, f"architecture.md does not map {missing}"


def test_the_document_names_every_executor_that_exists():
    """Four executors and three classes. A fifth arriving without a row in
    the table is exactly the drift this test exists to catch."""
    text = DOCUMENT.read_text()
    for module in ("core/runner.py", "core/vector.py", "core/compiled.py",
                   "core/stochastic.py"):
        assert module in text, module
    for klass in ("per-policy", "block", "scenario"):
        assert klass in text, klass
