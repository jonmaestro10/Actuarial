# Changelog

Semantic versioning, with one addition that matters more than the scheme:

> **Every change that moves a number carries an expected-change note.**

A code diff shows what changed. It does not show what a reserve did in
response, and a reviewer cannot derive one from the other. The CI drift gate
already refuses a commit that moves a golden expected value without saying so;
this file is where that note becomes public rather than staying in the commit
that made it. `scripts/changelog_gate.py` enforces it.

Versions are `MAJOR.MINOR.PATCH`:

- **MAJOR** — a numeric result changes for an unchanged input, or a documented
  API is removed. Both are the same kind of event for a client: work has to be
  redone.
- **MINOR** — new templates, new reporting overlays, new dated regulation
  sets, new API routes. Existing numbers do not move.
- **PATCH** — fixes and documentation that move no number.

A **new dated regulation set is MINOR, not MAJOR**, and that is the whole point
of dating them: `DELEGATED_2026` arriving does not disturb a client still
reporting on `DELEGATED_2015`. See `docs/regulatory-calendar.md`.

## Deprecation policy

1. Anything public is deprecated for **two MINOR releases** before removal, and
   a removal is MAJOR.
2. A deprecated name keeps working and warns, naming its replacement. A
   deprecation that only appears in release notes is one a client meets at
   upgrade time.
3. **A dated regulation set is never deprecated.** It is the only way to
   reproduce a valuation done under it, and reproducibility of a prior period
   is not a feature that expires. Sets accumulate.
4. Security fixes are exempt from the two-release window where the vulnerability
   requires it, and the changelog says which case applies.

---

## Unreleased

### Added
- **Multi-tenant packaging** (RFC-078). A tenant on each principal scopes every
  run-scoped route; `deploy/` carries a Dockerfile, a compose profile and a
  Helm chart. Off by default — an untenanted principals file behaves exactly as
  before.
- **SOC 2 control substrate** (RFC-079). `docs/compliance/soc2-controls.md`
  maps nineteen Trust Services criteria to mechanisms, the evidence pack grows
  a `compliance` section that regenerates the binder, and every control's named
  test is checked against the collected suite.
- **API hardening** (RFC-079). Security headers on every response and an
  optional in-process rate limit, both off or narrow by default.
- **`scripts/local_matrix.py`** (RFC-077) and a second architecture in CI
  (`test-arm64`).
- **The pilot playbook** (RFC-081), executed end to end in CI against synthetic
  fixtures, with `--prove-it-bites` requiring the reconciliation to fail on one
  part in ten million.
- **Regulatory calendar and this changelog** (RFC-080), with a CI gate refusing
  a moved golden value that carries no expected-change note.

### Known advisories
- `pip-audit`'s first run reports findings in `urllib3` and `wheel`. Both are
  transitive dependencies of the `[test]` and `[api]` extras, not of the
  calculation core — `engine/core`, `data`, `library` and `report` depend on
  NumPy alone (§1.4). This is the case the advisory-not-blocking decision was
  made for, and it landed on the first run.

### Changed
- CI triggers on a pull request into `main` and nothing else.

### Expected change to numbers
- **None.** No golden expected value moved in any change above. The tenancy,
  compliance and hardening work is entirely outside the calculation path, and
  the arm64 job confirmed that the engine's golden values are unchanged on a
  second architecture.
- The gate flagged numeric literals in `tests/test_tenancy.py` and
  `tests/test_compliance.py` on its first real run. They are **incidental**:
  model-point fixtures (`mortality: 0.01`, `annual_premium: 900.0`) and
  rate-limit windows (`60.0`, `59.99`) in tests that assert no reserve. Noted
  rather than suppressed — the gate errs toward asking, and the record that
  the question was considered is the thing being asked for.

### Fixed
- **The bitwise boundary asserted a property of the silicon** (RFC-072
  correction). It claimed the operations IEEE-754 §9.2 declines to require
  correct rounding for *do* differ under a compiler — true on an AVX-512
  machine, false on a runner without it, and measured in exactly one place for
  the life of the item. The classification now rests on the standard, with the
  measurement recorded beside it. **No number moved**: the operations were
  hoisted before and are hoisted now.
