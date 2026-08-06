# Calculation UX Plan: Exposing the Calculations Themselves

*The implementation plan for the user experience that exposes this engine's
calculations — not just its results. Premise: the
[competitive-execution-plan](competitive-execution-plan.md) is **executed** —
RFC-033 through RFC-060 implemented, milestones M1–M6 reached. So the parity
core (A1), auth and roles (D1), 4-eyes approvals (D2), the audit log (D3),
the results warehouse (E1), the workbook writer (E2), the production UI's
first cut (E3: runs list, results explorer, assumption diff, parity and
evidence-pack views), the live Excel add-in (E4), and the evidence pack (F1)
all exist. This plan is the layer that platform cannot yet show: the
calculation itself. Items claim RFC-061 onward. Derived from
[competitive-landscape.md](competitive-landscape.md) §7, the discovery of how
the field exposes its engines. Written August 2026.*

---

## 1. The thesis: transparency is the product, so the UX is a microscope

Every platform surveyed in landscape §7 hides its calculation behind its UX.
Prophet's Diagram View shows the variable graph but the engine is compiled
C++ from a proprietary workspace; AXIS shows switches and 26,000 help texts
because users *cannot* read the formulas; Integrate's Power BI dashboards
show outputs whose lineage stops at the run that produced them. The field's
results UX answers "what is the number?"; none of it answers **"why is the
number?"** below the run level.

With the execution plan done, this repo matches the field's exposure
baseline: a web runs surface, drill-down results exploration, assumption
diffs, warehouse-fed BI, fingerprint-stamped workbooks. What remains
unbuilt — and what no incumbent can copy — is the layer only a transparent
engine permits. The calculation is plain Python in git, traced into a
dependency graph with time offsets (RFC-030), executed under a bitwise
multi-executor guarantee, every run a content-addressed question with a
content-addressed answer (RFC-003, RFC-031). The UX this plan builds is a
**microscope over that transparent engine**: every number on every screen
can be opened, and opening it shows the formula, its documentation, its
inputs, and their values, down to the source line.

One sentence of scope discipline, learned from SLOPE's counter-example: this
plan builds **inspection surfaces — not a web authoring surface**. Authoring
stays in Python, in git, in CI; that is the repo's thesis (landscape §2.1),
and a browser formula-builder would compete with the repo's own best
property. The 80% of users who will never read Python (landscape §5.4) are
served by making the *reading* unnecessary: docstrings, rendered formulas,
traced values and plain-language lineage do the reading for them.

## 2. What we take from each competitor, and what we decline

The best aspects found in the §7 discovery, each mapped to a design decision.
Where the executed plan already delivered the parity move, the row says so
and this plan builds the step beyond it.

| Source (landscape §7) | What they got right | Status after the executed plan | What this plan adds |
|---|---|---|---|
| Prophet **Diagram View** | Precedent/dependent navigation per variable, computed values visible after a run | Static graph + generated docs (RFC-030); no interactive view | The **graph explorer** (§4.2): the graph rendered live, run values overlaid, time-offset edges (`t-1`) drawn as such — which Prophet's view does not distinguish |
| Prophet **`.rpt` variable groups** / AXIS seriatim / SLOPE **drill-to-model-point** | Policy-level drill-down is the feature vendors lead with | ✅ E3's explorer drills aggregate → variable → model point | Every seriatim value becomes a **door into the trace panel** (§4.1) — drill-down that ends at the formula, not at the number |
| Prophet **QA module** / Coherent Spark **Testing Center** | Run-vs-run comparison and regression as a product surface | ✅ E3 renders A1 parity reports; assumption diff exists | The **compare screen** (§4.3): any two fingerprints → per-variable deltas → the exact disagreeing cell → *two trace panels side by side* |
| AXIS **26,000 help texts** + **Navigator** | Documentation at industrial scale, AI-fronted, because the engine is opaque | Docstrings measured and asserted (floor 80.3%); rendered in generated docs | Docstrings surfaced **in place** next to every value on every screen; the MCP surface (§4.5) is our Navigator, minus the need for one |
| Integrate **Power BI** / SLOPE **Snowflake** / R³S **QuickSight** | The real results UX is a queryable store plus the client's own BI | ✅ E1 warehouse, fingerprint on every row; documented DuckDB/Power BI path | **Warehouse concordance UX** (§5): paste any BI number's fingerprint into the UI, land on that number's trace — "your dashboard, our microscope, one digest" |
| R³S **Workflow Manager** | A genuinely web-based run-operations surface with roles, approvals, audit | ✅ E3 + D1–D3 | Approval and audit trails *linked from the numbers they govern* (§4.4) — the approval chip on a result opens the D2 record |
| WTW **Unify** sign-off gates and input snapshots | Reproducibility and review gates around the run | ✅ Superseded by construction (registry) | The UX job left: *show* it — determinism badges, digest chips, deep links as citations (§5) |
| Integrate **sandboxed what-if** | Non-modellers safely rerunning production models with changed assumptions | Submission exists (API, add-in); no guided sandbox | The **what-if screen** (§4.4): clone, edit via schema-driven form, submit, land in Compare — idempotency makes it nearly free |
| Spark **MCP endpoint**, RAFM/Montoux **LLM assistants** | AI agents as first-class consumers of the platform | REST is agent-usable but untyped for agents | An **MCP server** over the documented API (§4.5): catalogue, runs, results, traces, compare — the engine legible to agents with no assistant product in between |

