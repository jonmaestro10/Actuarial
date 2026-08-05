"use strict";
/* RFC-032's demonstration page.
 *
 * Every number on the screen came out of an endpoint listed in /docs. There
 * is no second path into the engine from here and nothing is computed in
 * this file that the engine could have been asked for — block totals come
 * from GET /runs/{id}/results?aggregate=true rather than from summing the
 * per-model-point arrays in JavaScript, because the two executors reduce
 * differently and a total added up here would be close to the engine's
 * rather than equal to it.
 *
 * Charts are hand-drawn SVG. See engine/api/ui/__init__.py for why there is
 * no chart library. */

// ── small helpers ───────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...kids) {
  const node = tag.includes(":")
    ? document.createElementNS("http://www.w3.org/2000/svg", tag.split(":")[1])
    : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined) continue;
    node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return node;
}

const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };

/* A fixed locale, so the same run reads the same on any machine. */
const GROUPED = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });

function num(v) {
  if (v === null || v === undefined) return "—";
  if (!Number.isFinite(v)) return String(v);
  if (v !== 0 && Math.abs(v) < 0.005) return v.toExponential(2);
  return GROUPED.format(v);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : (body || response.statusText);
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return body;
}

const postJSON = (path, body) => api(path, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

function showError(node, error) {
  clear(node);
  if (error) node.appendChild(el("div", { class: "err", text: String(error.message || error) }));
}

function pill(node, text, kind) {
  node.className = "pill" + (kind ? " " + kind : "");
  node.textContent = text;
}

const SERIES_COLOURS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
const colourOf = (i) => `var(${SERIES_COLOURS[i % SERIES_COLOURS.length]})`;

// ── page state ──────────────────────────────────────────────────────────

const state = {
  models: [],
  model: null,       // the selected template's name
  example: null,     // its worked request, as loaded
  run: null,         // the last submitted run's summary
  results: null,     // its aggregated series
  graph: null,
  report: null,
  events: null,      // EventSource
};

// ── charts ──────────────────────────────────────────────────────────────

/* A line chart, drawn into `host`, with a hover readout written into
 * `readout` and a clickable legend built into `legend`.
 *
 * series: [{name, values:[…]}] on a shared integer x axis starting at 0. */
function lineChart(host, legendHost, readoutHost, series, options) {
  const opts = Object.assign({ height: 260, xLabel: "period", area: false }, options || {});
  const hidden = new Set(opts.hidden || []);
  const W = 900, H = opts.height, PAD = { t: 12, r: 14, b: 38, l: 66 };

  function draw() {
    clear(host);
    const live = series.filter((s) => !hidden.has(s.name));
    const n = Math.max(1, ...series.map((s) => s.values.length));
    let lo = 0, hi = 0;
    for (const s of live) for (const v of s.values) {
      if (!Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    if (lo === hi) hi = lo + 1;
    const span = hi - lo;
    lo -= span * 0.04; hi += span * 0.04;

    const x = (i) => PAD.l + (n === 1 ? 0 : (i / (n - 1)) * (W - PAD.l - PAD.r));
    const y = (v) => H - PAD.b - ((v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);

    const svg = el("svg:svg", {
      class: "chart", viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: "none", role: "img",
    });

    // y gridlines at five steps, labelled compactly — these axes carry
    // millions and a full grouping would be wider than the plot.
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((hi - lo) * k) / 4;
      svg.appendChild(el("svg:line", { class: "grid", x1: PAD.l, x2: W - PAD.r, y1: y(v), y2: y(v) }));
      svg.appendChild(el("svg:text", {
        x: PAD.l - 8, y: y(v) + 3.5, "text-anchor": "end", text: COMPACT.format(v),
      }));
    }
    if (lo < 0 && hi > 0) {
      svg.appendChild(el("svg:line", { class: "zero", x1: PAD.l, x2: W - PAD.r, y1: y(0), y2: y(0) }));
    }
    svg.appendChild(el("svg:line", { class: "axis", x1: PAD.l, x2: PAD.l, y1: PAD.t, y2: H - PAD.b }));
    svg.appendChild(el("svg:line", { class: "axis", x1: PAD.l, x2: W - PAD.r, y1: H - PAD.b, y2: H - PAD.b }));

    const step = Math.max(1, Math.ceil(n / 12));
    for (let i = 0; i < n; i += step) {
      svg.appendChild(el("svg:text", { x: x(i), y: H - PAD.b + 15, "text-anchor": "middle", text: String(i) }));
    }
    // On its own line under the ticks: sharing the row put it on top of the
    // last tick label whenever the axis ran to the right-hand edge.
    svg.appendChild(el("svg:text", {
      x: PAD.l + (W - PAD.l - PAD.r) / 2, y: H - 5,
      "text-anchor": "middle", text: opts.xLabel,
    }));

    for (const s of live) {
      const colour = colourOf(series.indexOf(s));
      const points = s.values
        .map((v, i) => (Number.isFinite(v) ? `${x(i)},${y(v)}` : null))
        .filter(Boolean);
      if (!points.length) continue;
      if (opts.area) {
        svg.appendChild(el("svg:polygon", {
          points: `${x(0)},${y(Math.max(lo, 0))} ${points.join(" ")} ${x(s.values.length - 1)},${y(Math.max(lo, 0))}`,
          fill: colour, "fill-opacity": 0.1, stroke: "none",
        }));
      }
      svg.appendChild(el("svg:polyline", {
        points: points.join(" "), fill: "none", stroke: colour,
        "stroke-width": 1.8, "stroke-linejoin": "round",
      }));
    }

    const cursor = el("svg:line", { class: "cursor", x1: 0, x2: 0, y1: PAD.t, y2: H - PAD.b, opacity: 0 });
    svg.appendChild(cursor);
    svg.appendChild(el("svg:rect", {
      x: PAD.l, y: PAD.t, width: W - PAD.l - PAD.r, height: H - PAD.t - PAD.b,
      fill: "transparent",
      onmousemove: (event) => {
        const box = svg.getBoundingClientRect();
        const px = ((event.clientX - box.left) / box.width) * W;
        const i = Math.max(0, Math.min(n - 1, Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (n - 1))));
        cursor.setAttribute("opacity", 1);
        cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i));
        clear(readoutHost);
        readoutHost.appendChild(el("span", {}, el("b", { text: `t = ${i}` }), "  "));
        for (const s of live) {
          readoutHost.appendChild(el("span", { style: `color:${colourOf(series.indexOf(s))}` },
            ` ${s.name} `, el("b", { text: num(s.values[i]) })));
        }
      },
      onmouseleave: () => { cursor.setAttribute("opacity", 0); clear(readoutHost); },
    }));

    host.appendChild(svg);
  }

  clear(legendHost);
  for (const s of series) {
    const button = el("button", {
      type: "button", "aria-pressed": String(!hidden.has(s.name)),
      onclick: (event) => {
        if (hidden.has(s.name)) hidden.delete(s.name); else hidden.add(s.name);
        event.currentTarget.setAttribute("aria-pressed", String(!hidden.has(s.name)));
        draw();
      },
    },
      el("span", { class: "swatch", style: `background:${colourOf(series.indexOf(s))}` }),
      s.name);
    legendHost.appendChild(button);
  }
  draw();
}

