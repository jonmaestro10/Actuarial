"""Content addressing: a stable digest of whatever went into a run.

PLAN.md §2.3 makes reproducibility the backbone of the accuracy story — a
run pins exact versions of model code, assumptions and inputs. That needs a
digest with three properties, and the awkward one is the third:

1. **Deterministic across processes.** Python randomises string hashing per
   interpreter, so ``hash()`` is unusable; everything here goes through a
   canonical byte encoding into BLAKE2b. tests/test_registry.py checks this
   from a subprocess with a different ``PYTHONHASHSEED``.
2. **Structural.** Same content, same digest — regardless of object
   identity, dict insertion order, or which run built it.
3. **Total, or an error.** An encoder that quietly skips what it does not
   recognise produces a digest that certifies less than it appears to. This
   one raises. A fingerprint you cannot trust is worse than none, because it
   invites you to stop checking.

Objects opt in by defining ``__fingerprint__()``, returning whatever
actually defines them. That is deliberate rather than reflective: a
``MortalityBasis`` carries lookup caches whose contents depend on which
calendar years have been asked for, and hashing those would make an
assumption set's identity depend on its evaluation history. Stating what
identifies an object is also the more honest documentation of it.

Floats are hashed by their bits, so ``-0.0`` and ``0.0`` differ even though
they compare equal and would project identically. That is the conservative
direction: a spurious difference is a false alarm, a missed one is a wrong
audit trail.
"""

from __future__ import annotations

import hashlib
import inspect
import struct
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

DIGEST_BYTES = 16

# Type tags keep structurally different values apart: without them the list
# [1, 2] and the tuple (1, 2) — or the string "12" — could collide.
_NONE = b"\x00"
_FALSE = b"\x01"
_TRUE = b"\x02"
_INT = b"\x03"
_FLOAT = b"\x04"
_STR = b"\x05"
_BYTES = b"\x06"
_LIST = b"\x07"
_TUPLE = b"\x08"
_DICT = b"\x09"
_SET = b"\x0a"
_ARRAY = b"\x0b"
_DATE = b"\x0c"
_DATETIME = b"\x0d"
_OBJECT = b"\x0e"
_CALLABLE = b"\x0f"
_RANGE = b"\x10"


class UnfingerprintableError(TypeError):
    """Raised for a value the encoder cannot represent faithfully.

    Deliberately fatal. The alternative — skipping the value — yields a
    digest that looks authoritative while certifying nothing about the part
    it dropped.
    """


def _write(out: bytearray, tag: bytes, payload: bytes) -> None:
    out += tag
    out += struct.pack("<Q", len(payload))
    out += payload


def _encode(value: Any, out: bytearray) -> None:
    if value is None:
        out += _NONE
        return
    if value is True or value is False:
        out += _TRUE if value else _FALSE
        return
    if isinstance(value, (int, np.integer)):
        _write(out, _INT, str(int(value)).encode())
        return
    if isinstance(value, (float, np.floating)):
        _write(out, _FLOAT, struct.pack("<d", float(value)))
        return
    if isinstance(value, str):
        _write(out, _STR, value.encode("utf-8"))
        return
    if isinstance(value, (bytes, bytearray)):
        _write(out, _BYTES, bytes(value))
        return
    if isinstance(value, datetime):
        _write(out, _DATETIME, value.isoformat().encode())
        return
    if isinstance(value, date):
        _write(out, _DATE, value.isoformat().encode())
        return
    if isinstance(value, range):
        _write(out, _RANGE, f"{value.start}:{value.stop}:{value.step}".encode())
        return
    if isinstance(value, np.ndarray):
        contiguous = np.ascontiguousarray(value)
        if contiguous.dtype == object:
            # An object array is a sequence of things we still have to encode.
            _write(out, _ARRAY, b"object")
            _encode(list(contiguous.ravel()), out)
            _encode(contiguous.shape, out)
            return
        _write(out, _ARRAY, str(contiguous.dtype).encode())
        _encode(contiguous.shape, out)
        _write(out, _BYTES, contiguous.tobytes())
        return
    if isinstance(value, (list, tuple)):
        tag = _LIST if isinstance(value, list) else _TUPLE
        _write(out, tag, struct.pack("<Q", len(value)))
        for item in value:
            _encode(item, out)
        return
    if isinstance(value, (Mapping, MappingProxyType)):
        items = [(_digest_of(k), k, v) for k, v in value.items()]
        items.sort(key=lambda row: row[0])
        _write(out, _DICT, struct.pack("<Q", len(items)))
        for _, key, item in items:
            _encode(key, out)
            _encode(item, out)
        return
    if isinstance(value, (set, frozenset)):
        digests = sorted(_digest_of(item) for item in value)
        _write(out, _SET, struct.pack("<Q", len(digests)))
        for digest in digests:
            _write(out, _BYTES, digest)
        return
    if hasattr(value, "__fingerprint__"):
        _write(out, _OBJECT, _qualified(type(value)).encode())
        _encode(value.__fingerprint__(), out)
        return
    if inspect.isclass(value) or inspect.isfunction(value):
        _write(out, _CALLABLE, _qualified(value).encode())
        _write(out, _BYTES, source_digest(value))
        return
    if hasattr(value, "__dict__"):
        _write(out, _OBJECT, _qualified(type(value)).encode())
        _encode(dict(vars(value)), out)
        return
    raise UnfingerprintableError(
        f"cannot fingerprint {type(value).__name__}: give it a "
        f"__fingerprint__() returning what defines it"
    )


def _qualified(obj) -> str:
    return f"{getattr(obj, '__module__', '?')}.{getattr(obj, '__qualname__', obj)}"


def _digest_of(value: Any) -> bytes:
    out = bytearray()
    _encode(value, out)
    return hashlib.blake2b(bytes(out), digest_size=DIGEST_BYTES).digest()


def fingerprint(value: Any) -> str:
    """Hex digest of ``value``'s content."""
    return _digest_of(value).hex()


def source_digest(target) -> bytes:
    """Digest of a class's source, including the bases it customises.

    A model's behaviour is its formulas, so a fingerprint that ignored the
    source would call two different products the same run. Bases are walked
    so that a template inheriting most of its logic still changes when that
    logic does.

    This is a best effort and is documented as one: it cannot see
    module-level helpers a formula calls, and it cannot read source at all
    for a class defined interactively. Pinning the git commit alongside it
    is the belt to this braces — which is what ``RunRecord`` does.
    """
    parts: list[bytes] = []
    targets = (
        inspect.getmro(target) if inspect.isclass(target) else [target]
    )
    for item in targets:
        if item in (object,) or getattr(item, "__module__", "") == "builtins":
            continue
        try:
            parts.append(inspect.getsource(item).encode("utf-8"))
        except (OSError, TypeError):
            parts.append(f"<source unavailable: {_qualified(item)}>".encode())
    return hashlib.blake2b(b"\x00".join(parts), digest_size=DIGEST_BYTES).digest()


def source_fingerprint(target) -> str:
    return source_digest(target).hex()
