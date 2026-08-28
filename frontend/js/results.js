/**
 * Result rendering: the tabbed workspace shown after a generation.
 *
 * Every tab reads the same response object, mirroring the backend's own
 * design — one shared model, several views over it. Nothing here re-derives
 * infrastructure facts; if a number is shown, the backend computed it.
 */

import { clear, copyText, downloadText, el, formatCurrency, icon, toast } from "./ui.js";
import { store } from "./store.js";

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };
const SEVERITY_LABEL = { error: "Errors", warning: "Warnings", info: "Recommendations" };

const TABS = [
  { id: "overview", label: "Overview", icon: "home" },
  { id: "resources", label: "Resources", icon: "layers" },
  { id: "diagram", label: "Architecture", icon: "route" },
  { id: "terraform", label: "Terraform", icon: "code" },
  { id: "validation", label: "Validation", icon: "shield" },
  { id: "trace", label: "Decision trace", icon: "clock" },
  { id: "cost", label: "Cost", icon: "chart" },
  { id: "dependencies", label: "Dependencies", icon: "route" },
];

/* ==================================================================
   Skeleton shown while generating
   ================================================================== */
export function renderSkeleton(mount) {
  clear(mount).append(
    el("div", { class: "result" }, [
      el("div", { class: "stat-row" },
        Array.from({ length: 4 }, () =>
          el("div", { class: "stat" }, [
            el("div", { class: "skeleton skeleton--text", style: { width: "38%" } }),
            el("div", { class: "skeleton skeleton--title", style: { width: "62%" } }),
          ]),
        ),
      ),
      el("div", { class: "panel", style: { marginTop: "var(--space-4)" } }, [
        el("div", { class: "panel__body" }, [
          el("div", { class: "skeleton skeleton--title" }),
          el("div", { class: "skeleton skeleton--text", style: { width: "92%" } }),
          el("div", { class: "skeleton skeleton--text", style: { width: "78%" } }),
          el("div", { class: "skeleton skeleton--text", style: { width: "85%" } }),
          el("div", { class: "skeleton skeleton--block", style: { marginTop: "var(--space-4)" } }),
        ]),
      ]),
      el("p", { class: "generating-note" }, [
        el("span", { class: "btn__spinner" }),
        el("span", { text: "Extracting intent, resolving dependencies, generating Terraform…" }),
      ]),
    ]),
  );
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
    clear(inspector).append(renderInspector(resource, result));
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
  clear(inspector).append(
    el("div", { class: "state" }, [
      el("div", { class: "state__icon" }, [icon("layers", 20)]),
      el("div", { class: "state__title", text: "Select a resource" }),
      el("p", { class: "state__message", text: "See why it exists, what depends on it, and its Terraform." }),
    ]),
  );

  return el("div", { class: "stack" }, [
    filters,
    el("div", { class: "split" }, [list, inspector]),
  ]);
}