function seriesTable(table, series, rows) {
  clear(table);
  const head = el("tr", {}, el("th", { text: "t" }), ...series.map((s) => el("th", { text: s.name })));
  table.appendChild(el("thead", {}, head));
  const body = el("tbody", {});
  for (let i = 0; i < rows; i++) {
    body.appendChild(el("tr", {},
      el("td", { text: String(i) }),
      ...series.map((s) => el("td", { text: num(s.values[i]) }))));
  }
  table.appendChild(body);
}

// ── boot ────────────────────────────────────────────────────────────────

async function boot() {
  try {
    const health = await api("/health");
    $("version").textContent =
      `engine ${health.engine_version} · ${health.models} templates`;
  } catch (error) {
    $("version").textContent = "the API is not answering";
    return;
  }
  const listing = await api("/models");
  state.models = listing.models;
  const host = $("models");
  clear(host);
  for (const model of state.models) {
    const button = el("button", {
      class: "model", type: "button", "aria-pressed": "false",
      disabled: !model.example,
      title: model.unavailable || "",
      onclick: () => selectModel(model.name),
    },
      model.name,
      el("span", {
        class: "meta",
        text: model.example
          ? `${model.variables} variables${model.pooled ? `, ${model.pooled} pooled` : ""}`
          : "no worked example",
      }));
    host.appendChild(button);
  }
  // The templates without an example are the interesting half of the
  // catalogue: they are what the request schema deliberately does not reach.
  const missing = state.models.filter((m) => !m.example);
  if (missing.length) {
    host.appendChild(el("p", { class: "small muted", style: "padding:10px 16px 0" },
      `${missing.length} of ${state.models.length} templates need an assumption `
      + "object the request schema does not carry — hover one for the reason. "
      + "They run from Python, or from a deployment that passes its own builder."));
  }
  // Term assurance first where it exists: it is the simplest template, and
  // the only one whose example carries every series the IFRS 17 tab wants.
  const first = state.models.find((m) => m.name === "TermLife" && m.example)
    || state.models.find((m) => m.example);
  if (first) selectModel(first.name);
  listenForEvents();
}

