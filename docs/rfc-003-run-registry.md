# RFC-003: Reproducibility — content addressing and the run registry

Status: **implemented** — `engine/core/fingerprint.py`, `engine/core/registry.py`

## Summary

PLAN.md §2.3 makes reproducibility the backbone of the accuracy story: a run
pins exact versions of model code, assumptions and inputs, and §7 asks the
registry to record them. Everything in the engine computed correctly before
this; nothing recorded *what* had been computed, which meant an accuracy
claim could only be checked by whoever was in the room at the time.

Two pieces:

- **`fingerprint(value)`** — a content-addressed digest of anything the
  engine takes as input.
- **`record_run(...)` and `RunRegistry`** — a run's inputs and its answer,
  each as a digest, so a repeat can be checked rather than trusted.

## The digest

Three properties, and the third is the one that takes discipline.

### It has to be stable across processes

Python salts string hashing per interpreter, so `hash()` would give a
different answer in the process that audits a result than in the process
that produced it — silently, and only sometimes. Everything goes through a
canonical byte encoding into BLAKE2b instead.

`tests/test_registry.py` runs the digest in a **subprocess with
`PYTHONHASHSEED` set to three different values** and requires the same hex
string. That test is the reason to trust every other one in the file.

### It has to be structural

Same content, same digest — regardless of object identity, dict insertion
order, or which run built the object. Sequence order *does* matter, because
reordering model points reorders results.

Type tags keep structurally different values apart. Without them `[1, 2]`,
`(1, 2)`, `"12"` and `{1: 2}` could collide, and a fingerprint that cannot
tell a list from a tuple cannot be trusted to tell one assumption set from
another.

### It has to be total, or raise

An encoder that quietly skips what it does not recognise produces a digest
that certifies less than it appears to — and the appearance is the danger,
because it invites you to stop checking. `UnfingerprintableError` is
deliberately fatal.

## Objects state their own identity

An object opts in by defining `__fingerprint__()`, returning what actually
defines it. This is explicit rather than reflective, for a concrete reason:
a `MortalityBasis` carries improvement-lookup caches filled on demand, so
hashing `vars(self)` would make an assumption set's identity depend on which
calendar years had happened to be asked for. Every fingerprint taken after a
projection would differ from the one taken before it.

`test_evaluation_history_does_not_change_an_assumption_set` pins that.

Writing it out has a second benefit: "what identifies this object" becomes a
stated thing that a reviewer can disagree with, rather than an accident of
attribute layout.

## What a run records

`run_id` fingerprints the **question**: engine version, executor, model
source, assumptions, model points, scenarios, projection length, outputs.
`results_digest` fingerprints the **answer**.

The pair is the assertion. Same `run_id` with a different `results_digest`
means the engine is not deterministic — a failure no per-number tolerance
would catch, because there is no reference number to compare against.
`RunRegistry.add` refuses it.

### Deliberately excluded

Two things are recorded but kept out of `run_id`, and each exclusion is a
claim that is therefore tested:

| Excluded | Claim | Test |
|---|---|---|
| `chunk_size` | Chunking is a memory-layout decision that cannot move a number | Every chunk size gives the same `run_id` **and** the same `results_digest` |
| `created_at` | A run repeated later from the same source is the same run | Two runs minutes apart share a `run_id` |

The executor *is* included, because it is part of how the question was
asked. That makes a satisfying test of the bitwise-equivalence claim the
engine has carried since Phase 1: the interpreted and vectorized executors
produce **different `run_id`s and the same `results_digest`**.

## What it cannot capture, said out loud

`source_digest` walks a model class and its bases. It cannot see:

- module-level helpers a formula calls;
- source at all for a class defined interactively.

So `RunRecord` carries `code_version` — the git commit, filled in
automatically from the working tree when there is one. The digest is the
braces; the commit is the belt. A record that pretended otherwise would be
the exact failure mode this RFC exists to prevent.

Floats are hashed by their bits, so `-0.0` and `0.0` differ although they
project identically. That is the conservative direction: a spurious
difference is a false alarm, a missed one is a wrong audit trail.

## Not in scope

The registry is a list, a JSON file, and one query — has this question been
asked before, and did it get the same answer. Anything richer (a Postgres
run registry, assumption approval workflow, lineage across runs) belongs in
the metadata layer PLAN.md §2.4 and §7 describe, not in the engine.