function renderInspector(resource, result) {
  const trace = result.extraction.find((entry) => entry.id === resource.id);
  const graph = result.dependency_graph.edges || {};
  const dependsOn = graph[resource.id] || [];
  const dependents = Object.entries(graph)
    .filter(([, parents]) => parents.includes(resource.id))
    .map(([id]) => id);
  const findings = result.findings.filter((f) => f.resource_id === resource.id);
  const snippet = terraformSnippet(result.terraform, resource);

  return el("div", { class: "inspector__inner" }, [
    el("header", { class: "inspector__head" }, [
      el("span", { class: `resource-row__dot cat-${categoryOf(resource.kind)}` }),
      el("div", {}, [
        el("h3", { text: resource.name }),
        el("span", { class: "inspector__kind mono", text: resource.kind }),
      ]),
    ]),

    el("dl", { class: "kv" }, [
      el("dt", { text: "Identifier" }),
      el("dd", { class: "mono", text: resource.id }),
      resource.display_name && el("dt", { text: "Name" }),
      resource.display_name && el("dd", { class: "mono", text: resource.display_name }),
      resource.external_id && el("dt", { text: "Existing id" }),
      resource.external_id && el("dd", { class: "mono", text: resource.external_id }),
      el("dt", { text: "Origin" }),
      el("dd", {}, [
        el("span", {
          class: `badge ${resource.origin === "explicit" ? "badge--brand" : "badge--neutral"}`,
          text: resource.origin === "explicit" ? "Requested" : "Mandatory dependency",
        }),
      ]),
      el("dt", { text: "Confidence" }),
      el("dd", {}, [confidenceBar(resource.confidence)]),
      resource.count > 1 && el("dt", { text: "Count" }),
      resource.count > 1 && el("dd", { class: "tabular", text: String(resource.count) }),
    ]),

    el("section", { class: "inspector__section" }, [
      el("h4", { text: "Why it exists" }),
      el("p", { class: "reason", text: resource.reason || "No reason recorded." }),
      trace?.source && el("p", { class: "evidence" }, [
        el("span", { text: "From your words: " }),
        el("q", { text: trace.source }),
      ]),
    ]),

    Object.keys(resource.properties || {}).length > 0 &&
      el("section", { class: "inspector__section" }, [
        el("h4", { text: "Configuration" }),
        el("dl", { class: "kv kv--compact" },
          Object.entries(resource.properties)
            .filter(([, value]) => typeof value !== "object")
            .flatMap(([key, value]) => [
              el("dt", { class: "mono", text: key }),
              el("dd", { class: "mono", text: String(value) }),
            ]),
        ),
      ]),

    (dependsOn.length > 0 || dependents.length > 0) &&
      el("section", { class: "inspector__section" }, [
        el("h4", { text: "Dependencies" }),
        dependsOn.length > 0 && el("p", { class: "dep-line" }, [
          el("span", { class: "dep-label", text: "Created after" }),
          ...dependsOn.map((id) => el("code", { class: "dep-chip", text: id })),
        ]),
        dependents.length > 0 && el("p", { class: "dep-line" }, [
          el("span", { class: "dep-label", text: "Required by" }),
          ...dependents.map((id) => el("code", { class: "dep-chip", text: id })),
        ]),
      ]),

    findings.length > 0 && el("section", { class: "inspector__section" }, [
      el("h4", { text: "Findings" }),
      ...findings.map((finding) => findingRow(finding, { compact: true })),
    ]),

    snippet && el("section", { class: "inspector__section" }, [
      el("div", { class: "inspector__section-head" }, [
        el("h4", { text: "Terraform" }),
        el("button", {
          class: "btn btn--ghost btn--sm",
          onClick: () => copyText(snippet, "Snippet copied"),
        }, [icon("copy", 13), el("span", { text: "Copy" })]),
      ]),
      el("pre", { class: "code code--boxed" }, [el("code", { text: snippet })]),
    ]),
  ]);
}

function confidenceBar(value = 1) {
  const percent = Math.round(value * 100);
  return el("span", { class: "confidence" }, [
    el("span", { class: "confidence__track" }, [
      el("span", { class: "confidence__fill", style: { width: `${percent}%` } }),
    ]),
    el("span", { class: "confidence__value tabular", text: `${percent}%` }),
  ]);
}

/** Pull the block for one resource out of the generated files. */
function terraformSnippet(files, resource) {
  for (const content of Object.values(files)) {
    const pattern = new RegExp(
      `^(?:resource|data)\\s+"[\\w-]+"\\s+"${resource.id}"[\\s\\S]*?^\\}`, "m",
    );
    const match = content.match(pattern);
    if (match) return match[0];
  }
  return null;
}

function categoryOf(kind) {
  if (/vm|instance|autoscaling|container|kubernetes|function|bastion|registry/.test(kind)) return "compute";
  if (/load_balancer|target_group|api_gateway|cdn|dns|certificate/.test(kind)) return "traffic";
  if (/sql|nosql|cache|warehouse/.test(kind)) return "data";
  if (/storage/.test(kind)) return "storage";
  if (/security|iam|secret|key|waf/.test(kind)) return "security";
  if (/queue|topic|event/.test(kind)) return "integration";
  if (/monitoring/.test(kind)) return "ops";
  return "network";
}

/* ==================================================================
   Diagram
   ================================================================== */