**Declined**, with reasons: SLOPE's no-code authoring (contradicts the
code-first thesis, §1); thick clients (the API is the product boundary;
RFC-032's rule stands); a bespoke BI/charting suite (landscape §7.3.2 — the
industry converged on "land it queryable, let BI look at it"; E1 is that
answer, and a request for a custom dashboard is met with the DuckDB/Power BI
recipe, not a dashboard builder).

## 3. Personas and the architecture rules

Three personas, mapped to D1's shipped roles:

- **The modeller** (runner): lives in the graph explorer and trace panel;
  wants "why is this cell this value" answered in seconds.
- **The reviewer/approver** (approver): lives in Compare and the approval
  views; wants deltas explained and evidence linked.
- **The consumer** (viewer): lives in E3's explorer and the E2/E4 Excel
  surfaces; wants numbers with provenance, never the machinery.

Five architecture rules, inherited from RFC-032/E3 and extended:

1. **Every pixel is a documented REST call.** The UI has no privileged
   channel; anything it shows, a client script or MCP agent can fetch
   identically. New screens force new endpoints, and the endpoints are the
   deliverable as much as the screens.
2. **No build toolchain.** Static assets served by FastAPI as today
   (`engine/api/ui/`): vanilla JS, hand-rolled SVG for charts and graph
   layout, zero npm. The dependency discipline (execution plan §1.4)
   applies to the front end too.
3. **The URL is a citation.** Every view's full state — fingerprint,
   variable, model point, time step, comparison pair — lives in the URL, so
   any screen pastes into a review comment and reproduces exactly.
   Content-addressed runs make the link *permanent*: it cannot rot into
   showing different numbers.
4. **The UI holds no state of its own.** Reads come from the registry, the
   run store and the E1 warehouse; writes are the existing documented
   mutations (submit, approve). No UI database, ever.
5. **Provenance is ambient.** Run fingerprint, results digest and
   assumption digests appear on every screen as copyable chips — the same
   stamping rule E2 established for workbooks — and a determinism badge
   states which executors produced and verified the numbers.

## 4. The surfaces

### 4.1 The trace panel — "explain this number"

The flagship, and the screen no competitor can build. Click any value
`v(p, t)` anywhere in E3's explorer and a panel opens showing:

- the variable's **docstring** (the help text, generated not written);
- its **formula source** — the actual `@var` body, syntax-highlighted,
  linked to file and line in the pinned model version;
- its **precedents with their values at the referenced offsets** —
  `premium(t)`, `reserve(t-1)` — each value itself clickable, so a reviewer
  walks the recursion exactly the way the engine did;
- its **dependents**, for the opposite question ("what does this feed?").

Recomputation: none. Values come from stored results; structure from the
RFC-030 graph; source and docs from `modeldoc`. A cell not retained in
stored output (memo windowing) says so honestly — "not retained; rerun with
retention" — rather than silently recomputing.

