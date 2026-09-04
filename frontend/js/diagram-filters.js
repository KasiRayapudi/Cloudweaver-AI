/**
 * Diagram search, filtering and dependency focus.
 *
 * All three work by adding classes to the SVG that the backend already drew,
 * never by re-laying it out. That matters for two reasons: the layout stays
 * deterministic — filtering the view cannot move a box — and an export taken
 * while a filter is active is still the full, correct diagram.
 *
 * Dimming rather than hiding is deliberate. A node removed from the canvas
 * takes its edges with it and leaves connectors pointing at nothing; a dimmed
 * node keeps the shape of the architecture visible while the matches stand
 * out against it.
 */

import { clear, el, icon } from "./ui.js";

/** Service groupings offered as layer toggles. */
export const LAYERS = [
  { id: "compute", label: "Compute", icon: "server",
    match: /vm|autoscaling|container_service|kubernetes|function|bastion|registry/ },
  { id: "traffic", label: "Traffic", icon: "route",
    match: /load_balancer|target_group|api_gateway|cdn|dns_zone|certificate/ },
  { id: "data", label: "Data", icon: "layers",
    match: /sql_|nosql_|cache|warehouse/ },
  { id: "storage", label: "Storage", icon: "book",
    match: /object_storage|file_storage/ },
  { id: "security", label: "Security", icon: "shield",
    match: /security_group|iam_role|secret_store|key_management|waf/ },
  { id: "network", label: "Networking", icon: "route",
    match: /vpc|subnet|gateway|route_table|elastic_ip/ },
  { id: "integration", label: "Integration", icon: "bolt",
    match: /queue|topic|event_bus/ },
  { id: "ops", label: "Monitoring", icon: "chart", match: /monitoring/ },
];

function layerOf(kind) {
  return LAYERS.find((layer) => layer.match.test(kind))?.id || "network";
}

/**
 * @param {object} options
 * @param {HTMLElement} options.stage  the element holding the SVG
 * @param {object} options.result      the generation result
 * @param {Function} options.onFocus   called with a resource id when focused
 */
export function createFilters({ stage, result, onFocus }) {
  const spec = result.spec;

  // Index once. Everything below is a set operation over these.
  const byId = new Map(spec.resources.map((r) => [r.id, r]));
  const groups = new Map(
    [...stage.querySelectorAll("[data-resource-id]")].map(
      (node) => [node.dataset.resourceId, node],
    ),
  );

  const graph = result.dependency_graph?.edges || {};
  const dependents = new Map();
  for (const [child, parents] of Object.entries(graph)) {
    for (const parent of parents) {
      if (!dependents.has(parent)) dependents.set(parent, []);
      dependents.get(parent).push(child);
    }
  }

  const state = {
    query: "",
    hidden: new Set(),      // layer ids the user has switched off
    focused: null,          // resource id, or null
  };

  /* ---------------- which resources are currently of interest ---------- */

  function matchesQuery(id) {
    if (!state.query) return true;
    const resource = byId.get(id);
    const haystack = [
      id,
      resource?.name,
      resource?.kind,
      resource?.display_name,
      resource?.external_id,
    ].filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(state.query);
  }

  function inVisibleLayer(id) {
    const resource = byId.get(id);
    if (!resource) return true;                // the internet node has no kind
    return !state.hidden.has(layerOf(resource.kind));
  }

  // Neighbours as *drawn*, not just as declared in the dependency graph.
  // The graph holds creation-order dependencies; the picture also shows data
  // and traffic flows. Focusing on a database and dimming the very edge that
  // reaches it would be the opposite of useful.
  const drawn = new Map();
  for (const path of stage.querySelectorAll("path.edge[data-from]")) {
    const from = path.dataset.from;
    const to = path.dataset.to;
    if (!drawn.has(from)) drawn.set(from, new Set());
    if (!drawn.has(to)) drawn.set(to, new Set());
    drawn.get(from).add(to);
    drawn.get(to).add(from);
  }

  /** The focused resource plus everything it touches, in either direction. */
  function focusSet() {
    if (!state.focused) return null;
    const set = new Set([state.focused]);
    for (const parent of graph[state.focused] || []) set.add(parent);
    for (const child of dependents.get(state.focused) || []) set.add(child);
    for (const neighbour of drawn.get(state.focused) || []) set.add(neighbour);
    return set;
  }

  /* ---------------- apply ---------------------------------------------- */

  function apply() {
    const focus = focusSet();
    let matches = 0;

    for (const [id, node] of groups) {
      const searching = Boolean(state.query);
      const hit = matchesQuery(id);
      const visible = inVisibleLayer(id);
      const inFocus = !focus || focus.has(id);

      const dim = !visible || !inFocus || (searching && !hit);
      node.classList.toggle("is-dimmed", dim);
      node.classList.toggle("is-match", searching && hit && visible && inFocus);
      node.classList.toggle("is-focus-root", id === state.focused);
      if (searching && hit && visible) matches += 1;
    }

    // Edges follow their endpoints: an edge is only fully drawn when both
    // ends are still of interest, otherwise it fades with them.
    for (const path of stage.querySelectorAll("path.edge")) {
      const from = path.dataset.from;
      const to = path.dataset.to;
      if (!from || !to) continue;
      const live =
        !groups.get(from)?.classList.contains("is-dimmed")
        && !groups.get(to)?.classList.contains("is-dimmed");
      path.classList.toggle("is-dimmed", !live);
      path.classList.toggle(
        "is-focus-edge",
        Boolean(focus) && live && (from === state.focused || to === state.focused),
      );
    }

    stage.classList.toggle("is-filtered", Boolean(
      state.query || state.hidden.size || state.focused,
    ));
    return matches;
  }

  /* ---------------- public operations ---------------------------------- */

  function search(query) {
    state.query = (query || "").trim().toLowerCase();
    return apply();
  }

  function toggleLayer(layerId) {
    if (state.hidden.has(layerId)) state.hidden.delete(layerId);
    else state.hidden.add(layerId);
    apply();
    return !state.hidden.has(layerId);
  }

  function focus(resourceId) {
    state.focused = state.focused === resourceId ? null : resourceId;
    apply();
    if (state.focused) onFocus?.(state.focused);
    return state.focused;
  }

  function reset() {
    state.query = "";
    state.hidden.clear();
    state.focused = null;
    apply();
  }

  /** Resources matching the current query, for the search results list. */
  function results() {
    if (!state.query) return [];
    return spec.resources
      .filter((r) => matchesQuery(r.id) && inVisibleLayer(r.id))
      .slice(0, 8);
  }

  return {
    search, toggleLayer, focus, reset, results, apply,
    get state() { return state; },
    layerOf,
  };
}