function renderDiagram(result) {
  const stage = el("div", { class: "diagram-stage", tabindex: "0",
    "aria-label": "Architecture diagram. Drag to pan, scroll to zoom." });
  stage.innerHTML = result.diagram.svg;         // generated by our own renderer

  const mermaid = el("pre", { class: "code code--boxed", hidden: true }, [
    el("code", { text: result.diagram.mermaid }),
  ]);

  let zoom = 1;
  let panX = 0;
  let panY = 0;
  let dragging = false;
  let originX = 0;
  let originY = 0;

  function apply() {
    const svg = stage.querySelector("svg");
    if (!svg) return;
    svg.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
    svg.style.transformOrigin = "top center";
    label.textContent = `${Math.round(zoom * 100)}%`;
  }

  function setZoom(next) {
    zoom = Math.min(3, Math.max(0.3, next));
    apply();
  }

  stage.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;   // plain scroll still scrolls
    event.preventDefault();
    setZoom(zoom - event.deltaY * 0.002);
  }, { passive: false });

  stage.addEventListener("pointerdown", (event) => {
    dragging = true;
    originX = event.clientX - panX;
    originY = event.clientY - panY;
    stage.setPointerCapture(event.pointerId);
    stage.style.cursor = "grabbing";
  });
  stage.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    panX = event.clientX - originX;
    panY = event.clientY - originY;
    apply();
  });
  stage.addEventListener("pointerup", (event) => {
    dragging = false;
    stage.releasePointerCapture(event.pointerId);
    stage.style.cursor = "grab";
  });

  stage.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 40 : 16;
    const moves = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (moves[event.key]) {
      event.preventDefault();
      panX += moves[event.key][0];
      panY += moves[event.key][1];
      apply();
    } else if (event.key === "+" || event.key === "=") { event.preventDefault(); setZoom(zoom + 0.15); }
    else if (event.key === "-") { event.preventDefault(); setZoom(zoom - 0.15); }
    else if (event.key === "0") { event.preventDefault(); reset(); }
  });

  function reset() { zoom = 1; panX = 0; panY = 0; apply(); }

  const label = el("span", { class: "zoom-label tabular", text: "100%" });

  const wrapper = el("div", { class: "diagram" }, [
    el("div", { class: "toolbar" }, [
      el("div", { class: "segmented", role: "group", "aria-label": "Diagram format" }, [
        el("button", { class: "segmented__option", type: "button", "aria-pressed": "true",
          onClick: (e) => setFormat(e, true) }, [el("span", { text: "Rendered" })]),
        el("button", { class: "segmented__option", type: "button", "aria-pressed": "false",
          onClick: (e) => setFormat(e, false) }, [el("span", { text: "Mermaid" })]),
      ]),
      el("span", { class: "toolbar__spacer" }),
      label,
      el("button", { class: "btn btn--ghost btn--icon btn--sm", "data-tooltip": "Zoom out",
        "aria-label": "Zoom out", onClick: () => setZoom(zoom - 0.15) }, [el("span", { text: "−" })]),
      el("button", { class: "btn btn--ghost btn--icon btn--sm", "data-tooltip": "Zoom in",
        "aria-label": "Zoom in", onClick: () => setZoom(zoom + 0.15) }, [el("span", { text: "+" })]),
      el("button", { class: "btn btn--ghost btn--sm", "data-tooltip": "Reset view",
        onClick: reset }, [el("span", { text: "Reset" })]),
      el("button", { class: "btn btn--ghost btn--sm", "data-tooltip": "Fullscreen",
        "aria-label": "Fullscreen", onClick: () => {
          if (document.fullscreenElement) document.exitFullscreen();
          else wrapper.requestFullscreen?.().catch(() => toast("Fullscreen unavailable", { variant: "warning" }));
        } }, [icon("panel", 14)]),
      el("button", { class: "btn btn--secondary btn--sm", onClick: () => {
        downloadText(result.diagram.svg, `${result.summary.name}-architecture.svg`, "image/svg+xml");
        toast("Diagram saved", { variant: "success" });
      } }, [icon("download", 14), el("span", { text: "SVG" })]),
      el("button", { class: "btn btn--secondary btn--sm", onClick: () => exportPng(result) },
        [icon("download", 14), el("span", { text: "PNG" })]),
    ]),
    stage,
    mermaid,
  ]);

  function setFormat(event, rendered) {
    for (const option of event.currentTarget.parentElement.children) {
      option.setAttribute("aria-pressed", String(option === event.currentTarget));
    }
    stage.hidden = !rendered;
    mermaid.hidden = rendered;
  }

  queueMicrotask(apply);
  return wrapper;
}