function listenForEvents() {
  const source = new EventSource("/events");
  state.events = source;
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (!state.run || event.run_id !== state.run.run_id) return;
    logEvent(event);
    state.run = event;
    renderRunFacts();
    if (event.state === "succeeded") loadResults(event.run_id);
    if (event.state === "failed") {
      showError($("request-error"), new Error(event.error || "the run failed"));
      pill($("request-state"), "failed", "bad");
    }
  };
}

// ── templates ───────────────────────────────────────────────────────────

/* The request, formatted for a human to edit.
 *
 * Indented two spaces like any JSON, except that a mortality table is put
 * on one line. A specimen table is ninety-odd ages, and pretty-printed it
 * buries the model points — the part anyone actually wants to change —
 * under three screens of rates. Still valid JSON, still editable, and the
 * table is still all there. */
function prettyRequest(request) {
  const table = request.assumptions && request.assumptions.mortality;
  if (!table || typeof table !== "object") return JSON.stringify(request, null, 2);
  // A sentinel JSON will not escape and the request will not contain, so
  // the substitution back is an exact single match. (A control character
  // would be the tidier marker and is the wrong choice: JSON.stringify
  // escapes it, and the escaped form is not what the search looks for.)
  const MARK = "__MORTALITY_TABLE__";
  const shallow = Object.assign({}, request, {
    assumptions: Object.assign({}, request.assumptions, { mortality: MARK }),
  });
  return JSON.stringify(shallow, null, 2)
    .replace(JSON.stringify(MARK), JSON.stringify(table));
}

async function selectModel(name) {
  state.model = name;
  for (const button of document.querySelectorAll(".model")) {
    button.setAttribute("aria-pressed", String(button.textContent.startsWith(name)));
  }
  const example = await api(`/models/${name}/example`);
  state.example = example;
  $("run-title").textContent = `Request — ${name}`;
  $("run-note").textContent = example.note;
  $("request").value = prettyRequest(example.request);
  pill($("request-state"), "example loaded");
  showError($("request-error"), null);
  $("run-card").hidden = true;
  $("results-card").hidden = true;
  $("results-table-card").hidden = true;
  $("results-empty").hidden = false;
  $("report-out").hidden = true;
  pill($("report-state"), "not measured");
  pill($("graph-state"), "not traced");
  $("graph").hidden = true;
  clear($("lineage"));
  state.run = null; state.results = null; state.graph = null; state.report = null;
  describeModel(name);
  suggestReportFields(example.request);
}

