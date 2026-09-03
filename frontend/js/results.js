/**
 * Result rendering: the tabbed workspace shown after a generation.
 *
 * Every tab reads the same response object, mirroring the backend's own
 * design — one shared model, several views over it. Nothing here re-derives
 * infrastructure facts; if a number is shown, the backend computed it.
 */

import { createDiagramViewer } from "./diagram.js";
import { categoryOf, renderInspector, renderInspectorEmpty } from "./inspector.js";
import { renderCost } from "./cost-view.js";
import { renderOptimize } from "./optimize-view.js";
import { renderValidation } from "./validation-view.js";
import { renderTerraform } from "./terraform-view.js";
import { clear, copyText, downloadText, el, formatCurrency, icon, toast } from "./ui.js";
import { store } from "./store.js";


const TABS = [
  { id: "overview", label: "Overview", icon: "home" },
  { id: "resources", label: "Resources", icon: "layers" },
  { id: "diagram", label: "Architecture", icon: "route" },
  { id: "terraform", label: "Terraform", icon: "code" },
  { id: "validation", label: "Validation", icon: "shield" },
  { id: "optimize", label: "Optimise", icon: "bolt" },
  { id: "trace", label: "Decision trace", icon: "clock" },
  { id: "cost", label: "Cost", icon: "chart" },
  { id: "dependencies", label: "Dependencies", icon: "route" },
];

/* ==================================================================
   Skeleton shown while generating
   ================================================================== */
export const PIPELINE_STAGES = [
  { id: "extract", label: "Extracting intent", detail: "Matching services, counts, regions and refusals" },
  { id: "policy", label: "Applying policy", detail: "Only explicit resources and mandatory dependencies" },
  { id: "closure", label: "Resolving dependencies", detail: "Closing over the requirement graph" },
  { id: "terraform", label: "Generating Terraform", detail: "Typed HCL from the shared model" },
  { id: "diagram", label: "Drawing architecture", detail: "Same model, second view" },
  { id: "validate", label: "Validating", detail: "AWS constraints Terraform cannot see" },
];

/**
 * Progress pipeline shown during generation.
 *
 * The backend is a single call that completes in tens of milliseconds, so
 * these stages are NOT polled -- there is no per-stage endpoint to poll, and
 * inventing one would mean adding artificial delay to make a progress bar look
 * busy. Instead the stages advance on a short timer purely as an explanation
 * of what the pipeline does, and every remaining stage is marked complete the
 * instant the real response arrives. Nothing here ever claims a stage
 * finished before the work actually did.
 */
export function renderPipeline(mount) {
  clear(mount);

  const rows = new Map();
  const list = el("ol", { class: "pipeline-progress", "aria-label": "Generation progress" });

  for (const stage of PIPELINE_STAGES) {
    const marker = el("span", { class: "pipeline-progress__marker" });
    const row = el("li", { class: "pipeline-progress__row", dataset: { state: "waiting" } }, [
      marker,
      el("span", { class: "pipeline-progress__body" }, [
        el("span", { class: "pipeline-progress__label", text: stage.label }),
        el("span", { class: "pipeline-progress__detail", text: stage.detail }),
      ]),
    ]);
    rows.set(stage.id, { row, marker });
    list.append(row);
  }

  const status = el("p", {
    class: "pipeline-progress__status",
    role: "status",
    "aria-live": "polite",
    text: "Starting…",
  });

  mount.append(
    el("div", { class: "result" }, [
      el("div", { class: "panel" }, [
        el("div", { class: "panel__header" }, [
          el("span", { class: "btn__spinner" }),
          el("span", { class: "panel__title", text: "Generating infrastructure" }),
        ]),
        el("div", { class: "panel__body" }, [list, status]),
      ]),
    ]),
  );

  let index = 0;
  function advance() {
    if (index >= PIPELINE_STAGES.length) return;
    const stage = PIPELINE_STAGES[index];
    const entry = rows.get(stage.id);
    if (index > 0) rows.get(PIPELINE_STAGES[index - 1].id).row.dataset.state = "done";
    entry.row.dataset.state = "active";
    status.textContent = stage.label + "…";
    index += 1;
  }

  advance();
  const timer = setInterval(advance, 130);

  return {
    /** Mark everything complete; called when the response actually lands. */
    finish(durationMs) {
      clearInterval(timer);
      for (const { row } of rows.values()) row.dataset.state = "done";
      status.textContent = `Completed in ${Math.round(durationMs)} ms`;
    },
    fail(message) {
      clearInterval(timer);
      const current = PIPELINE_STAGES[Math.max(0, index - 1)];
      rows.get(current.id).row.dataset.state = "failed";
      status.textContent = message;
    },
    stop: () => clearInterval(timer),
  };
}

