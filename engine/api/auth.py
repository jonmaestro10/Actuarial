"""Who is asking, and what they are allowed to ask for.

RFC-043. The engine is a library first, and a library has no principals —
the code that imports it *is* the principal. So authentication here is a
property of the **deployment**, not of the engine: with no principals file
configured the API behaves exactly as it did before this module existed, and
configuring one turns every route into a route with a role requirement.

Three decisions worth stating.

**Tokens are minted, not chosen.** :func:`mint_token` produces 32 bytes from
:mod:`secrets`; the principals file stores the SHA-256 of that and never the
token itself. Because the token is high-entropy random, a plain hash is the
right primitive and a password-hashing KDF would be theatre — the reason
KDFs exist is that humans pick guessable secrets. A deployment that wants to
let humans pick their own is a deployment that needs a different module and
should say so out loud.

**No role implies another.** ``admin`` does not confer ``viewer``, and
``runner`` does not confer either. A role ladder is a convenience that turns
into a privilege escalation the first time somebody adds a rung in the
middle; a principal that needs to read runs and submit them carries both
roles, in the file, where an auditor can see it.

**Identity is deployed, not edited over HTTP.** There is a route to *read*
the principal list and none to change it. The file is configuration — it
arrives through whatever change process the rest of the deployment uses,
which is the process that already has an audit trail — and an API that could
rewrite its own access control is an API one bug away from granting itself
the roles it likes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from fastapi import HTTPException, Request
except ImportError as exc:  # pragma: no cover - exercised by the extra
    raise ImportError(
        "the REST API needs FastAPI: pip install -e '.[api]'"
    ) from exc


class Role(str, Enum):
    """What a principal may do.

    ``approver`` grants nothing today: the routes it exists for arrive with
    RFC-044's four-eyes approval. It is defined now so that a deployment can
    write its principals file once, and so that the shape of the governance
    model is visible before the workflow that uses it.
    """

    VIEWER = "viewer"
    RUNNER = "runner"
    APPROVER = "approver"
    ADMIN = "admin"


class PrincipalsError(ValueError):
    """A principals file that cannot be trusted to say who anyone is."""


def token_digest(token: str) -> str:
    """The stored form of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token(nbytes: int = 32) -> str:
    """A new bearer token: 32 bytes of ``secrets``, URL-safe."""
    return secrets.token_urlsafe(nbytes)


@dataclass(frozen=True)
class Principal:
    """One identity and the roles it carries."""

    name: str
    roles: frozenset[Role]

    def has(self, *roles: Role) -> bool:
        """Does this principal carry **every** role named?"""
        return all(role in self.roles for role in roles)

    def summary(self) -> dict:
        return {"name": self.name,
                "roles": sorted(role.value for role in self.roles)}


