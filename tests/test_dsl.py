"""Core DSL mechanics: memoization, domains, instance isolation."""

import pytest

from engine.core.model import Model, var
from engine.core.results import stable_sum


class Counter(Model):
    calls = 0

    @var
    def fib_like(self, t):
        type(self).calls += 1
        if t <= 1:
            return 1.0
        return self.fib_like(t - 1) + self.fib_like(t - 2)


def make(proj_len=10):
    Counter.calls = 0
    return Counter(mp=None, assumptions=None, proj_len=proj_len)


def test_memoization_evaluates_each_point_once():
    m = make(20)
    m.fib_like(20)
    assert Counter.calls == 21  # t = 0..20, once each


def test_cache_isolated_between_instances():
    m1 = make(5)
    assert m1.fib_like(5) == 8.0
    m2 = Counter(mp=None, assumptions=None, proj_len=5)
    assert m2.fib_like(5) == 8.0
    assert m1._cache is not m2._cache


def test_series_matches_pointwise_eval():
    m = make(10)
    assert m.series("fib_like") == [m.fib_like(t) for t in range(11)]


def test_long_projection_no_recursion_blowup():
    m = make(5000)
    assert len(m.series("fib_like")) == 5001


def test_out_of_range_t_raises():
    m = make(10)
    with pytest.raises(IndexError):
        m.fib_like(11)
    with pytest.raises(IndexError):
        m.fib_like(-1)
    with pytest.raises(TypeError):
        m.fib_like(1.5)


def test_var_names_discovered():
    assert Counter.var_names() == ["fib_like"]


def test_stable_sum_beats_naive_on_ill_conditioned_input():
    values = [1e16, 1.0, -1e16] * 1000
    assert stable_sum(values) == 1000.0
