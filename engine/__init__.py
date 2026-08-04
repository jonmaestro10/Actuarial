"""Open actuarial projection engine.

Phase 0: interpreted, memoized evaluation of declarative time-indexed
variable graphs. Correctness first; the vectorizing/compiling executor
replaces the interpreter in Phase 1 behind the same model definitions.
"""

from engine.core.model import Model, var
from engine.core.runner import run

__all__ = ["Model", "var", "run"]