/** Rasterise the SVG in a canvas. Self-contained, so no network is involved. */
function exportPng(result) {
  const svg = result.diagram.svg;
  const sizes = svg.match(/width="([\d.]+)"\s+height="([\d.]+)"/);
  const width = Math.ceil(Number(sizes?.[1] || 1200));
  const height = Math.ceil(Number(sizes?.[2] || 800));
  const scale = 2;

  const image = new Image();
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  image.onload = () => {
    const canvas = el("canvas", { width: width * scale, height: height * scale });
    const context = canvas.getContext("2d");
    context.scale(scale, scale);
    context.drawImage(image, 0, 0);
    canvas.toBlob((png) => {
      if (!png) { toast("PNG export failed", { variant: "error" }); return; }
      const link = el("a", { href: URL.createObjectURL(png), download: `${result.summary.name}-architecture.png` });
      document.body.append(link);
      link.click();
      link.remove();
      toast("PNG saved", { variant: "success" });
    }, "image/png");
    URL.revokeObjectURL(url);
  };
  image.onerror = () => {
    URL.revokeObjectURL(url);
    toast("PNG export failed", { message: "Save the SVG instead.", variant: "error" });
  };
  image.src = url;
}

/* ==================================================================
   Terraform viewer
   ================================================================== */
const FILE_ORDER = [
  "versions.tf", "variables.tf", "locals.tf", "network.tf", "security.tf",
  "compute.tf", "data.tf", "edge.tf", "integration.tf", "iam.tf",
  "monitoring.tf", "outputs.tf", "terraform.tfvars",
];

function renderTerraform(result) {
  const names = Object.keys(result.terraform).sort((a, b) => {
    const ia = FILE_ORDER.indexOf(a);
    const ib = FILE_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });

  let active = names[0];
  let query = "";

  const fileList = el("ul", { class: "file-list", role: "tablist", "aria-label": "Generated files" });
  const viewer = el("div", { class: "viewer" });
  const meta = el("span", { class: "toolbar__meta" });

  const search = el("input", {
    class: "field field--inline",
    type: "search",
    placeholder: "Search in file…",
    "aria-label": "Search within the file",
  });
  search.addEventListener("input", () => { query = search.value; paintViewer(); });

  function paintList() {
    clear(fileList);
    for (const name of names) {
      const lines = result.terraform[name].split("\n").length;
      fileList.append(
        el("li", {}, [
          el("button", {
            class: "file-item",
            type: "button",
            role: "tab",
            "aria-selected": String(name === active),
            onClick: () => { active = name; search.value = ""; query = ""; paintList(); paintViewer(); },
          }, [
            icon(name.endsWith(".tf") ? "code" : "book", 13),
            el("span", { class: "file-item__name mono", text: name }),
            el("span", { class: "file-item__lines tabular", text: String(lines) }),
          ]),
        ]),
      );
    }
  }

  function paintViewer() {
    const content = result.terraform[active] || "";
    const lines = content.split("\n");
    const needle = query.trim().toLowerCase();
    let hits = 0;

    const gutter = el("div", { class: "viewer__gutter", "aria-hidden": "true" });
    const code = el("div", { class: "viewer__code" });

    lines.forEach((line, index) => {
      const isHit = needle && line.toLowerCase().includes(needle);
      if (isHit) hits += 1;
      gutter.append(el("span", { class: "viewer__line-no", text: String(index + 1) }));
      code.append(
        el("span", {
          class: `viewer__line${isHit ? " is-hit" : ""}`,
          text: line || " ",
        }),
      );
    });

    meta.textContent = needle
      ? `${hits} match${hits === 1 ? "" : "es"}`
      : `${lines.length} lines`;

    clear(viewer).append(gutter, code);
  }

  paintList();
  paintViewer();

  return el("div", { class: "stack" }, [
    el("div", { class: "toolbar" }, [
      search,
      meta,
      el("span", { class: "toolbar__spacer" }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        onClick: () => copyText(result.terraform[active], `${active} copied`),
      }, [icon("copy", 14), el("span", { text: "Copy file" })]),
      el("button", {
        class: "btn btn--secondary btn--sm",
        onClick: () => downloadText(result.terraform[active], active),
      }, [icon("download", 14), el("span", { text: "Download" })]),
    ]),
    el("div", { class: "split split--code" }, [fileList, viewer]),
  ]);
}

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