/* ==================================================================
   Entry point
   ================================================================== */
export function renderResult(mount, result, handlers = {}) {
  clear(mount);

  const state = { tab: store.state.activeTab || "overview", selected: null };
  const panelHost = el("div", { class: "tab-panels" });

  const tablist = el("div", { class: "tabs", role: "tablist", "aria-label": "Result views" });
  const buttons = new Map();

  for (const tab of TABS) {
    const count = tabCount(tab.id, result);
    const button = el("button", {
      class: "tab",
      role: "tab",
      id: `tab-${tab.id}`,
      type: "button",
      "aria-selected": String(state.tab === tab.id),
      "aria-controls": `panel-${tab.id}`,
      onClick: () => select(tab.id),
    }, [
      icon(tab.icon, 15),
      el("span", { text: tab.label }),
      count !== null &&
        el("span", {
          class: `tab__count${tab.id === "validation" && errorCount(result) ? " tab__count--alert" : ""}`,
          text: String(count),
        }),
    ]);
    buttons.set(tab.id, button);
    tablist.append(button);
  }

  // Roving focus: arrow keys move between tabs, as the pattern requires.
  tablist.addEventListener("keydown", (event) => {
    const ids = TABS.map((t) => t.id);
    const index = ids.indexOf(state.tab);
    if (event.key === "ArrowRight") select(ids[(index + 1) % ids.length], true);
    else if (event.key === "ArrowLeft") select(ids[(index - 1 + ids.length) % ids.length], true);
    else return;
    event.preventDefault();
  });

  function select(id, focus = false) {
    state.tab = id;
    store.set({ activeTab: id });
    for (const [tabId, button] of buttons) {
      button.setAttribute("aria-selected", String(tabId === id));
      button.tabIndex = tabId === id ? 0 : -1;
    }
    if (focus) buttons.get(id).focus();
    paint();
  }

  function paint() {
    clear(panelHost);
    const panel = el("div", {
      class: "tab-panel",
      id: `panel-${state.tab}`,
      role: "tabpanel",
      "aria-labelledby": `tab-${state.tab}`,
      tabindex: "0",
    }, [renderers[state.tab](result, { ...handlers, select, state })]);
    panelHost.append(panel);

    // Now that the panel is in the document and has a measurable box, fit the
    // diagram. Switching to this tab from another one lands here too, so a
    // diagram rendered while hidden is still fitted the moment it is shown.
    if (state.tab === "diagram" && state.viewer) {
      setTimeout(() => state.viewer.fit({ animate: false }), 0);
    }
  }

  mount.append(
    el("div", { class: "result" }, [
      renderHeader(result, handlers),
      tablist,
      panelHost,
    ]),
  );
  select(state.tab);
}

function errorCount(result) {
  return result.findings.filter((f) => f.severity === "error").length;
}

function tabCount(id, result) {
  switch (id) {
    case "resources": return result.spec.resources.length;
    case "terraform": return Object.keys(result.terraform).length;
    case "validation": return result.findings.length;
    case "optimize": return (result.recommendations || []).length;
    case "trace": return result.extraction.length;
    default: return null;
  }
}

/* ==================================================================
   Header
   ================================================================== */
function renderHeader(result, handlers) {
  const { summary } = result;
  return el("header", { class: "result-header" }, [
    el("div", { class: "result-header__main" }, [
      el("h2", { class: "result-header__name", text: summary.name }),
      el("p", { class: "result-header__summary", text: summary.description }),
    ]),
    el("div", { class: "result-header__actions" }, [
      el("button", {
        class: "btn btn--secondary btn--sm",
        onClick: () => copyText(
          JSON.stringify(result.spec, null, 2), "Shared model copied",
        ),
      }, [icon("copy", 14), el("span", { text: "Copy JSON" })]),
      el("button", {
        class: "btn btn--primary btn--sm",
        onClick: () => handlers.onDownload?.(),
      }, [icon("download", 14), el("span", { text: "Download project" })]),
    ]),
  ]);
}

