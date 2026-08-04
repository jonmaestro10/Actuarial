"""Open actuarial projection engine.

Phase 0: interpreted, memoized evaluation of declarative time-indexed
variable graphs. Correctness first; the vectorizing/compiling executor
replaces the interpreter in Phase 1 behind the same model definitions.
"""

from engine.core.model import Model, pool, var
from engine.core.runner import run
from engine.core.stochastic import run_stochastic
from engine.core.vector import run_vectorized

__all__ = ["Model", "var", "pool", "run", "run_vectorized", "run_stochastic"]
