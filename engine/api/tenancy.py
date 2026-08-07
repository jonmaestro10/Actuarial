r"""Whose run is it, and the one thing sharing compute still leaks.

RFC-078. §9's G1 states the rule this module is built around: **isolation is
asserted by tests, not by a policy document.** So almost nothing here is a
convention. A tenant is a value that scopes a name, visibility is a set that a
route consults, and every claim below has a test that would fail if the claim
stopped being true.

Off by default, exactly like authentication
-------------------------------------------
A principals file with no ``tenant`` on any entry yields
:data:`SINGLE_TENANT`, and the API behaves precisely as it did before this
module existed. That is the same shape RFC-043 chose for identity and for the
same reason: the engine is a library first, and a library has no tenants. A
deployment opts in by writing the field.

Mixing is refused rather than resolved. A principals file where *some*
entries carry a tenant is a file whose author believes an untenanted principal
is scoped when it is not — and the reading that would make that safe (treat
absent as its own tenant) and the reading that would make it convenient (treat
absent as "sees everything") are both defensible, which is exactly when
guessing is worst.

The fingerprint stays global; visibility is what scopes
------------------------------------------------------
``RunStore.identify`` fingerprints the *request*, so two tenants submitting
byte-identical work already collide on one :class:`~engine.api.store.Run`
before this module sees them. That is the intended behaviour — G1 asks for
"identical submissions from two tenants deduplicate compute but not
visibility" — and it means ownership is the wrong model. A run is visible to a
**set** of tenants, and the second submitter joins the set rather than
displacing or copying anything.

Keeping the fingerprint content-true is what makes the registry's whole
provenance story survive tenancy: a digest that meant something different in
each tenant would be a digest that means nothing.

**And that sharing leaks, in exactly one way.** A tenant who submits work
another tenant has already run gets an answer faster than one who does not —
and if the run has finished, gets ``complete`` immediately. That is a
cross-tenant *liveness oracle*: it tells you somebody, somewhere, ran this
exact request. It says nothing about who, and nothing about the numbers, and
you can only ask it about a request you could already construct in full.

That is a real property and it is stated rather than buried.
:func:`shared_compute_leak` returns it in words, the evidence pack reports it,
and a deployment that will not accept it sets ``dedupe_across_tenants=False``
and pays for the recompute — which is a decision about a threat model, and so
belongs to the deployment rather than to this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

#: What a deployment with no tenancy configured resolves to. Not a tenant
#: named "default" — a sentinel, so that "this deployment is single-tenant"
#: and "this deployment has a tenant that happens to be called default" can
#: never be confused, and so a stray tenant string cannot silently match it.
SINGLE_TENANT = None

#: A tenant name is a namespace segment: it becomes a registry prefix and a
#: warehouse partition directory, so it must survive both. Lowercase, digits
#: and internal hyphens, 2–40 characters. Deliberately narrower than either
#: store strictly needs — the cost of a too-strict name is a rejected config
#: line, and the cost of a too-loose one is a directory traversal.
_TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")


class TenancyError(ValueError):
    """A tenancy configuration that cannot be trusted to separate anyone."""


def valid_tenant(name: str) -> str:
    """Return ``name`` if it is a usable tenant, else raise.

    The refusals matter more than the grant. ``..`` and ``a/b`` are rejected
    here rather than sanitised downstream, because a name that reaches
    :func:`namespace` has already been used to build a path.
    """
    if not isinstance(name, str) or not _TENANT.match(name):
        raise TenancyError(
            f"tenant {name!r} is not a namespace segment: lowercase letters, "
            f"digits and internal hyphens, 2 to 40 characters. It becomes a "
            f"registry prefix and a directory name, so it is validated here "
            f"rather than escaped later."
        )
    return name


def namespace(tenant: str | None, key: str) -> str:
    """The name ``key`` takes inside ``tenant``.

    Single-tenant deployments get ``key`` unchanged, which is what lets
    tenancy be switched on over an existing store without renaming what is
    already in it.
    """
    if tenant is SINGLE_TENANT:
        return key
    return f"{valid_tenant(tenant)}/{key}"


def shared_compute_leak() -> str:
    """The residual signal cross-tenant deduplication leaves, in words.

    A function rather than a constant because it is quoted into the evidence
    pack, and a claim about a system's security properties that lives in two
    places is a claim that will disagree with itself.
    """
    return (
        "Cross-tenant compute deduplication leaves one signal: a tenant "
        "submitting a request another tenant has already run observes it "
        "reach a terminal state sooner, and may observe it already complete. "
        "This reveals that some tenant has run that exact request. It does "
        "not reveal which tenant, does not expose results, and can only be "
        "asked about a request the asker can already construct in full. "
        "Deployments that will not accept it set dedupe_across_tenants=False "
        "and pay the recompute."
    )


@dataclass(frozen=True)
class TenantRef:
    """A principal's tenant, resolved once so routes do not re-derive it."""

    name: str | None

    @property
    def is_single(self) -> bool:
        return self.name is SINGLE_TENANT

    def scope(self, key: str) -> str:
        return namespace(self.name, key)