/* The overlay's defaults follow the template, because "premiums" is not a
 * series every template has. Only ever a suggestion: the fields stay
 * editable and the endpoint validates them against the run. */
function suggestReportFields(request) {
  const outputs = request.outputs || [];
  const has = (name) => outputs.includes(name);
  const pick = (candidates) => candidates.filter(has).join(", ");
  $("r-inflows").value = pick(["premiums", "earned_premium"]);
  $("r-outflows").value = pick(["claims", "death_claims", "expenses", "maturities", "surrenders"]);
  $("r-units").value = pick(["pols_if", "lives_if"]) || (outputs[0] || "");
  $("r-acq").value = has("initial_expenses") ? "initial_expenses" : "";
  $("r-ra-of").value = pick(["claims", "death_claims"]);
}

async function describeModel(name) {
  const doc = await api(`/models/${name}`);
  $("model-title").textContent = `${name} — ${doc.variables.length} variables`;
  $("model-doc").textContent = (doc.doc || "").trim().split("\n\n")[0];
  const fields = doc.modelpoint_fields;
  clear($("model-facts"));
  const facts = [
    ["docstring coverage", `${(doc.coverage * 100).toFixed(0)}%`],
    ["model point fields, required", fields.required.join(", ") || "—"],
    ["model point fields, optional", fields.optional.join(", ") || "—"],
  ];
  if (fields.reflective) {
    facts.push(["found by reading the source",
      "at least one field is read by a computed name, so the required list is a lower bound"]);
  }
  for (const [term, value] of facts) {
    $("model-facts").appendChild(el("dt", { text: term }));
    $("model-facts").appendChild(el("dd", { text: value }));
  }

  const host = $("vars");
  clear(host);
  for (const variable of doc.variables) {
    const summary = el("summary", {},
      el("span", { class: "name", text: variable.name }),
      variable.assumption ? el("span", { class: "pill", text: variable.assumption }) : null,
      variable.pooled ? el("span", { class: "pill", text: "pooled" }) : null,
      el("span", { class: "doc", text: (variable.doc || "").trim().split("\n")[0] || "no docstring" }));
    const details = el("details", { class: "var" }, summary,
      el("pre", { text: variable.source || "" }),
      el("div", { class: "edges", id: `edges-${variable.name}` }));
    host.appendChild(details);
  }
  $("vars-note").textContent =
    `Every @var, its docstring, its assumption binding and its source. `
    + `${Math.round(doc.coverage * 100)}% carry a docstring.`;
}

// ── running ─────────────────────────────────────────────────────────────

function currentRequest() {
  const box = $("request");
  try {
    const parsed = JSON.parse(box.value);
    box.classList.remove("bad");
    return parsed;
  } catch (error) {
    box.classList.add("bad");
    throw new Error(`the request is not JSON: ${error.message}`);
  }
}

function logLine(...parts) {
  $("events").appendChild(el("div", {}, ...parts));
  $("events").scrollTop = $("events").scrollHeight;
}

/* Only the stream writes states. The 202's body carries one too, but by the
 * time the response lands the worker may already have moved on — logging it
 * as well printed the run's state twice and out of order. */
function logEvent(event) {
  const stamp = (event.finished_at || event.submitted_at || "").slice(11, 19);
  logLine(`${stamp}  `, el("b", { text: event.state }),
    event.executor ? `  executor ${event.executor}` : "");
}