class Principals:
    """The principals file, loaded.

    Deliberately a file and a dict rather than a database: PLAN §2.4 keeps
    stateful stores optional, and an access-control list small enough to
    read is an access-control list somebody will read.
    """

    def __init__(self, principals: Iterable[Principal] = (),
                 digests: Mapping[str, str] | None = None):
        self._principals = {p.name: p for p in principals}
        #: token digest → principal name.
        self._digests = dict(digests or {})
        if len(self._principals) != len(list(principals)):  # pragma: no cover
            raise PrincipalsError("duplicate principal names")

    def __len__(self) -> int:
        return len(self._principals)

    def __iter__(self):
        return iter(self._principals.values())

    def get(self, name: str) -> Principal | None:
        return self._principals.get(name)

    def authenticate(self, token: str) -> Principal | None:
        """The principal a token belongs to, or ``None``.

        Compared with :func:`hmac.compare_digest` against every stored
        digest rather than looked up in a dict: the dict would answer faster
        for a wrong token than for a right one, and while that is a thin
        channel it costs nothing to close.
        """
        if not token:
            return None
        candidate = token_digest(token)
        found = None
        for digest, name in self._digests.items():
            if hmac.compare_digest(digest, candidate):
                found = name
        return self._principals.get(found) if found else None

    def summary(self) -> list[dict]:
        return [p.summary() for p in sorted(self._principals.values(),
                                            key=lambda p: p.name)]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Principals":
        """Load from ``{"principals": [{name, token_sha256, roles}, ...]}``.

        Every failure raises. A principals file with an entry the loader did
        not understand is a file whose author believes somebody has access
        that they do not, or does not have access that they do — and both of
        those are worse than refusing to start.
        """
        entries = payload.get("principals")
        if not isinstance(entries, list) or not entries:
            raise PrincipalsError(
                "a principals file needs a non-empty 'principals' list"
            )
        principals, digests = [], {}
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise PrincipalsError(f"principal {i} is not an object")
            name = entry.get("name")
            digest = entry.get("token_sha256")
            raw_roles = entry.get("roles")
            if not name or not isinstance(name, str):
                raise PrincipalsError(f"principal {i} has no name")
            if not digest or not isinstance(digest, str):
                raise PrincipalsError(f"principal {name!r} has no token_sha256")
            if len(digest) != 64 or any(c not in "0123456789abcdef"
                                        for c in digest.lower()):
                raise PrincipalsError(
                    f"principal {name!r}: token_sha256 is not a SHA-256 hex "
                    f"digest"
                )
            if not isinstance(raw_roles, list) or not raw_roles:
                raise PrincipalsError(f"principal {name!r} has no roles")
            roles = set()
            for role in raw_roles:
                try:
                    roles.add(Role(role))
                except ValueError:
                    raise PrincipalsError(
                        f"principal {name!r}: unknown role {role!r}; the "
                        f"roles are {[r.value for r in Role]}"
                    ) from None
            digest = digest.lower()
            if digest in digests:
                raise PrincipalsError(
                    f"principals {digests[digest]!r} and {name!r} share a "
                    f"token"
                )
            if any(p.name == name for p in principals):
                raise PrincipalsError(f"duplicate principal {name!r}")
            digests[digest] = name
            principals.append(Principal(name=name, roles=frozenset(roles)))
        return cls(principals, digests)

    @classmethod
    def load(cls, path) -> "Principals":
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PrincipalsError(f"{path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise PrincipalsError(f"{path}: not JSON: {exc}") from exc
        return cls.from_dict(payload)

    @classmethod
    def resolve(cls, principals) -> "Principals | None":
        """Accept a :class:`Principals`, a path, a mapping, or ``None``."""
        if principals is None or isinstance(principals, cls):
            return principals
        if isinstance(principals, Mapping):
            return cls.from_dict(principals)
        return cls.load(principals)


def bearer_token(header: str | None) -> str | None:
    """The token out of an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def principal_of(request: "Request") -> Principal | None:
    """Who this request authenticated as, if anyone."""
    return getattr(request.state, "principal", None)


def require(*roles: Role):
    """A route dependency demanding every role named.

    With no principals configured it is a no-op, which is what keeps the
    library and the local demo unchanged. With principals configured it is
    the only way past: a missing or unknown token is a 401, an authenticated
    principal without the role is a 403, and the message names the roles it
    would have needed rather than making the caller guess.
    """

    async def guard(request: "Request") -> Principal | None:
        principals = getattr(request.app.state, "principals", None)
        if principals is None:
            return None
        token = bearer_token(request.headers.get("authorization"))
        if token is None:
            raise HTTPException(
                401, "this deployment requires a bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        principal = principals.authenticate(token)
        if principal is None:
            raise HTTPException(
                401, "unknown token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not principal.has(*roles):
            raise HTTPException(
                403,
                f"{principal.name} carries "
                f"{sorted(r.value for r in principal.roles)}; this route "
                f"needs {sorted(r.value for r in roles)}",
            )
        request.state.principal = principal
        return principal

    return guard