/* ==================================================================
   Overview
   ================================================================== */
function statCard(label, value, hint, tone = "") {
  return el("div", { class: `stat ${tone}` }, [
    el("span", { class: "stat__label", text: label }),
    el("span", { class: "stat__value tabular", text: String(value) }),
    hint && el("span", { class: "stat__hint", text: hint }),
  ]);
}

function renderOverview(result) {
  const { summary, spec, findings } = result;
  const errors = findings.filter((f) => f.severity === "error");
  const warnings = findings.filter((f) => f.severity === "warning");
  const required = spec.resources.filter((r) => r.origin === "implied").length;

  return el("div", { class: "stack" }, [
    el("div", { class: "stat-row" }, [
      statCard("Resources", summary.resource_count,
        `${summary.resource_count - required} requested · ${required} required`),
      statCard("Terraform files", summary.file_count, `${summary.region} · ${summary.environment}`),
      statCard("Estimated cost", formatCurrency(summary.estimated_monthly_cost_usd), "per month, on-demand"),
      statCard("Findings", findings.length,
        errors.length ? `${errors.length} must be fixed` : "nothing blocking",
        errors.length ? "stat--danger" : warnings.length ? "stat--warning" : "stat--success"),
    ]),

    errors.length > 0 && el("div", { class: "callout callout--error" }, [
      icon("alert"),
      el("div", {}, [
        el("strong", { text: `${errors.length} issue${errors.length === 1 ? "" : "s"} would fail at deploy` }),
        el("p", { text: errors[0].message }),
      ]),
    ]),

    spec.exclusions?.length > 0 && el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("x", 15),
        el("span", { class: "panel__title", text: "Deliberately excluded" }),
      ]),
      el("ul", { class: "plain-list panel__body" },
        spec.exclusions.map((exclusion) =>
          el("li", {}, [
            el("span", { class: "badge badge--neutral", text: exclusion.kind }),
            el("span", { text: ` ${exclusion.reason}` }),
          ]),
        ),
      ),
    ]),

    spec.assumptions?.length > 0 && el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("info", 15),
        el("span", { class: "panel__title", text: "Assumptions made" }),
        el("span", { class: "badge badge--neutral", text: String(spec.assumptions.length) }),
      ]),
      el("ul", { class: "plain-list panel__body" },
        spec.assumptions.map((note) => el("li", { text: note })),
      ),
    ]),

    spec.warnings?.length > 0 && el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("alert", 15),
        el("span", { class: "panel__title", text: "Limitations" }),
      ]),
      el("ul", { class: "plain-list panel__body" },
        spec.warnings.map((note) => el("li", { text: note })),
      ),
    ]),
  ]);
}

/* ==================================================================
   Resources + inspector
   ================================================================== */
