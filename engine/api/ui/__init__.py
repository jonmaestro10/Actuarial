"""The demonstration page's assets, and how they reach the client.

Three files, read off the package and served by name. No build step, no
bundler, no package manager, and nothing fetched from a network at render
time — the page runs from the same install as the engine and works with no
route out of the machine.

That is a constraint rather than a preference. This repository has one
runtime dependency, and the honest comparison it invites is between a
projection engine and a projection engine; adding a JavaScript toolchain to
show one off would make the largest thing in the tree the part that draws
the charts. So the charts are drawn by hand, in SVG, in a file you can
read.

The cost is real and worth naming: no framework means the page is a few
hundred lines of DOM code, and a graph laid out by hand is not a graph laid
out by Graphviz. What is bought is that ``pip install -e ".[api]"`` is the
whole install, and that the page has no supply chain.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).parent

#: The assets ``/ui/{asset}`` will serve, and their content types. A fixed
#: set rather than a directory listing — see :func:`read_asset`.
UI_FILES: dict = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}


def media_type(asset: str) -> str:
    """The content type for a known asset."""
    return UI_FILES[asset]


def read_asset(asset: str) -> str:
    """One asset's text, by name.

    The name is checked against :data:`UI_FILES` before it touches a path,
    so ``../../core/model.py`` is a ``KeyError`` here rather than a source
    disclosure there. Read per request rather than cached at import: the
    files are a few kilobytes, and editing one and reloading the page is
    how the page gets written.
    """
    if asset not in UI_FILES:
        raise KeyError(asset)
    return (_HERE / asset).read_text(encoding="utf-8")
