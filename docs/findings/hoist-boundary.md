# The published boundary and the operative one are different boundaries

*The compiled executor hoists what a kernel cannot reproduce bit for bit.
RFC-072 settled what that is, against IEEE-754. Almost nothing is hoisted for
that reason.*

`python scripts/findings/hoist_boundary.py`

**Claim.** RFC-072 settled which operations a kernel may contain, against
IEEE-754, and the compiled executor hoists everything else. In practice almost
nothing is hoisted for that reason: the overwhelming trigger is
`untracked-array` — a raw ndarray arriving mid-expression — which is a limit of
the *tracer*, not of the arithmetic. The published boundary and the operative
one are different boundaries, and the operative one is invisible because both
produce the same correct answer.

---

## The claim

RFC-072 is one of this repository's better pieces of work. It asked whether a
compiled kernel returns the same bits NumPy does, measured that the answer
splits by IEEE-754 §5 against §9.2 rather than by compiler, and fixed the
kernel's contents accordingly: correctly-rounded operations only, everything
else hoisted into a NumPy slab.

Ask the tracer *why* each variable is actually hoisted and the answer is
almost never that.

| reason | `classify` says | templates |
|---|---|---|
| `untracked-array` | unknown | **12** |
| `power` | hoist (§9.2) | 1 |
| `max` (reduction) | reduce | 1 |

**Twelve of thirteen templates are blocked by `untracked-array`** — a raw
`ndarray` arriving mid-expression, which the tracer cannot follow and so marks
opaque, forcing the whole variable out of the kernel. One is blocked by the
standard.

## Why it stayed invisible

Both boundaries produce the *same correct answer*. A variable hoisted for a
good reason and a variable hoisted for a bad one are both computed by the
vectorized executor, bitwise, and the executor-equivalence suite is satisfied
either way. Nothing was wrong; something was merely slower than its stated
reason implied.

It also stayed invisible because the stated reason is **true of the
operations** the kernel contains. RFC-072's table is right. It is just not
what is deciding the hoist set.

[RFC-082](../rfc-082-interleaved-prepass.md) walked past this twice. It measured that `PayoutAnnuity`'s pre-pass is
irreducible, and concluded that widening the boundary was the way to move it —
without asking what the boundary currently *is*. The answer was one query away
from the code it was already reading.

## Stage 0: what the arrays actually are

`untracked-array` is one label over **two** root causes, and neither is an
anonymous computed intermediate — which was the outcome that would have made
this ceiling permanent.

| root cause | what it is | templates blocked |
|---|---|---|
| `self.at(slab, t)` | a per-policy-per-period **setup slab**, sliced | LongTermCare, LongevitySwap, PayoutAnnuity, PensionBuyout, GeneralInsurance |
| `self.assumptions.periodic_q(...)` | a mortality **table gather** | 7 templates, all via `q_x` |

Everything else in the pass-1 trace is a *cascade* from those two: a variable
reading a not-yet-hoisted variable sees a raw array, and later passes resolve
it into a `ref`. The roots are the two rows above.

**Both already have names that survive to run time**, which is the property the
fix needs and the reason it is available at all. A setup slab is built by
`setup()` from the batch, so another batch builds its own; `periodic_q` is a
lookup against a basis the run already carries.

### Where the slab leaves the trace

`Model.at` is one line: `value = slab[..., t]`. `_Traced` implements the ufunc
and array-function protocols and **not `__getitem__`**, so a slab cannot be
carried through that slice as a traced value — it arrives at the operand as a
plain array and the variable hoists.

The obvious repair — give `_Traced` a `__getitem__` so the slice stays traced —
is **dead code**, and measuring said so before it was written. Instrumenting
`Model.at` during a trace finds the slab is a plain `ndarray` every time, 28 of
28 on `PayoutAnnuity`. It never enters the trace, so there is nothing for
`__getitem__` to preserve.

`setup()` shows why. Slabs are built by date and calendar machinery —
`DateArray.coerce(self.mp.dob)`, `born.year[:, None]`, `np.atleast_1d(np.asarray(...))` —
which sits entirely outside the ufunc and array-function protocols the tracer
hooks. The slab is not a traced expression that got dropped at an index. **It
can never be a traced expression**, and making it one would mean tracing the
calendar.

That is the useful conclusion, because it says what the fix is *not*. A slab
does not need to be traced. It needs to be **named and passed in** — it is an
input, exactly like a hoisted variable's slab, and it already has a name:
the attribute `setup()` assigned it to. The work is registration and argument
plumbing, not tracer coverage.

## What it would take, and why the cheap fixes are wrong

The tracer marks an array opaque because it has **no name that survives to run
time**. That is the same argument the module docstring makes for hoisting a
variable whole rather than in part: a `@var` has a name the vectorized executor
can evaluate for any batch, and an anonymous intermediate does not.

Two obvious fixes are both unsafe:

- **Capture the array at trace time and pass it as an argument.** It would be
  specialised to the traced batch and wrong for the next block. That is
  RFC-070's bug with a compiler behind it, which `plan` already refuses when it
  is a *branch*; doing it for an array would be the same defect through a
  different door.
- **Fold a constant vector to a scalar.** Constant *in this batch* is not
  constant by construction, and nothing distinguishes them at trace time.

What would work is naming the inputs: an assumption a model reads as an array
becomes a traced leaf with a stable identity, so the kernel can take it as an
argument and the next block can supply its own. That is a change to how models
reach the basis, not to the kernel — and it is the item the plan does not have.

## Why this is worth writing down rather than fixing here

The fix is a substantial change to a load-bearing seam, and this finding's
value does not depend on it. What is worth having now is that the number in the
plan — "the pre-pass is a median 54% of the runtime, Amdahl's law, not a defect
in the fusion" — is true and slightly misleading. It is Amdahl's law over a
denominator set by a **tracing limitation**, not by the standard the surrounding
prose cites.

A performance ceiling attributed to IEEE-754 sounds permanent. This one is not.