function renderValidation(result) {
  const { findings } = result;
  if (!findings.length) {
    return el("div", { class: "state" }, [
      el("div", { class: "state__icon", style: { background: "var(--success-bg)", color: "var(--success)" } },
        [icon("check", 20)]),
      el("div", { class: "state__title", text: "No issues found" }),
      el("p", { class: "state__message",
        text: "The design passes every structural, security, reliability and AWS constraint check." }),
    ]);
  }

  const groups = new Map();
  for (const finding of [...findings].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
  )) {
    if (!groups.has(finding.severity)) groups.set(finding.severity, []);
    groups.get(finding.severity).push(finding);
  }

  return el("div", { class: "stack" },
    [...groups].map(([severity, items]) =>
      el("section", { class: "panel" }, [
        el("div", { class: "panel__header" }, [
          el("span", { class: `dot dot--${severity}` }),
          el("span", { class: "panel__title", text: SEVERITY_LABEL[severity] }),
          el("span", { class: `badge badge--${severity === "error" ? "error" : severity === "warning" ? "warning" : "info"}`,
            text: String(items.length) }),
        ]),
        el("div", { class: "panel__body stack stack--tight" }, items.map((f) => findingRow(f))),
      ]),
    ),
  );
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
function renderCost(result) {
  const total = result.summary.estimated_monthly_cost_usd;
  const byResource = result.spec.resources
    .map((resource) => ({
      name: resource.name,
      id: resource.id,
      kind: resource.kind,
      count: resource.count,
      cost: estimateFor(resource),
    }))
    .filter((row) => row.cost > 0)
    .sort((a, b) => b.cost - a.cost);

  const max = byResource[0]?.cost || 1;

  return el("div", { class: "stack" }, [
    el("div", { class: "stat-row" }, [
      statCard("Monthly estimate", formatCurrency(total), "on-demand, before data transfer"),
      statCard("Annualised", formatCurrency(total * 12), "same rate, twelve months"),
      statCard("Priced resources", byResource.length,
        `${result.spec.resources.length - byResource.length} carry no standing charge`),
    ]),

    el("div", { class: "callout callout--info" }, [
      icon("info"),
      el("div", {}, [
        el("strong", { text: "These are order-of-magnitude figures" }),
        el("p", { text: "Static per-service rates, not live pricing. Use them to compare designs, not to forecast a bill." }),
      ]),
    ]),

    byResource.length > 0 && el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("chart", 15),
        el("span", { class: "panel__title", text: "Cost by resource" }),
      ]),
      el("div", { class: "panel__body stack stack--tight" },
        byResource.map((row) =>
          el("div", { class: "cost-row" }, [
            el("span", { class: "cost-row__name" }, [
              el("span", { class: `resource-row__dot cat-${categoryOf(row.kind)}` }),
              el("span", { text: row.name }),
              row.count > 1 && el("span", { class: "badge badge--neutral", text: `×${row.count}` }),
            ]),
            el("span", { class: "cost-row__bar" }, [
              el("span", {
                class: `cost-row__fill cat-bg-${categoryOf(row.kind)}`,
                style: { width: `${Math.max(3, (row.cost / max) * 100)}%` },
              }),
            ]),
            el("span", { class: "cost-row__value tabular", text: formatCurrency(row.cost) }),
          ]),
        ),
      ),
    ]),
  ]);
}

/** Mirrors the backend's static hints so the breakdown sums to its total. */
const COST_HINTS = {
  vm: 8, autoscaling_group: 24, nat_gateway: 33, load_balancer: 18,
  network_load_balancer: 18, gateway_load_balancer: 18,
  sql_database: 25, sql_cluster: 90, cache: 13, kubernetes_cluster: 73,
  container_service: 15, object_storage: 1, cdn: 5, function: 1,
  nosql_table: 2, data_warehouse: 180,
};

function estimateFor(resource) {
  return (COST_HINTS[resource.kind] || 0) * (resource.count || 1);
}

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
  trace: renderTrace,
  cost: renderCost,
  dependencies: renderDependencies,
};