function renderRunFacts() {
  const run = state.run;
  const host = $("run-facts");
  clear(host);
  const facts = [
    ["run_id", run.run_id],
    ["state", run.state],
    ["model", `${run.model}, ${run.n_modelpoints} model points, ${run.proj_len} periods`],
  ];
  if (run.executor) facts.push(["executor", run.executor]);
  if (run.results_digest) facts.push(["results digest", run.results_digest]);
  if (run.engine_version) facts.push(["engine", run.engine_version]);
  for (const [term, value] of facts) {
    host.appendChild(el("dt", { text: term }));
    host.appendChild(el("dd", { text: value }));
  }
}

async function submit() {
  showError($("request-error"), null);
  clear($("idempotency"));
  let request;
  try { request = currentRequest(); } catch (error) { return showError($("request-error"), error); }
  $("submit").disabled = true;
  pill($("request-state"), "submitting", "live");
  try {
    const accepted = await postJSON("/runs", request);
    state.run = accepted;
    $("run-card").hidden = false;
    clear($("events"));
    logLine("          ", el("b", { text: "POST /runs" }), "  202 Accepted");
    renderRunFacts();
    pill($("request-state"), "accepted (202)", "live");
  } catch (error) {
    pill($("request-state"), `rejected (${error.status || "?"})`, "bad");
    showError($("request-error"), error);
  } finally {
    $("submit").disabled = false;
  }
}

/* The identifier is a fingerprint of the inputs, not a ticket — so the same
 * question asked twice, written differently, is the same run and costs
 * nothing the second time. Worth doing rather than claiming. */
async function resubmitReordered() {
  if (!state.run) return showError($("request-error"), new Error("submit a run first"));
  let request;
  try { request = currentRequest(); } catch (error) { return showError($("request-error"), error); }
  const reordered = {};
  for (const key of Object.keys(request).reverse()) reordered[key] = request[key];
  const before = state.run.run_id;
  const again = await postJSON("/runs", reordered);
  const same = again.run_id === before;
  clear($("idempotency"));
  $("idempotency").appendChild(el("div", { class: "banner " + (same ? "good" : "bad") },
    el("b", { text: same ? "Same run. " : "Different run. " }),
    same
      ? "The keys went up in the reverse order and the identifier did not move: "
        + "it is a fingerprint of what was asked, so the engine recognised the "
        + "question and did no second computation."
      : "The identifier moved, which it should not have.",
    el("div", { class: "num small muted", style: "margin-top:4px" },
      `${before}\n${again.run_id}`)));
}

async function loadResults(runId) {
  // aggregate=true: the engine's own reduction, not one done in this file.
  const payload = await api(`/runs/${runId}/results?aggregate=true`);
  state.results = payload;
  const series = payload.outputs.map((name) => ({ name, values: payload.results[name] }));
  $("results-empty").hidden = true;
  $("results-card").hidden = false;
  $("results-table-card").hidden = false;
  lineChart($("results-chart"), $("results-legend"), $("results-readout"), series);
  seriesTable($("results-table"), series, Math.max(...series.map((s) => s.values.length)));
  $("results-digest-note").textContent =
    `Aggregated across the block by the engine. The digest ${payload.results_digest} `
    + "covers the per-model-point arrays, which is what the registry fingerprinted — "
    + "drop the aggregate flag to fetch those.";
  pill($("request-state"), "succeeded", "good");
}

// ── the dependency graph ────────────────────────────────────────────────

async function traceGraph() {
  showError($("graph-error"), null);
  let request;
  try { request = currentRequest(); } catch (error) { return showError($("graph-error"), error); }
  const length = Number($("trace-length").value) || 3;
  pill($("graph-state"), "tracing", "live");
  try {
    const graph = await postJSON(
      `/models/${state.model}/graph?trace_length=${length}&check_settled=true`, request);
    state.graph = graph;
    drawGraph(graph);
    annotateVariables(graph);
    pill($("graph-state"),
      graph.settled
        ? `${graph.order.length} variables · settled at ${graph.trace_length}`
        : `unsettled: a longer trace found more`,
      graph.settled ? "good" : "warn");
  } catch (error) {
    pill($("graph-state"), "failed", "bad");
    showError($("graph-error"), error);
  }
}