function renderResources(result, ctx) {
  const list = el("div", { class: "resource-list" });
  const inspector = el("aside", { class: "inspector", "aria-live": "polite" });
  const search = el("input", {
    class: "field",
    type: "search",
    placeholder: "Filter by name, kind or id…",
    "aria-label": "Filter resources",
  });

  let filter = "";
  let originFilter = "all";

  function matches(resource) {
    const haystack = `${resource.name} ${resource.kind} ${resource.id}`.toLowerCase();
    const byText = !filter || haystack.includes(filter);
    const byOrigin = originFilter === "all" || resource.origin === originFilter;
    return byText && byOrigin;
  }

  function show(resource) {
    ctx.state.selected = resource.id;
    clear(inspector).append(renderInspector(resource, result, { onSelectResource: show }));
    for (const row of list.querySelectorAll(".resource-row")) {
      row.setAttribute("aria-current", String(row.dataset.id === resource.id));
    }
  }

  function paint() {
    clear(list);
    const visible = result.spec.resources.filter(matches);

    if (!visible.length) {
      list.append(
        el("div", { class: "state" }, [
          el("div", { class: "state__icon" }, [icon("search", 20)]),
          el("div", { class: "state__title", text: "No matching resources" }),
          el("p", { class: "state__message", text: "Try a different term, or clear the filter." }),
        ]),
      );
      return;
    }

    for (const resource of visible) {
      list.append(
        el("button", {
          class: "resource-row",
          type: "button",
          dataset: { id: resource.id },
          "aria-current": String(ctx.state.selected === resource.id),
          onClick: () => show(resource),
        }, [
          el("span", { class: `resource-row__dot cat-${categoryOf(resource.kind)}` }),
          el("span", { class: "resource-row__main" }, [
            el("span", { class: "resource-row__name", text: resource.name }),
            el("span", { class: "resource-row__id mono", text: resource.id }),
          ]),
          resource.count > 1 && el("span", { class: "badge badge--neutral", text: `×${resource.count}` }),
          resource.external_id && el("span", { class: "badge badge--info", text: "existing" }),
          el("span", {
            class: `badge ${resource.origin === "explicit" ? "badge--brand" : "badge--neutral"}`,
            text: resource.origin === "explicit" ? "requested" : "required",
          }),
        ]),
      );
    }
  }

  search.addEventListener("input", () => { filter = search.value.trim().toLowerCase(); paint(); });

  const filters = el("div", { class: "filter-row" }, [
    search,
    el("div", { class: "segmented", role: "group", "aria-label": "Filter by origin" },
      [["all", "All"], ["explicit", "Requested"], ["implied", "Required"]].map(([value, label]) =>
        el("button", {
          class: "segmented__option",
          type: "button",
          "aria-pressed": String(originFilter === value),
          onClick: (event) => {
            originFilter = value;
            for (const option of event.currentTarget.parentElement.children) {
              option.setAttribute("aria-pressed", String(option === event.currentTarget));
            }
            paint();
          },
        }, [el("span", { text: label })]),
      ),
    ),
  ]);

  paint();
  clear(inspector).append(renderInspectorEmpty());

  return el("div", { class: "stack" }, [
    filters,
    el("div", { class: "split" }, [list, inspector]),
  ]);
}

/* ==================================================================
   Diagram
   ================================================================== */
function renderDiagram(result, ctx) {
  // A dedicated module owns pan, zoom, fit and export; this only wires the
  // viewer to the inspector so a node click opens the same panel the resource
  // list opens.
  const drawer = el("aside", { class: "node-drawer", hidden: true, "aria-live": "polite" });

  const viewer = createDiagramViewer(result, {
    onSelectResource: (resource) => {
      drawer.hidden = false;
      clear(drawer).append(
        el("div", { class: "node-drawer__head" }, [
          el("span", { class: "node-drawer__title", text: "Resource inspector" }),
          el("button", {
            class: "btn btn--ghost btn--icon btn--sm",
            "aria-label": "Close inspector",
            onClick: () => { drawer.hidden = true; },
          }, [icon("x", 14)]),
        ]),
        renderInspector(resource, result, {
          onSelectResource: (next) => viewer.highlight(next.id),
        }),
      );
    },
  });

  ctx.state.viewer = viewer;

  // Facts about the design, above the picture of it. Everything here is a
  // value the API already returned; nothing is recomputed in the browser.
  const { summary, findings } = result;
  const errors = findings.filter((f) => f.severity === "error").length;
  const warnings = findings.filter((f) => f.severity === "warning").length;

  const facts = [
    ["Architecture", summary.name],
    ["Region", summary.region],
    ["Environment", summary.environment],
    ["Resources", String(summary.resource_count)],
    ["Terraform files", String(summary.file_count)],
    ["Est. monthly", formatCurrency(summary.estimated_monthly_cost_usd)],
    ["Validation", errors ? `${errors} error${errors === 1 ? "" : "s"}`
      : warnings ? `${warnings} warning${warnings === 1 ? "" : "s"}` : "Clean"],
    ["Generated in", `${Math.round(summary.duration_ms)} ms`],
  ];

  const summaryBar = el("dl", {
    class: "arch-summary",
    "aria-label": "Architecture summary",
  }, facts.flatMap(([label, value], index) => [
    el("div", {
      class: `arch-summary__cell${
        index === 6 ? (errors ? " is-error" : warnings ? " is-warning" : " is-clean") : ""
      }`,
    }, [
      el("dt", { text: label }),
      el("dd", { class: index >= 3 ? "tabular" : "", text: value }),
    ]),
  ]));

  return el("div", { class: "stack" }, [
    summaryBar,
    el("div", { class: "diagram-layout" }, [viewer.element, drawer]),
  ]);
}

