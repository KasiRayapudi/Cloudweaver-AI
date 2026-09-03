/**
 * Resource inspector.
 *
 * Reads the backend's `explanations` array rather than re-deriving anything.
 * The previous version pulled the Terraform block out with a regex in the
 * browser and guessed a category from the kind string; both are now computed
 * server-side, so the panel, the API and a generated report quote identical
 * text by construction rather than by coincidence.
 */

import { clear, copyText, el, formatCurrency, icon } from "./ui.js";

/** Mirrors the diagram's category colours so a resource reads the same everywhere. */
export function categoryOf(kind) {
  if (/vm|instance|autoscaling|container|kubernetes|function|bastion|registry/.test(kind)) return "compute";
  if (/load_balancer|target_group|api_gateway|cdn|dns|certificate/.test(kind)) return "traffic";
  if (/sql|nosql|cache|warehouse/.test(kind)) return "data";
  if (/storage/.test(kind)) return "storage";
  if (/security|iam|secret|key|waf/.test(kind)) return "security";
  if (/queue|topic|event/.test(kind)) return "integration";
  if (/monitoring/.test(kind)) return "ops";
  return "network";
}

function section(title, ...children) {
  const kept = children.flat().filter(Boolean);
  if (!kept.length) return null;
  return el("section", { class: "inspector__section" }, [
    el("h4", { text: title }),
    ...kept,
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

function chips(ids, result, onSelect) {
  return el("div", { class: "opt-card__chips" },
    ids.map((id) => {
      const resource = result.spec.resources.find((r) => r.id === id);
      return el("button", {
        class: "dep-chip dep-chip--action",
        type: "button",
        title: resource ? resource.name : id,
        onClick: () => resource && onSelect?.(resource),
      }, [
        el("span", { class: `resource-row__dot cat-${categoryOf(resource?.kind || "")}` }),
        el("span", { text: id }),
      ]);
    }),
  );
}

export function renderInspector(resource, result, ctx = {}) {
  const explanation =
    (result.explanations || []).find((e) => e.resource_id === resource.id) || null;

  // Falls back to the resource itself if explanations are absent, so an older
  // cached response cannot blank the panel.
  const data = explanation || {
    requested: resource.origin === "explicit",
    reason: resource.reason,
    confidence: resource.confidence,
    depends_on: [],
    required_by: [],
    alternatives: [],
    best_practices: [],
    finding_codes: [],
    recommendation_ids: [],
    monthly_cost_usd: 0,
    pillar: "",
  };

  const findings = (result.findings || []).filter(
    (f) => f.resource_id === resource.id,
  );

  return el("div", { class: "inspector__inner" }, [
    el("header", { class: "inspector__head" }, [
      el("span", { class: `resource-row__dot cat-${categoryOf(resource.kind)}` }),
      el("div", {}, [
        el("h3", { text: resource.name }),
        el("span", { class: "inspector__kind mono", text: resource.kind }),
      ]),
    ]),

    /* --- headline facts --- */
    el("div", { class: "inspector__facts" }, [
      el("span", {
        class: `badge ${data.requested ? "badge--brand" : "badge--neutral"}`,
        text: data.requested ? "Requested" : "Dependency",
      }),
      data.pillar && el("span", { class: "badge badge--info", text: data.pillar }),
      resource.external_id && el("span", { class: "badge badge--warning", text: "Existing" }),
      data.monthly_cost_usd > 0 && el("span", {
        class: "badge badge--neutral tabular",
        text: `${formatCurrency(data.monthly_cost_usd)}/mo`,
      }),
    ]),

    el("dl", { class: "kv" }, [
      el("dt", { text: "Identifier" }),
      el("dd", { class: "mono", text: resource.id }),
      resource.display_name && el("dt", { text: "Name" }),
      resource.display_name && el("dd", { class: "mono", text: resource.display_name }),
      resource.external_id && el("dt", { text: "Existing id" }),
      resource.external_id && el("dd", { class: "mono", text: resource.external_id }),
      resource.count > 1 && el("dt", { text: "Count" }),
      resource.count > 1 && el("dd", { class: "tabular", text: String(resource.count) }),
      el("dt", { text: "Confidence" }),
      el("dd", {}, [confidenceBar(data.confidence)]),
    ]),

    /* --- why it exists --- */
    section("Why it exists",
      el("p", { class: "reason", text: data.reason || "No reason recorded." }),
      data.evidence && el("p", { class: "evidence" }, [
        el("span", { text: "From your words: " }),
        el("q", { text: data.evidence }),
      ]),
      data.rule && el("div", { class: "rule-cite" }, [
        el("span", { class: "rule-cite__label", text: "Policy rule" }),
        el("code", { text: data.rule }),
      ]),
      data.triggered_by && el("p", { class: "evidence" }, [
        el("span", { text: "Required by " }),
        el("code", { class: "dep-chip", text: data.triggered_by }),
      ]),
    ),

    /* --- configuration --- */
    Object.keys(resource.properties || {}).length > 0 &&
      section("Configuration",
        el("dl", { class: "kv kv--compact" },
          Object.entries(resource.properties)
            .filter(([, value]) => typeof value !== "object")
            .flatMap(([key, value]) => [
              el("dt", { class: "mono", text: key }),
              el("dd", { class: "mono", text: String(value) }),
            ]),
        ),
      ),

    /* --- relationships --- */
    (data.depends_on.length > 0 || data.required_by.length > 0) &&
      section("Dependencies",
        data.depends_on.length > 0 && el("div", { class: "dep-block" }, [
          el("span", { class: "dep-label", text: "Created after" }),
          chips(data.depends_on, result, ctx.onSelectResource),
        ]),
        data.required_by.length > 0 && el("div", { class: "dep-block" }, [
          el("span", { class: "dep-label", text: "Required by" }),
          chips(data.required_by, result, ctx.onSelectResource),
        ]),
      ),

    /* --- service knowledge --- */
    section("Security",
      data.security_notes && el("p", { class: "note", text: data.security_notes }),
    ),
    section("Networking",
      data.networking_notes && el("p", { class: "note", text: data.networking_notes }),
    ),
    section("Operations",
      data.operational_notes && el("p", { class: "note", text: data.operational_notes }),
    ),

    data.best_practices?.length > 0 &&
      section("Best practices",
        el("ul", { class: "note-list" },
          data.best_practices.map((item) => el("li", { text: item })),
        ),
      ),

    data.alternatives?.length > 0 &&
      section("Alternatives",
        el("ul", { class: "alt-list" },
          data.alternatives.map((option) =>
            el("li", {}, [
              el("strong", { text: option.service }),
              el("p", { text: option.when }),
            ]),
          ),
        ),
      ),

    /* --- this design's issues --- */
    findings.length > 0 &&
      section("Findings",
        ...findings.map((finding) =>
          el("div", { class: `finding finding--${finding.severity} finding--compact` }, [
            el("span", { class: `dot dot--${finding.severity}` }),
            el("div", { class: "finding__body" }, [
              el("p", { class: "finding__message", text: finding.message }),
            ]),
          ]),
        ),
      ),

    data.recommendation_ids?.length > 0 &&
      section("Suggested improvements",
        el("ul", { class: "note-list" },
          data.recommendation_ids.map((id) => {
            const rec = (result.recommendations || []).find((r) => r.id === id);
            return el("li", { text: rec ? rec.title : id });
          }),
        ),
      ),

    /* --- code --- */
    data.terraform_snippet &&
      el("section", { class: "inspector__section" }, [
        el("div", { class: "inspector__section-head" }, [
          el("h4", { text: `Terraform · ${data.terraform_file}` }),
          el("button", {
            class: "btn btn--ghost btn--sm",
            type: "button",
            onClick: () => copyText(data.terraform_snippet, "Snippet copied"),
          }, [icon("copy", 13), el("span", { text: "Copy" })]),
        ]),
        el("pre", { class: "code code--boxed" }, [
          el("code", { text: data.terraform_snippet }),
        ]),
      ]),
  ]);
}

export function renderInspectorEmpty() {
  return el("div", { class: "state" }, [
    el("div", { class: "state__icon" }, [icon("layers", 20)]),
    el("div", { class: "state__title", text: "Select a resource" }),
    el("p", {
      class: "state__message",
      text: "See why it exists, which policy rule required it, what depends "
          + "on it, its cost, and the alternatives worth considering.",
    }),
  ]);
}

export { clear };