New endpoints: `GET /runs/{id}/trace?var=&mp=&t=` (value, precedent values,
source, doc — one call per panel) and a seriatim slice endpoint. Both are
exactly what the MCP surface (§4.5) needs too; rule 1 pays for itself here.

### 4.2 The graph explorer

The whole model's dependency graph, laid out layered (hand-rolled
Sugiyama-style; library templates are hundreds of nodes, not millions),
time-offset edges drawn distinctly, filterable by tag, subgraph and search.
With a run selected, node badges show aggregate values at a chosen `t`;
without one, it is the model's living documentation diagram — the data
`GET /models/{name}/graph` already serves, made navigable. Clicking a node
opens the trace panel. This is Prophet's Diagram View with the two things
it lacks: offset-labelled edges and the source under every node.

### 4.3 Compare — regression, reconciliation, explanation

Any two fingerprints (or a fingerprint and an A1 parity report): a
per-variable delta summary (max abs/rel, count differing, worst cell),
sortable; each row expands to the disagreeing cells; each cell opens **two
trace panels side by side** — same variable, same policy, same `t`, both
runs — so "why did the number move" is answered by inspection, not
archaeology. The E3 assumption diff renders above the numeric deltas:
cause above effect. Prophet's QA module and Spark's Testing Center stop at
*which* numbers differ; this screen continues to *why*.

Compare deltas are computed server-side (`GET /compare?a=&b=`) from stored
results — never in the browser, so the MCP surface and CI get the identical
comparison.

### 4.4 What-if — the sandbox

From any run: clone the request into a schema-driven form (the catalogue's
`example`/`modelpoint_fields` machinery describes the shape), edit
assumptions, submit role-permitting (D1), land in Compare against the base.
Unchanged questions dedupe to the base fingerprint — the sandbox is safe
*and* cheap by construction, where Integrate needs a managed service to
promise the same. What-if runs are registry-recorded like any run (no
off-the-books mode), visibly tagged with their base fingerprint, and
excluded from approved-mode reporting unless approved (D2). Approval chips
on results link to the governing D2 record; the audit trail (D3) for a run
is one click from the run.

### 4.5 The MCP surface — the field's direction, met early

`engine/api/mcp.py` (behind `[api]`): an MCP server exposing the documented
REST surface as typed tools — list/describe models, submit runs, fetch
result slices, trace values, compare runs, read parity reports and evidence
packs (F1). Landscape §7.3.8's observation is that every vendor is bolting
AI assistants onto opaque engines; a transparent engine plus MCP needs no
assistant product at all — the agent reads the same docstrings, sources and
traces the human does. Costs little because rules 1 and 5 made every
capability an endpoint already.

## 5. Provenance UX — the beyond-parity layer

The discovery's sharpest conclusion (§7.3.7): the field audits
reproducibility by *snapshot*; this repo has it by *construction*. The
machinery all exists post-plan (registry, D3 chained log, F1 evidence
packs, E1 fingerprint-on-every-row). The remaining work is making it
*visible everywhere* rather than checkable somewhere:

- **Digest chips** on every screen — copyable, pasteable into the global
  search box, which resolves any digest to its object (run, results,
  assumption set, approval, parity report).