/* ==================================================================
   Terraform viewer
   ================================================================== */
/* ==================================================================
   Validation
   ================================================================== */
function findingRow(finding, { compact = false } = {}) {
  return el("div", { class: `finding finding--${finding.severity}${compact ? " finding--compact" : ""}` }, [
    el("span", { class: `dot dot--${finding.severity}` }),
    el("div", { class: "finding__body" }, [
      el("p", { class: "finding__message", text: finding.message }),
      el("div", { class: "finding__meta" }, [
        el("code", { text: finding.code }),
        finding.resource_id && el("code", { text: finding.resource_id }),
      ]),
    ]),
  ]);
}

/* ==================================================================
   Decision trace
   ================================================================== */
function renderTrace(result) {
  return el("div", { class: "stack" }, [
    el("p", { class: "lede",
      text: "Every resource, and the step that put it there. Requested resources come from your words; required ones from a policy rule that made them mandatory." }),
    el("ol", { class: "timeline" },
      result.extraction.map((entry) => {
        const resource = result.spec.resources.find((r) => r.id === entry.id);
        return el("li", { class: `timeline__item timeline__item--${entry.origin}` }, [
          el("span", { class: "timeline__marker" }),
          el("div", { class: "timeline__card" }, [
            el("div", { class: "timeline__head" }, [
              el("strong", { text: entry.resource }),
              el("span", {
                class: `badge ${entry.origin === "explicit" ? "badge--brand" : "badge--neutral"}`,
                text: entry.origin === "explicit" ? "Requested" : "Dependency",
              }),
              el("span", { class: "timeline__confidence tabular", text: `${Math.round(entry.confidence * 100)}%` }),
            ]),
            el("p", { class: "timeline__reason", text: entry.reason }),
            entry.source && el("p", { class: "evidence" }, [
              el("span", { text: "Source: " }), el("q", { text: entry.source }),
            ]),
            resource?.external_id && el("p", { class: "evidence",
              text: `Looked up, not created: ${resource.external_id}` }),
          ]),
        ]);
      }),
    ),
  ]);
}

/* ==================================================================
   Cost
   ================================================================== */
/* ==================================================================
   Dependencies
   ================================================================== */
function renderDependencies(result) {
  const { edges, creation_order: order, cycles } = result.dependency_graph;

  return el("div", { class: "stack" }, [
    cycles.length > 0
      ? el("div", { class: "callout callout--error" }, [
          icon("alert"),
          el("div", {}, [
            el("strong", { text: "Circular dependency" }),
            el("p", { text: cycles.map((cycle) => cycle.join(" → ")).join("; ") }),
          ]),
        ])
      : el("div", { class: "callout callout--success" }, [
          icon("check"),
          el("div", {}, [
            el("strong", { text: "Acyclic" }),
            el("p", { text: "Terraform can resolve this graph; every dependency precedes what needs it." }),
          ]),
        ]),

    el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("route", 15),
        el("span", { class: "panel__title", text: "Creation order" }),
        el("span", { class: "badge badge--neutral", text: String(order.length) }),
      ]),
      el("ol", { class: "order-list panel__body" },
        order.map((id, index) => {
          const parents = edges[id] || [];
          return el("li", { class: "order-item" }, [
            el("span", { class: "order-item__index tabular", text: String(index + 1) }),
            el("code", { class: "order-item__id", text: id }),
            parents.length > 0 && el("span", { class: "order-item__after" }, [
              el("span", { text: "after " }),
              ...parents.map((parent) => el("code", { class: "dep-chip", text: parent })),
            ]),
          ]);
        }),
      ),
    ]),
  ]);
}

/* ================================================================== */
const renderers = {
  overview: renderOverview,
  resources: renderResources,
  diagram: renderDiagram,
  terraform: renderTerraform,
  validation: renderValidation,
  optimize: renderOptimize,
  trace: renderTrace,
  cost: renderCost,
  dependencies: renderDependencies,
};
