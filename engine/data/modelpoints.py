"""Model point containers.

Phase 0: a validated attribute bag plus a dict loader. Parquet/Arrow I/O
replaces the loader in Phase 1 without changing model code.
"""

from __future__ import annotations

from typing import Iterable


class ModelPoint:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __repr__(self):
        inner = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"ModelPoint({inner})"


def from_dicts(rows: Iterable[dict]) -> list[ModelPoint]:
    return [ModelPoint(**row) for row in rows]
