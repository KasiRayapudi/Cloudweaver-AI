/**
 * Cost dashboard.
 *
 * The figures come from the backend's own static per-service rates, mirrored
 * here only so the breakdown can attribute the total it already returned.
 * Nothing on this page invents a number: the monthly total shown in the
 * cards is `summary.estimated_monthly_cost_usd` exactly as the API sent it.
 *
 * The donut is hand-drawn SVG rather than a charting library. One ring of
 * eight segments needs about forty lines of arc maths; a library would be
 * two orders of magnitude more bytes for a chart this simple, and would have
 * to be themed to match the palette anyway.
 */

import { el, formatCurrency, icon } from "./ui.js";

/** Mirrors MONTHLY_COST_HINTS in backend/app/engine/validator.py. */
const COST_HINTS = {
  vm: 8, autoscaling_group: 24, nat_gateway: 33, load_balancer: 18,
  network_load_balancer: 18, gateway_load_balancer: 18,
  sql_database: 25, sql_cluster: 90, cache: 13, kubernetes_cluster: 73,
  container_service: 15, object_storage: 1, cdn: 5, function: 1,
  nosql_table: 2, data_warehouse: 180,
};

/** Groups a resource kind into the service family shown in the donut. */
const FAMILY = [
  [/^(vm|autoscaling_group|bastion)$/, "Compute", "compute"],
  [/^(container_service|kubernetes_cluster|container_registry)$/, "Containers", "compute"],
  [/^function$/, "Serverless", "compute"],
  [/^(sql_database|sql_cluster|nosql_table|data_warehouse)$/, "Databases", "data"],
  [/^cache$/, "Cache", "data"],
  [/^(object_storage|file_storage)$/, "Storage", "storage"],
  [/^(load_balancer|network_load_balancer|gateway_load_balancer|target_group)$/, "Load balancing", "traffic"],
  [/^(cdn|api_gateway|dns_zone|certificate|waf)$/, "Edge", "traffic"],
  [/^(nat_gateway|elastic_ip)$/, "Networking", "network"],
  [/^(queue|topic|event_bus)$/, "Integration", "integration"],
];

function familyOf(kind) {
  for (const [pattern, label, category] of FAMILY) {
    if (pattern.test(kind)) return { label, category };
  }
  return { label: "Other", category: "network" };
}

function estimateFor(resource) {
  return (COST_HINTS[resource.kind] || 0) * (resource.count || 1);
}

/* ------------------------------------------------------------------
   Donut
   ------------------------------------------------------------------ */

const SIZE = 168;
const RADIUS = 66;
const THICKNESS = 26;

/** Point on the ring at a given fraction of the way round, from 12 o'clock. */
function pointAt(fraction, radius) {
  const angle = fraction * Math.PI * 2 - Math.PI / 2;
  return [
    SIZE / 2 + Math.cos(angle) * radius,
    SIZE / 2 + Math.sin(angle) * radius,
  ];
}

function donut(segments, total) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${SIZE} ${SIZE}`);
  svg.setAttribute("width", String(SIZE));
  svg.setAttribute("height", String(SIZE));
  svg.setAttribute("class", "donut");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    `Cost split: ${segments.map((s) => `${s.label} ${Math.round(s.cost / total * 100)}%`).join(", ")}`,
  );

  let cursor = 0;
  for (const segment of segments) {
    const share = segment.cost / total;
    // A segment covering everything cannot be drawn as an arc: the start and
    // end points coincide and the path collapses. Draw a full ring instead.
    if (share >= 0.9999) {
      const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ring.setAttribute("cx", String(SIZE / 2));
      ring.setAttribute("cy", String(SIZE / 2));
      ring.setAttribute("r", String(RADIUS - THICKNESS / 2));
      ring.setAttribute("fill", "none");
      ring.setAttribute("stroke-width", String(THICKNESS));
      ring.setAttribute("class", `donut__seg cat-stroke-${segment.category}`);
      svg.append(ring);
      break;
    }

    const [x1, y1] = pointAt(cursor, RADIUS);
    const [x2, y2] = pointAt(cursor + share, RADIUS);
    const [x3, y3] = pointAt(cursor + share, RADIUS - THICKNESS);
    const [x4, y4] = pointAt(cursor, RADIUS - THICKNESS);
    const large = share > 0.5 ? 1 : 0;

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", [
      `M ${x1.toFixed(2)} ${y1.toFixed(2)}`,
      `A ${RADIUS} ${RADIUS} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`,
      `L ${x3.toFixed(2)} ${y3.toFixed(2)}`,
      `A ${RADIUS - THICKNESS} ${RADIUS - THICKNESS} 0 ${large} 0 ${x4.toFixed(2)} ${y4.toFixed(2)}`,
      "Z",
    ].join(" "));
    path.setAttribute("class", `donut__seg cat-fill-${segment.category}`);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${segment.label}: ${formatCurrency(segment.cost)}/month`;
    path.append(title);
    svg.append(path);

    cursor += share;
  }
  return svg;
}

/* ------------------------------------------------------------------
   Cards
   ------------------------------------------------------------------ */