/* Layered left-to-right: a variable sits one column right of everything it
 * reads in the same period. Cross-period edges impose no column constraint —
 * that value was computed last period — which is exactly why they are the
 * dashed ones. */
function layout(graph) {
  const sameParents = new Map(graph.order.map((n) => [n, []]));
  for (const edge of graph.edges) {
    if (edge.offset === 0 && edge.from !== edge.to) sameParents.get(edge.to).push(edge.from);
  }
  const depth = new Map();
  for (const name of graph.order) {           // topological, so parents are done
    const parents = sameParents.get(name) || [];
    depth.set(name, parents.length ? Math.max(...parents.map((p) => depth.get(p) ?? 0)) + 1 : 0);
  }
  const columns = new Map();
  for (const name of graph.order) {
    const d = depth.get(name);
    if (!columns.has(d)) columns.set(d, []);
    columns.get(d).push(name);
  }
  const NODE_W = 160, NODE_H = 24, GAP_X = 60, GAP_Y = 12, PAD = 18;
  const position = new Map();
  let width = PAD, height = 0;
  for (const [d, names] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    const x = PAD + d * (NODE_W + GAP_X);
    names.forEach((name, i) => position.set(name, { x, y: PAD + i * (NODE_H + GAP_Y) }));
    width = Math.max(width, x + NODE_W + PAD);
    height = Math.max(height, PAD + names.length * (NODE_H + GAP_Y) + PAD);
  }
  return { position, width, height, NODE_W, NODE_H };
}

function drawGraph(graph) {
  const { position, width, height, NODE_W, NODE_H } = layout(graph);
  const host = $("graph");
  host.hidden = false;
  clear(host);
  const svg = el("svg:svg", {
    class: "graph", width, height, viewBox: `0 0 ${width} ${height}`,
  });

  const edgeNodes = [];
  for (const edge of graph.edges) {
    const from = position.get(edge.from), to = position.get(edge.to);
    if (!from || !to) continue;
    let path;
    if (edge.from === edge.to) {                     // self-recursion
      const x = from.x + NODE_W / 2, y = from.y;
      path = `M ${x - 12} ${y} C ${x - 20} ${y - 26}, ${x + 20} ${y - 26}, ${x + 12} ${y}`;
    } else {
      const x1 = from.x + NODE_W, y1 = from.y + NODE_H / 2;
      const x2 = to.x, y2 = to.y + NODE_H / 2;
      const dx = Math.max(24, (x2 - x1) / 2);
      path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
    }
    const node = el("svg:path", {
      class: "edge" + (edge.offset === 0 ? "" : " lag"), d: path,
    });
    node.dataset.from = edge.from;
    node.dataset.to = edge.to;
    edgeNodes.push(node);
    svg.appendChild(node);
  }

  const pooled = new Set(graph.pooled);
  const nodes = new Map();
  for (const [name, at] of position) {
    const group = el("svg:g", {
      class: "node" + (pooled.has(name) ? " pooled" : ""),
      onclick: () => highlight(name),
    },
      el("svg:rect", { x: at.x, y: at.y, width: NODE_W, height: NODE_H }),
      el("svg:text", {
        x: at.x + NODE_W / 2, y: at.y + NODE_H / 2 + 4, "text-anchor": "middle",
        text: name.length > 21 ? name.slice(0, 20) + "…" : name,
      }),
      el("svg:title", { text: name }));
    nodes.set(name, group);
    svg.appendChild(group);
  }

  function highlight(name) {
    const lineage = graph.lineage[name] || { inputs_of: [], affected_by: [] };
    const upstream = new Set(lineage.inputs_of);
    const downstream = new Set(lineage.affected_by);
    for (const [other, group] of nodes) {
      group.classList.remove("sel", "up", "down", "dim");
      if (other === name) group.classList.add("sel");
      else if (upstream.has(other)) group.classList.add("up");
      else if (downstream.has(other)) group.classList.add("down");
      else group.classList.add("dim");
    }
    const lit = new Set([name, ...upstream, ...downstream]);
    for (const edge of edgeNodes) {
      const hot = lit.has(edge.dataset.from) && lit.has(edge.dataset.to);
      edge.classList.toggle("hot", hot && (edge.dataset.to === name || edge.dataset.from === name));
      edge.classList.toggle("dim", !hot);
    }
    clear($("lineage"));
    $("lineage").appendChild(el("span", {},
      el("b", { text: name }), " depends on ", el("b", { text: String(upstream.size) }),
      " variables and moves ", el("b", { text: String(downstream.size) }),
      ". ",
      el("button", {
        class: "ghost", style: "padding:1px 8px;font-size:11.5px",
        onclick: () => { clearHighlight(); }, text: "clear",
      })));
  }

  function clearHighlight() {
    for (const group of nodes.values()) group.classList.remove("sel", "up", "down", "dim");
    for (const edge of edgeNodes) edge.classList.remove("hot", "dim");
    clear($("lineage"));
  }

  host.appendChild(svg);
  clear($("lineage"));
  $("lineage").textContent =
    `${graph.order.length} variables, ${graph.edges.length} edges, reaching back `
    + `${graph.horizon} period${graph.horizon === 1 ? "" : "s"}. `
    + `Traced over ${graph.trace_length} periods`
    + (graph.settled === null ? "." : graph.settled
      ? "; a trace four times longer found the same graph."
      : "; a longer trace found MORE — the graph below is incomplete.");
}

