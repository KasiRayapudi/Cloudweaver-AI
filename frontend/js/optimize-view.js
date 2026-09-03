/**
 * Optimisation view.
 *
 * Shows what would make the design better, as distinct from the Validation
 * tab which shows what is wrong with it. The distinction matters: a finding
 * blocks or endangers a deployment, a recommendation is a judgement call the
 * user gets to make with the cost in front of them.
 *
 * Nothing here is computed in the browser. Every recommendation, its priority
 * and its cost delta come from the backend's optimiser, so the page and a
 * generated report cannot disagree.
 */

import { clear, el, formatCurrency, icon } from "./ui.js";

const PRIORITY_ORDER = ["critical", "high", "medium", "low"];

const PRIORITY_META = {
  critical: { label: "Critical", badge: "error", blurb: "Address before deploying" },
  high: { label: "High", badge: "warning", blurb: "Worth doing now" },
  medium: { label: "Medium", badge: "info", blurb: "Plan it in" },
  low: { label: "Low", badge: "neutral", blurb: "Consider when convenient" },
};

const CATEGORY_META = {
  security: { label: "Security", icon: "shield" },
  cost: { label: "Cost", icon: "chart" },
  reliability: { label: "Reliability", icon: "layers" },
  performance: { label: "Performance", icon: "bolt" },
  networking: { label: "Networking", icon: "route" },
  operations: { label: "Operations", icon: "server" },
  compliance: { label: "Compliance", icon: "shield" },
};

const DIFFICULTY_LABEL = {
  trivial: "Rephrase the prompt",
  moderate: "Some work",
  involved: "Significant work",
};

export function renderOptimize(result, ctx = {}) {
  const recommendations = result.recommendations || [];
  const summary = result.optimization || { total: 0 };

  if (!recommendations.length) {
    return el("div", { class: "state" }, [
      el("div", {
        class: "state__icon",
        style: { background: "var(--success-bg)", color: "var(--success)" },
      }, [icon("check", 20)]),
      el("div", { class: "state__title", text: "Nothing to improve" }),
      el("p", {
        class: "state__message",
        text: "Every optimisation rule passed against this design. That is a "
            + "statement about the rules as much as the architecture — the "
            + "optimiser only reports what it can check.",
      }),
    ]);
  }

  let categoryFilter = "all";
  const listHost = el("div", { class: "stack stack--tight" });

  function paint() {
    clear(listHost);
    const visible = categoryFilter === "all"
      ? recommendations
      : recommendations.filter((r) => r.category === categoryFilter);

    // Grouped by priority so the reading order is the acting order.
    for (const priority of PRIORITY_ORDER) {
      const items = visible.filter((r) => r.priority === priority);
      if (!items.length) continue;
      const meta = PRIORITY_META[priority];
      listHost.append(
        el("div", { class: "opt-group" }, [
          el("div", { class: "opt-group__head" }, [
            el("span", { class: `badge badge--${meta.badge}`, text: meta.label }),
            el("span", { class: "opt-group__blurb", text: meta.blurb }),
            el("span", { class: "opt-group__count tabular", text: String(items.length) }),
          ]),
          ...items.map((item) => card(item, result, ctx)),
        ]),
      );
    }

    if (!visible.length) {
      listHost.append(
        el("p", { class: "opt-empty", text: "No recommendations in this category." }),
      );
    }
  }

  const categories = [...new Set(recommendations.map((r) => r.category))].sort();

  const filters = el("div", { class: "opt-filters", role: "group",
    "aria-label": "Filter by category" }, [
    filterChip("all", `All ${recommendations.length}`, true),
    ...categories.map((category) =>
      filterChip(
        category,
        `${CATEGORY_META[category]?.label || category} `
          + `${recommendations.filter((r) => r.category === category).length}`,
        false,
      ),
    ),
  ]);

  function filterChip(value, label, pressed) {
    return el("button", {
      class: "chip chip--filter",
      type: "button",
      "aria-pressed": String(pressed),
      onClick: (event) => {
        categoryFilter = value;
        for (const other of event.currentTarget.parentElement.children) {
          other.setAttribute("aria-pressed", String(other === event.currentTarget));
        }
        paint();
      },
    }, [
      value !== "all" && icon(CATEGORY_META[value]?.icon || "info", 13),
      el("span", { text: label }),
    ]);
  }

  paint();

  const net = (summary.additional_monthly_spend_usd || 0)
    - (summary.potential_monthly_saving_usd || 0);

  return el("div", { class: "stack" }, [
    el("div", { class: "price-row" }, [
      statTile("Recommendations", String(summary.total), "across every pillar"),
      statTile(
        "Potential saving",
        formatCurrency(summary.potential_monthly_saving_usd || 0),
        "per month, if all savings adopted",
        "price-card--saving",
      ),
      statTile(
        "Added spend",
        formatCurrency(summary.additional_monthly_spend_usd || 0),
        "per month, if all additions adopted",
      ),
      statTile(
        "Net if all adopted",
        `${net >= 0 ? "+" : "−"}${formatCurrency(Math.abs(net))}`,
        net >= 0 ? "more per month, for a better design" : "less per month",
        net >= 0 ? "" : "price-card--saving",
      ),
    ]),

    el("div", { class: "callout callout--info" }, [
      icon("info"),
      el("div", {}, [
        el("strong", { text: "These are suggestions, not changes" }),
        el("p", {
          text: "Nothing here has been applied. The generated Terraform "
              + "contains only what you asked for plus its mandatory "
              + "dependencies — adopting a recommendation means changing your "
              + "prompt or editing the code yourself.",
        }),
      ]),
    ]),

    filters,
    listHost,
  ]);
}