function priceCard(label, value, hint, tone = "") {
  return el("div", { class: `price-card ${tone}` }, [
    el("span", { class: "price-card__label", text: label }),
    el("span", { class: "price-card__value tabular", text: value }),
    hint && el("span", { class: "price-card__hint", text: hint }),
  ]);
}

export function renderCost(result) {
  const total = result.summary.estimated_monthly_cost_usd || 0;

  const priced = result.spec.resources
    .map((resource) => ({
      name: resource.name,
      id: resource.id,
      kind: resource.kind,
      count: resource.count || 1,
      cost: estimateFor(resource),
      ...familyOf(resource.kind),
    }))
    .filter((row) => row.cost > 0)
    .sort((a, b) => b.cost - a.cost);

  if (!priced.length) {
    return el("div", { class: "state" }, [
      el("div", { class: "state__icon" }, [icon("chart", 20)]),
      el("div", { class: "state__title", text: "No standing charges" }),
      el("p", {
        class: "state__message",
        text: "Nothing in this design carries a fixed monthly cost. "
            + "Usage-based services are billed on what you actually consume.",
      }),
    ]);
  }

  // Families, largest first, so the donut reads clockwise by size.
  const families = new Map();
  for (const row of priced) {
    const existing = families.get(row.label) || { label: row.label, category: row.category, cost: 0, count: 0 };
    existing.cost += row.cost;
    existing.count += 1;
    families.set(row.label, existing);
  }
  const segments = [...families.values()].sort((a, b) => b.cost - a.cost);
  const attributed = segments.reduce((sum, s) => sum + s.cost, 0);
  const top = priced[0];
  const max = top.cost;

  return el("div", { class: "stack" }, [
    /* --- headline cards --- */
    el("div", { class: "price-row" }, [
      priceCard("Monthly", formatCurrency(total), "on-demand, before data transfer", "price-card--primary"),
      priceCard("Daily", formatCurrency(total / 30.44), "averaged over a month"),
      priceCard("Annual", formatCurrency(total * 12), "same rate, twelve months"),
      priceCard("Priced resources", String(priced.length),
        `${result.spec.resources.length - priced.length} carry no standing charge`),
    ]),

    /* --- most expensive --- */
    el("div", { class: "callout callout--info" }, [
      icon("chart"),
      el("div", {}, [
        el("strong", { text: `${top.name} is the largest single cost` }),
        el("p", {
          text: `${formatCurrency(top.cost)} per month — `
              + `${Math.round((top.cost / attributed) * 100)}% of the attributed total`
              + `${top.count > 1 ? `, across ${top.count} instances` : ""}.`,
        }),
      ]),
    ]),

    /* --- donut + families --- */
    el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("chart", 15),
        el("span", { class: "panel__title", text: "Cost by service family" }),
      ]),
      el("div", { class: "panel__body cost-split" }, [
        el("div", { class: "cost-split__chart" }, [
          donut(segments, attributed),
          el("div", { class: "donut__centre" }, [
            el("span", { class: "donut__total tabular", text: formatCurrency(attributed) }),
            el("span", { class: "donut__caption", text: "per month" }),
          ]),
        ]),
        el("ul", { class: "legend" },
          segments.map((segment) =>
            el("li", { class: "legend__row" }, [
              el("span", { class: `legend__swatch cat-${segment.category}` }),
              el("span", { class: "legend__label", text: segment.label }),
              el("span", { class: "legend__share tabular",
                text: `${Math.round((segment.cost / attributed) * 100)}%` }),
              el("span", { class: "legend__value tabular", text: formatCurrency(segment.cost) }),
            ]),
          ),
        ),
      ]),
    ]),

    /* --- per-resource ranking --- */
    el("section", { class: "panel" }, [
      el("div", { class: "panel__header" }, [
        icon("layers", 15),
        el("span", { class: "panel__title", text: "Every priced resource" }),
        el("span", { class: "badge badge--neutral", text: String(priced.length) }),
      ]),
      el("div", { class: "panel__body stack stack--tight" },
        priced.map((row, index) =>
          el("div", { class: `cost-row${index === 0 ? " cost-row--top" : ""}` }, [
            el("span", { class: "cost-row__name" }, [
              el("span", { class: `resource-row__dot cat-${row.category}` }),
              el("span", { text: row.name }),
              row.count > 1 && el("span", { class: "badge badge--neutral", text: `×${row.count}` }),
            ]),
            el("span", { class: "cost-row__bar" }, [
              el("span", {
                class: `cost-row__fill cat-bg-${row.category}`,
                style: { width: `${Math.max(3, (row.cost / max) * 100)}%` },
              }),
            ]),
            el("span", { class: "cost-row__value tabular", text: formatCurrency(row.cost) }),
          ]),
        ),
      ),
    ]),

    /* --- the honest caveat --- */
    el("div", { class: "callout" }, [
      icon("info"),
      el("div", {}, [
        el("strong", { text: "These are order-of-magnitude figures" }),
        el("p", {
          text: "Static per-service rates, not live AWS pricing, and they exclude "
              + "data transfer, request charges and storage growth. Use them to "
              + "compare two designs, not to forecast a bill.",
        }),
      ]),
    ]),
  ]);
}