/**
 * The filter bar: search box, layer toggles, focus indicator.
 * Kept separate from the viewer so the viewer stays about geometry.
 */
export function renderFilterBar(filters, { onSelectResource } = {}) {
  const count = el("span", { class: "filter-count tabular" });
  const resultList = el("div", { class: "filter-results", hidden: true });

  const search = el("input", {
    class: "field field--inline",
    type: "search",
    placeholder: "Search resources…",
    "aria-label": "Search resources in the diagram",
    autocomplete: "off",
  });

  function paintResults() {
    const found = filters.results();
    clear(resultList);
    resultList.hidden = found.length === 0;
    for (const resource of found) {
      resultList.append(
        el("button", {
          class: "filter-result",
          type: "button",
          onClick: () => {
            onSelectResource?.(resource);
            filters.focus(resource.id);
            paintFocus();
          },
        }, [
          el("span", { class: `resource-row__dot cat-${filters.layerOf(resource.kind)}` }),
          el("span", { class: "filter-result__name", text: resource.name }),
          el("span", { class: "filter-result__id mono", text: resource.id }),
        ]),
      );
    }
  }

  search.addEventListener("input", () => {
    const matches = filters.search(search.value);
    count.textContent = search.value.trim()
      ? `${matches} match${matches === 1 ? "" : "es"}`
      : "";
    paintResults();
  });

  const focusChip = el("button", {
    class: "chip chip--focus",
    type: "button",
    hidden: true,
    onClick: () => { filters.focus(filters.state.focused); paintFocus(); },
  });

  function paintFocus() {
    const focused = filters.state.focused;
    focusChip.hidden = !focused;
    if (focused) {
      clear(focusChip).append(
        icon("route", 12),
        el("span", { text: `Focused on ${focused}` }),
        icon("x", 12),
      );
    }
  }

  const layerToggles = el("div", { class: "layer-toggles", role: "group",
    "aria-label": "Show or hide layers" },
    LAYERS.map((layer) =>
      el("button", {
        class: "chip chip--layer",
        type: "button",
        "aria-pressed": "true",
        title: `Toggle ${layer.label}`,
        onClick: (event) => {
          const on = filters.toggleLayer(layer.id);
          event.currentTarget.setAttribute("aria-pressed", String(on));
        },
      }, [
        el("span", { class: `layer-dot cat-${layer.id}` }),
        el("span", { text: layer.label }),
      ]),
    ),
  );

  const bar = el("div", { class: "filter-bar" }, [
    el("div", { class: "filter-bar__row" }, [
      el("div", { class: "filter-search" }, [search, resultList]),
      count,
      focusChip,
      el("span", { class: "toolbar__spacer" }),
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        onClick: () => {
          search.value = "";
          count.textContent = "";
          resultList.hidden = true;
          filters.reset();
          paintFocus();
          for (const button of layerToggles.children) {
            button.setAttribute("aria-pressed", "true");
          }
        },
      }, [icon("x", 13), el("span", { text: "Clear filters" })]),
    ]),
    layerToggles,
  ]);

  return { element: bar, paintFocus };
}
