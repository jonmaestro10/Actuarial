# SOC 2 control map

*Trust Services Criteria to the mechanism that satisfies each, and the test
that proves the mechanism still exists.*

**Certification is organizational; this is the technical substrate.** A SOC 2
report is an auditor's opinion about an organization — its policies, its
people, its vendors. None of that lives in a repository. What does live here is
the half an auditor asks for evidence of, and most of it already existed
before this document: this item joins it up and makes it **regenerable**.

## How to read this, and the one rule that keeps it honest

Every row names a test in the `Evidence` column, and
`tests/test_compliance.py` asserts that **every named test is collected by the
suite**. A control whose test is renamed or deleted fails the build. That is
the whole mechanism: without it this file is a document that describes a
system it has stopped matching, which is the ordinary fate of compliance
documentation and the reason auditors discount it.

The `Evidence` column is a pytest node id, or a `generated:` reference to a
section of the validation evidence pack (`python scripts/evidence_pack.py`),
which is content-addressed and rebuilds byte-identically.

**Rows that say "not claimed" are deliberate.** A control map with no gaps in
it is a control map nobody checked.

---

## CC — Common Criteria (Security)

| Control | Criterion | Mechanism | Evidence |
|---|---|---|---|
| CC6.1 | Logical access is restricted to authorised users | RFC-043: bearer tokens, SHA-256 digests stored and never tokens; no role implies another, so a ladder cannot become an escalation | `tests/test_auth.py::test_no_role_implies_another` |
| CC6.2 | Registration and authorisation precede access | Identity is deployment configuration, not editable over HTTP; there is a route to read the principal list and none to change it | `tests/test_auth.py::test_there_is_no_route_that_edits_the_principal_list` |
| CC6.3 | Access is modified and removed as roles change | Helm rolls pods on a change to the principals Secret, so a revoked token stops working at the revocation rather than at the next unrelated restart | `tests/test_deploy.py::test_every_environment_variable_the_deployment_sets_is_one_the_code_reads` |
| CC6.6 | External threats to the boundary are mitigated | RFC-079: `nosniff`, a `default-src 'none'` CSP on API routes, `no-referrer` because run ids are request fingerprints, `no-store` because results are a tenant's numbers | `tests/test_compliance.py::test_every_response_carries_the_headers_that_matter_for_json` |
| CC6.7 | Data movement is restricted to authorised parties | RFC-078: tenancy scopes every run-scoped route, and answers the wrong tenant 404 rather than 403 | `tests/test_tenancy.py::test_cross_tenant_reads_are_denied_on_every_run_route` |
| CC7.2 | Anomalies are monitored and identified | RFC-045: digest-chained audit log; any edit, reorder or relink breaks the chain and is named | `tests/test_audit.py::test_an_edited_entry_breaks_the_chain_and_is_named` |
| CC7.3 | Security events are evaluated | The chain verifies on read, so a tampered log is detected when it is used and not only when it is inspected | `tests/test_audit.py::test_a_reordered_log_is_caught` |
| CC8.1 | Changes are authorised, designed, tested, approved | CI runs the whole matrix on every pull request into `main`; RFC-044 requires four-eyes approval of an assumption set before a run may use it | `tests/test_approvals.py::test_approved_mode_refuses_an_unapproved_basis_and_names_it` |
| CC9.1 | Business disruption risk is mitigated | *Not claimed.* Backup, restore and continuity are properties of a deployment's infrastructure, not of this repository. The registry being content-addressed makes a restore *verifiable*, which is a different and smaller claim. | `generated:artifacts` |

## A — Availability

| Control | Criterion | Mechanism | Evidence |
|---|---|---|---|
| A1.1 | Capacity is monitored and managed | RFC-079: an in-process token bucket per principal, with its limits written into `RATE_LIMIT_SCOPE` — it bounds one client's share of one process and is neither distributed nor a DoS control | `tests/test_compliance.py::test_the_rate_limit_states_what_it_is_not` |
| A1.2 | Recovery infrastructure is authorised and maintained | A run is content-addressed and idempotent: the same request returns the same run and does no second computation, so a replay after a restore is verifiable rather than hopeful | `tests/test_registry.py::test_content_not_identity_decides_the_digest` |
| A1.3 | Recovery is tested | *Not claimed.* No restore drill runs here. | — |

## PI — Processing Integrity

*The category this system is actually built around.*

| Control | Criterion | Mechanism | Evidence |
|---|---|---|---|
| PI1.1 | The entity obtains information about processing objectives | Every template carries generated model documentation and declares the model-point fields it requires, so what a run needs is stated by the system rather than by a manual | `tests/test_api_demo.py::test_describing_a_model_carries_its_model_point_fields` |
| PI1.2 | Inputs are complete and accurate | The request layer validates before queueing, so a bad request is a 4xx and not a run that fails later | `tests/test_api.py::test_an_unknown_model_is_its_own_error` |
| PI1.3 | Processing is complete, accurate, timely and authorised | §1.2's executor equivalence: every template produces **bitwise-identical** results across the executors of its class, and RFC-072 fixes what a compiled kernel may contain so the guarantee survives compilation | `tests/test_bitwise_boundary.py::test_what_the_standard_guarantees_is_reproduced_bit_for_bit` |
| PI1.4 | Output is complete, accurate and timely | Results are rendered rather than encoded, because FastAPI's encoder turns a non-finite float into `null` — a valid 200 carrying a silently different number — and the results digest is returned alongside the numbers so a client can check rather than trust | `tests/test_api.py::test_results_round_trip_bitwise_through_json` |
| PI1.5 | Stored items are complete and accurate | RFC-003: every artifact is content-addressed through the registry, never a loose file with a mutable name | `tests/test_registry.py::test_arrays_are_hashed_by_content_and_shape` |

## C — Confidentiality

| Control | Criterion | Mechanism | Evidence |
|---|---|---|---|
| C1.1 | Confidential information is identified and protected | RFC-078: a tenant scopes visibility; an unclaimed run is invisible rather than public, which is the opposite of the natural implementation | `tests/test_tenancy.py::test_an_unclaimed_run_is_invisible_rather_than_public` |
| C1.2 | Confidential information is disposed of | *Not claimed.* There is no retention or deletion policy in code. The registry is append-only by design, which makes deletion an operational act on the volume. | — |

---

## What deduplicating compute across tenants leaks

Stated here because a control map that omits its own inconvenient sentence is
worth nothing. Identical work submitted by two tenants is computed **once**, by
design, and that leaves a cross-tenant liveness oracle: a tenant learns that
somebody has already run a request it can construct. Not who, no results, and
only for a request it could already build in full.

`engine.api.tenancy.shared_compute_leak()` returns this in words and the
generated compliance section quotes it, so it appears in the evidence binder
rather than only here. A deployment that will not accept it sets
`dedupe_across_tenants=False`.

## Dependency vulnerabilities

`pip-audit` runs in CI against the installed dependency set. It is advisory
here rather than blocking, and that is a deliberate and arguable choice: this
package's runtime dependency for `engine/core`, `data`, `library` and `report`
is **NumPy alone**, so an advisory almost always lands in an optional extra or
a test tool, and a blocking gate on a feed that changes daily turns into a gate
people disable. The job's output is part of the build record either way.