function statTile(label, value, hint, tone = "") {
  return el("div", { class: `price-card ${tone}` }, [
    el("span", { class: "price-card__label", text: label }),
    el("span", { class: "price-card__value tabular", text: value }),
    el("span", { class: "price-card__hint", text: hint }),
  ]);
}

function card(item, result, ctx) {
  const meta = CATEGORY_META[item.category] || { label: item.category, icon: "info" };
  const saving = item.monthly_delta_usd < 0;
  const free = item.monthly_delta_usd === 0;

  const body = el("div", { class: "opt-card__detail", hidden: true });
  let built = false;

  const head = el("button", {
    class: "opt-card__head",
    type: "button",
    "aria-expanded": "false",
    onClick: () => {
      const open = body.hidden;
      if (open && !built) {
        built = true;
        body.append(
          el("p", { class: "opt-card__reason", text: item.reason }),
          el("div", { class: "opt-card__action" }, [
            el("span", { class: "opt-card__action-label" }, [
              icon("bolt", 13), el("span", { text: "How to adopt it" }),
            ]),
            el("p", { text: item.action }),
          ]),
          el("dl", { class: "kv kv--compact opt-card__meta" }, [
            el("dt", { text: "Pillar" }),
            el("dd", { text: item.pillar }),
            el("dt", { text: "Effort" }),
            el("dd", { text: DIFFICULTY_LABEL[item.difficulty] || item.difficulty }),
            el("dt", { text: "Confidence" }),
            el("dd", { text: `${Math.round(item.confidence * 100)}%` }),
            el("dt", { text: "Rule" }),
            el("dd", { class: "mono", text: item.id }),
          ]),
          item.resources.length > 0 && el("div", { class: "opt-card__resources" }, [
            el("span", { class: "finding-card__resource-label", text: "Affects" }),
            el("div", { class: "opt-card__chips" },
              item.resources.map((id) => {
                const resource = result.spec.resources.find((r) => r.id === id);
                return el("button", {
                  class: "dep-chip dep-chip--action",
                  type: "button",
                  onClick: () => resource && ctx.onSelectResource?.(resource),
                }, [el("span", { text: resource ? resource.name : id })]);
              }),
            ),
          ]),
        );
      }
      body.hidden = !open;
      head.setAttribute("aria-expanded", String(open));
    },
  }, [
    el("span", { class: `opt-card__icon cat-icon--${item.category}` }, [icon(meta.icon, 14)]),
    el("span", { class: "opt-card__title", text: item.title }),
    el("span", {
      class: `opt-card__delta${saving ? " is-saving" : free ? " is-free" : ""}`,
      text: free
        ? "no cost"
        : `${saving ? "−" : "+"}${formatCurrency(Math.abs(item.monthly_delta_usd))}/mo`,
    }),
    el("span", { class: "finding-card__caret", "aria-hidden": "true" }),
  ]);

  return el("div", { class: `opt-card opt-card--${item.priority}` }, [head, body]);
}