/* The formula browser's other half: what each variable reads and what reads
 * it, filled in from the same trace. */
function annotateVariables(graph) {
  const reads = new Map(graph.order.map((n) => [n, []]));
  const readBy = new Map(graph.order.map((n) => [n, []]));
  for (const edge of graph.edges) {
    reads.get(edge.to).push(edge.offset === 0 ? edge.from : `${edge.from} [t${edge.offset}]`);
    if (!readBy.get(edge.from).includes(edge.to)) readBy.get(edge.from).push(edge.to);
  }
  for (const name of graph.order) {
    const host = $(`edges-${name}`);
    if (!host) continue;
    clear(host);
    host.appendChild(el("div", {}, "reads ", el("code", { text: reads.get(name).join(", ") || "—" })));
    host.appendChild(el("div", {}, "read by ", el("code", { text: readBy.get(name).join(", ") || "—" })));
  }
}

// ── the IFRS 17 overlay ─────────────────────────────────────────────────

const asList = (value) => value.split(",").map((s) => s.trim()).filter(Boolean);

async function measure() {
  showError($("report-error"), null);
  if (!state.run || state.run.state !== "succeeded") {
    return showError($("report-error"), new Error(
      "measure a run that has succeeded — submit one on the Run tab first"));
  }
  const spec = {
    inflows: asList($("r-inflows").value),
    outflows: asList($("r-outflows").value),
    coverage: { units: $("r-units").value.trim(), discount: $("r-discount-units").checked },
    discount_rate: Number($("r-rate").value),
  };
  if ($("r-acq").value.trim()) spec.acquisition = { series: $("r-acq").value.trim() };
  if ($("r-ra-of").value.trim() && Number($("r-ra").value)) {
    spec.risk_adjustment = { percent_of: $("r-ra-of").value.trim(), margin: Number($("r-ra").value) };
  }
  pill($("report-state"), "measuring", "live");
  try {
    const report = await postJSON(`/runs/${state.run.run_id}/reports/ifrs17`, spec);
    state.report = report;
    renderReport(report);
    pill($("report-state"), report.onerous ? "onerous group" : "profitable group",
      report.onerous ? "warn" : "good");
  } catch (error) {
    pill($("report-state"), `rejected (${error.status || "?"})`, "bad");
    $("report-out").hidden = true;
    showError($("report-error"), error);
  }
}

