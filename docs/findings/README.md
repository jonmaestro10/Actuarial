# Findings

*Sharp edges found by reading primary texts and the engine's own output, each
with a script beside it that CI runs.*

A finding that lives only in an RFC is an assertion. A finding with a runnable
demonstration is evidence — a reviewer can re-run it against the current
engine instead of trusting a paragraph, and if the engine changes so that a
finding stops reproducing, the build says so rather than the page quietly
becoming false.

`tests/test_findings.py` asserts that every page has a script and every script
has a page, runs each demonstration, and checks each claim. The claims are
asserted *there* rather than inside the scripts, because a script that
asserted its own claim would pass while proving nothing.

| finding | what it says | source |
|---|---|---|
| [The cliff at seven per cent](counterparty-band-cliff.md) | Solvency II Article 200's lower band boundary moves capital by 14 points of ΣLGD for an arbitrarily small change in the book; the upper boundary is continuous by construction | RFC-028 |
| [The pool of one](pool-of-one.md) | A pooled model run per policy gives every policy a pool of itself — and completes, 40% wrong, looking ordinary | RFC-061 |
| [The axis that is not the same axis twice](vm22-contract-year-bands.md) | VM-22's three structured-settlement tables band contract years two different ways, and the boundary they share reads a real cell of the wrong band | RFC-071 |
| [The number the actuary wrote](representation-error.md) | A float basis carries an error before any arithmetic runs, and an audit mode converted the wrong way hides it while looking like success | RFC-051 |
| [A sum has no safe length](reduction-order.md) | Reductions are order-dependent from twelve elements, with no threshold above which they are safe | RFC-072 |
| [The analysis of surplus depends on the order you peel it](aos-ordering.md) | Every ordering attributes different amounts; a decomposition quoted without its range presents a choice as a measurement | RFC-024 |

## Still scattered

Not everything is catalogued yet. The interest-SCR duration-matching finding
(RFC-026) and the LDTI-versus-IFRS-17 timing finding (RFC-015) are named in
the execution plan's F4 entry and still live only in their RFCs. They want
the same treatment: a page, a script, and a claim asserted in CI.