def tenant_of(principal) -> TenantRef:
    """The tenant a principal belongs to.

    ``None`` principal — an unauthenticated deployment — is single-tenant,
    because a deployment with no identities cannot have separated them.
    """
    if principal is None:
        return TenantRef(SINGLE_TENANT)
    return TenantRef(getattr(principal, "tenant", None))


@dataclass
class Tenancy:
    """Who may see which run, and the ledger that answers it.

    Kept out of :class:`~engine.api.store.RunStore` on purpose. The store's
    job is that a request maps to one computation; deciding who is allowed to
    learn that is a different question with a different failure mode, and a
    store that also enforced access control would be a store whose
    deduplication and whose authorisation could only be tested together.
    """

    #: run_id → the tenants that submitted it and may therefore see it.
    _visibility: dict = field(default_factory=dict)
    #: Whether an identical request from a second tenant reuses the first
    #: tenant's computation. True is the default and the leak above is its
    #: price; False recomputes, and the run ids then differ by tenant.
    dedupe_across_tenants: bool = True

    def salt(self, tenant: TenantRef) -> str | None:
        """What to fold into a submission's fingerprint, if anything.

        ``None`` with deduplication on, so the fingerprint is the request's
        alone and two tenants collide by design. The tenant name with it off,
        which is what makes the recompute happen — and it moves the
        ``run_id`` the caller sees, stated here because a deployment flipping
        this flag will find its run ids have changed. The salt never reaches
        the *stored* request: see :meth:`~engine.api.store.RunStore.identify`.
        """
        if self.dedupe_across_tenants or tenant.is_single:
            return None
        return valid_tenant(tenant.name)

    def note(self, run_id: str, tenant: TenantRef) -> None:
        """Record that ``tenant`` submitted ``run_id`` and may see it."""
        self._visibility.setdefault(run_id, set()).add(tenant.name)

    def may_see(self, run_id: str, tenant: TenantRef) -> bool:
        """Whether ``tenant`` is allowed to learn anything about ``run_id``.

        An unknown run is **not** visible. That is the case worth stating:
        the natural implementation returns True for a run nobody has claimed,
        and then every run submitted before tenancy was switched on becomes
        readable by everyone.
        """
        if tenant.is_single:
            return True
        return tenant.name in self._visibility.get(run_id, ())

    def visible(self, runs: Iterable, tenant: TenantRef) -> list:
        """Filter an iterable of runs to those ``tenant`` may see."""
        if tenant.is_single:
            return list(runs)
        return [r for r in runs if self.may_see(r.run_id, tenant)]

    def tenants_of(self, run_id: str) -> frozenset:
        """Every tenant that may see this run — for the audit trail."""
        return frozenset(self._visibility.get(run_id, ()))

    def summary(self) -> dict:
        """What this deployment's tenancy is, for ``/health``."""
        return {
            "enabled": bool(self._visibility) or not self.dedupe_across_tenants,
            "dedupe_across_tenants": self.dedupe_across_tenants,
            "shared_compute_leak": (shared_compute_leak()
                                    if self.dedupe_across_tenants else None),
        }


def tenants_in(principals) -> frozenset:
    """Every tenant named in a principals file.

    Raises if the file is *partly* tenanted. Both readings of an absent
    tenant beside a present one are defensible — its own tenant, or sees
    everything — and a file that needs the reader to pick is a file that
    will be read the other way by somebody.
    """
    if principals is None:
        return frozenset()
    named = {getattr(p, "tenant", None) for p in principals}
    if named == {None}:
        return frozenset()
    if None in named:
        untenanted = sorted(p.name for p in principals
                            if getattr(p, "tenant", None) is None)
        raise TenancyError(
            f"principals {untenanted} carry no tenant while others do. A "
            f"partly tenanted principals file has two defensible readings — "
            f"absent means its own tenant, or absent means sees everything — "
            f"and this refuses rather than picking one. Give every principal "
            f"a tenant, or none."
        )
    return frozenset(valid_tenant(t) for t in named)