- **A determinism badge** per run: which executors ran (interpreted /
  vectorized / compiled / dispatched via B2's shard tree), whether the
  bitwise cross-check ran, verified-on-read status. Green is not
  decoration; it names the guarantee.
- **Deep links as citations** (rule 3): review comments, audit findings and
  evidence packs cite screens by URL, and the URL provably shows the same
  numbers forever.
- **Warehouse concordance:** any figure in the client's own BI carries a
  run fingerprint (E1); entering it in the UI lands on that number's trace.
  That round trip — dashboard to formula in two actions — is the sales
  demo, and no surveyed platform can perform it.

## 6. Work items

Continues the execution plan's protocol (§1). RFC-033–060 are taken;
these claim RFC-061 onward. Effort keys as in the execution plan.

### UX1 — Trace panel + seriatim doors (RFC-061) — effort M
§4.1. The trace endpoint, the seriatim slice endpoint, the panel itself,
and the wiring that makes every value in E3's explorer a door into it.
**Accept:** `tests/test_api_trace.py` — the trace of a known template cell
returns the exact stored values for the cell and every precedent at the
correct offsets, asserted `==` against a direct engine run; source and
docstring match `modeldoc` output; a recursive variable's trace terminates
and links `t-1` correctly; a windowed-out cell returns the honest
"not retained" response, never a recomputed value; seriatim routes obey D1
role gates (viewer configurable to aggregates only).

### UX2 — Graph explorer (RFC-062) — effort M
§4.2. SVG layered layout, filter/search, run-value overlay, node → trace
panel. **Accept:** the graph view renders every template in
`engine/library/` (snapshot test on node/edge/offset-edge counts); layout
is deterministic (same graph → same coordinates — the screenshot is
reproducible, like everything else); overlay values equal the results
endpoint's aggregates exactly.

### UX3 — Compare + what-if (RFC-063) — effort M
§4.3–4.4. Server-side compare endpoint; clone/edit/submit reusing the
existing submission path unchanged; side-by-side trace.
**Accept:** compare of a run with itself is all-zero; compare of two runs
differing in one assumption localises every delta to the affected
variables (golden case); a what-if edited back to the original assumptions
dedupes to the base fingerprint, and the UI shows that visibly; approved-
mode exclusion of unapproved what-ifs tested against D2.

### UX4 — Provenance everywhere (RFC-064) — effort S
§5. Digest chips and global digest search, the determinism badge, URL
state discipline retrofitted to every E3 screen.
**Accept:** URL round-trip test — every view's state survives reload from
the URL alone; digest search resolves each registry object type; the badge
renders each executor/verification combination from fixture registries;
warehouse-vs-store concordance test (same run rendered from both paths
yields identical JSON).

### UX5 — MCP surface (RFC-065) — effort S
§4.5. **Accept:** an MCP test client lists models, submits the worked
example, traces a cell and gets values identical to the REST path; tool
schemas are generated from, and asserted against, the same catalogue the
UI uses; auth reuses D1 tokens.

## 7. Sequencing

With the execution plan done there are no cross-workstream dependencies
left; the order is by leverage:

```
UX1 (trace)        — first: the differentiator, and everything else links
                     into it.
UX2 (graph)        — second: shares UX1's data spine; the demo moment.
UX3 (compare/what-if)
UX4 (provenance)   — small, but touches every screen; after UX3 so it
                     stamps the full surface once.
UX5 (MCP)          — last and cheapest; by then every capability is an
                     endpoint.
```

Serial execution per protocol §1.9; each item independently shippable.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Hand-rolled SVG graph layout becomes a project of its own | Library graphs are 10²–10³ nodes; layered layout with filter-first UX suffices. If a model exceeds usable size, the *filter* is the feature — never adopt a JS graph framework to solve it (rule 2) |
| Trace endpoint tempts recomputation | Stated in the RFC: trace *reads* stored results and the static graph; it never executes model code. Windowed-out cells answer honestly (§4.1) |
| Seriatim/trace routes leak PII-shaped model-point data | D1 role gates on those routes; the warehouse path inherits E1 partitioning; the RFC documents the posture |
| The UI drifts from the API (screens with no endpoint) | Rule 1 enforced in CI: the UI asset test extracts fetch targets and asserts each appears in the OpenAPI schema |
| What-if becomes shadow production | Registry-recorded, base-fingerprint-tagged, excluded from approved-mode reporting unless approved (D2) — tested in UX3 |
| Scope creep toward a BI suite | §2's "declined" list is binding: trace, graph, compare — not dashboards. Custom-dashboard requests are answered with the E1 DuckDB/Power BI recipe |

---

*Sources: competitive-landscape.md §7 (discovery of competitor engine
exposure, August 2026); competitive-execution-plan.md (assumed executed:
RFC-033–060, milestones M1–M6); RFC-003 (registry), RFC-030 (model docs and
dependency graph), RFC-031 (REST API), RFC-032 (demo UI and catalogue);
`engine/api/app.py` route inventory at commit `ebd8622`.*