/* An exact reconciliation is the interesting case and "0.00e+0" hides it. */
function residual(difference) {
  return difference === 0 ? "exactly zero" : difference.toExponential(2);
}

function renderReport(report) {
  $("report-out").hidden = false;
  const statement = report.statement;
  const reconciliation = report.reconciliation;

  // Accounting moves profit between periods and cannot create it, so total
  // profit over a run-off is the group's undiscounted net cash. Shown as the
  // check it is, with the residual, rather than asserted in prose.
  const scale = Math.max(1, Math.abs(reconciliation.net_cash));
  const exact = Math.abs(reconciliation.difference) <= 1e-9 * scale;
  clear($("report-banner"));
  $("report-banner").appendChild(el("div", { class: "banner " + (exact ? "good" : "bad") },
    el("b", { text: report.onerous ? "Onerous at inception. " : "Profitable at inception. " }),
    report.onerous
      ? "There is no negative CSM to hold a loss, so it went to profit and loss "
        + "on day one and a later improvement must extinguish it before any margin rebuilds."
      : `The whole margin went into the CSM — ${num(statement.csm[0])} — and is `
        + "released as service is provided, so writing this business reported nothing today.",
    el("div", { style: "margin-top:7px" },
      "Total profit ", el("span", { class: "num", text: num(reconciliation.total_profit) }),
      " against undiscounted net cash ", el("span", { class: "num", text: num(reconciliation.net_cash) }),
      " — a residual of ",
      el("span", { class: "num", text: residual(reconciliation.difference) }),
      exact ? ". " : ", which is larger than float noise on a total of this size. ",
      "Closing CSM ", el("span", { class: "num", text: num(reconciliation.closing_csm) }), ".")));

  lineChart($("csm-chart"), $("csm-legend"), $("csm-readout"), [
    { name: "csm", values: statement.csm },
    { name: "csm_release", values: statement.csm_release },
    { name: "csm_accreted", values: statement.csm_accreted },
    { name: "loss_component", values: statement.loss_component },
    { name: "risk_adjustment", values: statement.risk_adjustment },
    { name: "liability", values: statement.liability },
  ], { area: true, hidden: ["risk_adjustment", "liability"] });

  lineChart($("pl-chart"), $("pl-legend"), $("pl-readout"), [
    { name: "insurance_revenue", values: statement.insurance_revenue },
    { name: "insurance_service_expenses", values: statement.insurance_service_expenses },
    { name: "insurance_service_result", values: statement.insurance_service_result },
    { name: "insurance_finance_expense", values: statement.insurance_finance_expense },
    { name: "profit", values: statement.profit },
  ]);

  const columns = ["csm", "csm_accreted", "csm_release", "loss_component",
    "insurance_revenue", "insurance_service_expenses", "insurance_service_result",
    "insurance_finance_expense", "profit", "fulfilment_cashflows", "liability"];
  seriesTable($("report-table"),
    columns.map((name) => ({ name, values: statement[name] })),
    report.periods + 1);
}

// ── wiring ──────────────────────────────────────────────────────────────

for (const tab of document.querySelectorAll("nav.tabs button")) {
  tab.addEventListener("click", () => {
    for (const other of document.querySelectorAll("nav.tabs button")) {
      const on = other === tab;
      other.setAttribute("aria-selected", String(on));
      $(`tab-${other.dataset.tab}`).hidden = !on;
    }
  });
}

$("submit").addEventListener("click", submit);
$("resubmit").addEventListener("click", () => resubmitReordered().catch(
  (error) => showError($("request-error"), error)));
$("reset").addEventListener("click", () => {
  if (state.example) $("request").value = prettyRequest(state.example.request);
});
$("trace").addEventListener("click", traceGraph);
$("measure").addEventListener("click", measure);

boot().catch((error) => {
  document.body.appendChild(el("div", { class: "err", style: "margin:20px" },
    String(error.message || error)));
});
