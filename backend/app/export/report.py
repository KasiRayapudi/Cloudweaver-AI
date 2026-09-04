"""Architecture report: a printable document describing one generated design.

Emitted as HTML with print rules rather than as a binary PDF. Three reasons,
in order of weight:

1. **Vector output.** Printing HTML to PDF keeps text selectable and the
   diagram vector at any zoom. A hand-rolled PDF writer would embed a raster
   of the diagram and lose both.
2. **No native dependency.** A server-side PDF or PNG needs a rasteriser such
   as libcairo. This project removed Graphviz for exactly that reason, and a
   native binary would also break the serverless deployment.
3. **Testable.** The document is a string, so its content can be asserted in
   the test suite. A binary would only be checkable by opening it.

The cost is that the reader chooses "Save as PDF" in the print dialog. That is
stated on the page rather than left as a surprise.

Everything here is read from the generation result. The report states no fact
the engines did not produce, which is what makes it usable as an appendix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from app.engine.explain import Explanation
from app.engine.optimizer import Recommendation, summarise
from app.generators.diagram.layout import LayoutEngine
from app.generators.diagram.svg_renderer import SvgRenderer
from app.models.ir import InfrastructureSpec, Kind, Origin

#: Sections built from resource kinds, so a section appears only when the
#: design has something to say in it. An empty "Analytics Overview" heading
#: is worse than no heading.
DOMAINS: tuple[tuple[str, tuple[Kind, ...]], ...] = (
    ("Compute", (Kind.VM, Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE,
                 Kind.KUBERNETES_CLUSTER, Kind.FUNCTION, Kind.BASTION,
                 Kind.CONTAINER_REGISTRY)),
    ("Networking", (Kind.VPC, Kind.SUBNET_PUBLIC, Kind.SUBNET_PRIVATE,
                    Kind.INTERNET_GATEWAY, Kind.NAT_GATEWAY, Kind.ROUTE_TABLE,
                    Kind.ELASTIC_IP)),
    ("Traffic and edge", (Kind.LOAD_BALANCER, Kind.NETWORK_LOAD_BALANCER,
                          Kind.GATEWAY_LOAD_BALANCER, Kind.TARGET_GROUP,
                          Kind.API_GATEWAY, Kind.CDN, Kind.DNS_ZONE)),
    ("Data", (Kind.SQL_DATABASE, Kind.SQL_CLUSTER, Kind.NOSQL_TABLE,
              Kind.CACHE, Kind.DATA_WAREHOUSE)),
    ("Storage", (Kind.OBJECT_STORAGE, Kind.FILE_STORAGE)),
    ("Security", (Kind.SECURITY_GROUP, Kind.IAM_ROLE, Kind.SECRET_STORE,
                  Kind.KEY_MANAGEMENT, Kind.WAF, Kind.CERTIFICATE)),
    ("Integration", (Kind.QUEUE, Kind.TOPIC, Kind.EVENT_BUS)),
    ("Monitoring", (Kind.MONITORING,)),
)

_STYLE = """
:root { --ink: #111827; --muted: #4b5563; --faint: #6b7280;
        --rule: #d1d5db; --accent: #0f7d73; --danger: #b91c1c;
        --warn: #b45309; --ok: #047857; }

* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  font: 10.5pt/1.55 "Inter", "Segoe UI", system-ui, sans-serif;
  color: var(--ink); background: #fff;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.page { max-width: 190mm; margin: 0 auto; padding: 14mm 0 20mm; }

h1 { font-size: 20pt; margin: 0 0 2mm; letter-spacing: -.02em; }
h2 { font-size: 13pt; margin: 9mm 0 3mm; padding-bottom: 1.5mm;
     border-bottom: 1.5px solid var(--rule); }
h3 { font-size: 11pt; margin: 5mm 0 2mm; }
p { margin: 0 0 2.5mm; }
.lede { color: var(--muted); font-size: 11pt; max-width: 62em; }
.small { font-size: 9pt; color: var(--faint); }
code { font: 9pt "JetBrains Mono", ui-monospace, monospace; }

.masthead { border-bottom: 2px solid var(--ink); padding-bottom: 4mm; }
.masthead .brand { font-size: 9pt; letter-spacing: .14em;
                   text-transform: uppercase; color: var(--accent);
                   font-weight: 700; }

.facts { display: grid; grid-template-columns: repeat(4, 1fr);
         gap: 3mm; margin: 5mm 0 0; }
.fact { border: 1px solid var(--rule); border-radius: 2mm; padding: 3mm; }
.fact dt { font-size: 7.5pt; letter-spacing: .09em; text-transform: uppercase;
           color: var(--faint); margin: 0 0 1mm; }
.fact dd { margin: 0; font-size: 13pt; font-weight: 650;
           font-variant-numeric: tabular-nums; }
.fact.bad dd { color: var(--danger); }
.fact.warn dd { color: var(--warn); }
.fact.good dd { color: var(--ok); }

figure { margin: 4mm 0; page-break-inside: avoid; text-align: center; }
figure svg { max-width: 100%; height: auto; }
figcaption { font-size: 8.5pt; color: var(--faint); margin-top: 2mm; }

table { width: 100%; border-collapse: collapse; font-size: 9pt;
        margin: 2mm 0 4mm; }
th, td { text-align: left; padding: 1.6mm 2mm;
         border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font-size: 7.5pt; letter-spacing: .07em; text-transform: uppercase;
     color: var(--faint); border-bottom-width: 1.5px; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr { page-break-inside: avoid; }

.tag { display: inline-block; padding: .3mm 1.4mm; border-radius: 1mm;
       font-size: 7.5pt; font-weight: 650; border: 1px solid var(--rule); }
.tag.req { border-color: var(--accent); color: var(--accent); }
.tag.dep { color: var(--faint); }
.tag.err { border-color: var(--danger); color: var(--danger); }
.tag.warn { border-color: var(--warn); color: var(--warn); }
.tag.info { border-color: var(--faint); color: var(--faint); }

.note { border-left: 3px solid var(--accent); background: #f6faf9;
        padding: 3mm 4mm; margin: 3mm 0; page-break-inside: avoid; }
.note.caution { border-left-color: var(--warn); background: #fffbf2; }

ol.steps { margin: 0 0 3mm; padding-left: 6mm; }
ol.steps li { margin-bottom: 1.5mm; }

footer { margin-top: 10mm; padding-top: 3mm; border-top: 1px solid var(--rule);
         font-size: 8pt; color: var(--faint);
         display: flex; justify-content: space-between; }

@page { size: A4; margin: 14mm 12mm 16mm; }
@media print {
  .page { padding: 0; max-width: none; }
  .no-print { display: none !important; }
  h2 { page-break-after: avoid; }
}
.no-print { margin: 0 0 6mm; padding: 3mm 4mm; border-radius: 2mm;
            background: #eef7f6; border: 1px solid #b9dedb; font-size: 9pt; }
"""


def _fact(label: str, value: str, tone: str = "") -> str:
    return (f'<div class="fact {tone}"><dt>{escape(label)}</dt>'
            f"<dd>{escape(value)}</dd></div>")


def _rows(rows: list[list[str]], headers: list[str]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="num">{cell}</td>' if header.lower() in
            ("cost", "monthly", "count", "line", "saving") else f"<td>{cell}</td>"
            for header, cell in zip(headers, row, strict=False)
        ) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _optimization_score(
    recommendations: list[Recommendation], findings: list
) -> tuple[int, str]:
    """A single figure for the cover, derived rather than invented.

    Starts at 100 and subtracts a weight per open item. It is a summary of the
    checks that ran, not a measure of architectural quality in general -- the
    caption says so, because a bare number invites more confidence than it
    has earned.
    """
    weights = {"critical": 12, "high": 7, "medium": 3, "low": 1}
    penalty = sum(weights.get(r.priority, 1) for r in recommendations)
    penalty += sum(10 for f in findings if f.severity == "error")
    score = max(0, 100 - penalty)
    tone = "good" if score >= 80 else "warn" if score >= 55 else "bad"
    return score, tone


def build_report(result, theme: str = "print") -> str:
    """Render the full architecture report for one generation result."""
    spec: InfrastructureSpec = result.spec
    summary = result.to_dict()["summary"]
    findings = result.findings
    recommendations = result.recommendations
    explanations: list[Explanation] = result.explanations

    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    score, score_tone = _optimization_score(recommendations, findings)
    optimisation = summarise(recommendations)

    # The diagram is re-rendered for print rather than reusing the viewer's
    # copy: the on-screen SVG carries a prefers-color-scheme query and would
    # invert itself on a dark-themed machine.
    diagram = SvgRenderer().render(LayoutEngine().build(spec), theme=theme)

    generated = datetime.now(UTC).strftime("%d %B %Y at %H:%M UTC")
    requested = [r for r in spec.resources if r.origin is Origin.EXPLICIT]
    required = [r for r in spec.resources if r.origin is not Origin.EXPLICIT]
    by_id = {e.resource_id: e for e in explanations}

    parts: list[str] = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{escape(spec.name)} — architecture report</title>",
        f"<style>{_STYLE}</style></head><body><div class='page'>",

        "<div class='no-print'>Use your browser's print dialog and choose "
        "<strong>Save as PDF</strong>. Text and the diagram stay vector at "
        "any zoom.</div>",

        "<header class='masthead'>",
        "<div class='brand'>Cloudweaver AI · Architecture Report</div>",
        f"<h1>{escape(spec.name)}</h1>",
        f"<p class='lede'>{escape(spec.summary)}</p>",
        f"<p class='small'>{escape(summary['region'])} · "
        f"{escape(summary['environment'])} · generated {escape(generated)}</p>",
        "</header>",

        "<dl class='facts'>",
        _fact("Resources", str(summary["resource_count"])),
        _fact("Terraform files", str(summary["file_count"])),
        _fact("Estimated monthly", f"${summary['estimated_monthly_cost_usd']:,.2f}"),
        _fact(
            "Validation",
            f"{len(errors)} error{'s' if len(errors) != 1 else ''}" if errors
            else f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
            if warnings else "Clean",
            "bad" if errors else "warn" if warnings else "good",
        ),
        "</dl>",
        "<dl class='facts'>",
        _fact("Optimisation score", f"{score}/100", score_tone),
        _fact("Recommendations", str(optimisation["total"])),
        _fact("Potential saving",
              f"${optimisation['potential_monthly_saving_usd']:,.2f}"),
        _fact("Generation time", f"{summary['duration_ms']:.0f} ms"),
        "</dl>",
    ]

    # ---- executive summary ------------------------------------------------
    parts += [
        "<h2>Executive summary</h2>",
        f"<p>This design contains <strong>{len(spec.resources)} resources</strong> "
        f"in {escape(summary['region'])}. "
        f"<strong>{len(requested)}</strong> were named in the requirement and "
        f"<strong>{len(required)}</strong> were added because something named "
        "cannot be deployed without them. Nothing was added on the grounds of "
        "being generally advisable.</p>",
    ]
    if errors:
        parts.append(
            "<div class='note caution'><strong>Not ready to deploy.</strong> "
            f"{len(errors)} validation error{'s' if len(errors) != 1 else ''} "
            "would fail during <code>terraform apply</code>; they are listed "
            "under Validation below.</div>")
    else:
        parts.append(
            "<div class='note'><strong>Deployable.</strong> Validation reports "
            "nothing that blocks <code>terraform apply</code>. Review the "
            "recommendations before treating it as production-ready.</div>")

    if spec.exclusions:
        excluded = ", ".join(
            f"{escape(e.kind.value)} (the requirement said “{escape(e.cue)}”)"
            for e in spec.exclusions)
        parts.append(f"<p><strong>Deliberately excluded:</strong> {excluded}.</p>")

    if spec.warnings:
        parts.append("<p><strong>Stated limitations:</strong> "
                     + " ".join(escape(w) for w in spec.warnings) + "</p>")

    # ---- diagram ----------------------------------------------------------
    parts += [
        "<h2>Architecture diagram</h2>",
        f"<figure>{diagram}"
        f"<figcaption>Figure 1 — {escape(spec.name)}. Generated from the same "
        "shared model as the Terraform, so the two cannot disagree."
        "</figcaption></figure>",
    ]

    # ---- components by domain --------------------------------------------
    parts.append("<h2>Infrastructure components</h2>")
    for label, kinds in DOMAINS:
        members = [r for r in spec.resources if r.kind in kinds]
        if not members:
            continue
        rows = []
        for resource in members:
            explanation = by_id.get(resource.id)
            origin = ("<span class='tag req'>requested</span>"
                      if resource.origin is Origin.EXPLICIT
                      else "<span class='tag dep'>dependency</span>")
            cost = explanation.monthly_cost_usd if explanation else 0.0
            rows.append([
                f"<strong>{escape(resource.name)}</strong>"
                + (f" ×{resource.count}" if resource.count > 1 else ""),
                f"<code>{escape(resource.id)}</code>",
                origin,
                escape(resource.reason),
                f"${cost:,.2f}" if cost else "—",
            ])
        parts.append(f"<h3>{escape(label)}</h3>")
        parts.append(_rows(rows, ["Resource", "Identifier", "Origin",
                                  "Why it is here", "Monthly"]))

    # ---- networking and security -----------------------------------------
    vpc = spec.first(Kind.VPC)
    if vpc is not None:
        parts += [
            "<h2>Networking</h2>",
            "<p>A VPC "
            + (f"({escape(str(vpc.external_id))}, existing) " if vpc.is_external
               else f"of {escape(str(vpc.properties.get('cidr_block', '10.0.0.0/16')))} ")
            + f"spanning {spec.availability_zones} availability zone"
            f"{'s' if spec.availability_zones != 1 else ''}. "
            "Public subnets hold a route to the internet gateway; private "
            "subnets hold none, so nothing in them is reachable from outside "
            "the VPC.</p>",
        ]
        placed = [(r, r.properties.get("subnet_band")) for r in spec.resources
                  if r.properties.get("subnet_band")]
        if placed:
            parts.append(_rows(
                [[escape(r.name), f"<code>{escape(r.id)}</code>",
                  f"{escape(str(band))} subnets"] for r, band in placed],
                ["Resource", "Identifier", "Placement"]))

    groups = spec.of_kind(Kind.SECURITY_GROUP)
    roles = spec.of_kind(Kind.IAM_ROLE)
    if groups or roles:
        parts.append("<h2>Security</h2>")
        if groups:
            parts.append(_rows(
                [[escape(g.name), f"<code>{escape(g.id)}</code>",
                  ", ".join(str(p) for p in g.properties.get("ingress_ports", [])) or "—",
                  escape(str(g.properties.get("ingress_from", "the VPC")))]
                 for g in groups],
                ["Security group", "Identifier", "Ingress ports", "Source"]))
        if roles:
            parts.append("<p>IAM roles are assumed by the service rather than "
                         "by a person; no long-lived access key exists for "
                         "them: "
                         + ", ".join(f"<code>{escape(r.id)}</code>" for r in roles)
                         + ".</p>")

    # ---- terraform --------------------------------------------------------
    parts += [
        "<h2>Terraform</h2>",
        f"<p>{len(result.terraform)} files. Every resource in the diagram "
        "above is created by one of them; the emission audit compares the two "
        "and reports any resource the generator failed to emit.</p>",
        _rows(
            [[f"<code>{escape(name)}</code>", str(len(content.splitlines()))]
             for name, content in sorted(result.terraform.items())
             if name.endswith((".tf", ".tfvars"))],
            ["File", "Line"]),
    ]

    # ---- validation -------------------------------------------------------
    parts.append("<h2>Validation</h2>")
    if not findings:
        parts.append("<p>No findings. The design passes every structural, "
                     "network, security, reliability and AWS deployment "
                     "check.</p>")
    else:
        tone = {"error": "err", "warning": "warn", "info": "info"}
        parts.append(_rows(
            [[f"<span class='tag {tone[f.severity]}'>{f.severity}</span>",
              escape(f.message), f"<code>{escape(f.code)}</code>"]
             for f in sorted(findings, key=lambda f: f.severity)],
            ["Severity", "Finding", "Check"]))

    # ---- optimisation -----------------------------------------------------
    parts.append("<h2>Optimisation recommendations</h2>")
    if not recommendations:
        parts.append("<p>Every optimisation rule passed against this design.</p>")
    else:
        parts.append(
            "<p>These are advisory. None has been applied: the generated "
            "Terraform contains only the requested resources and their "
            "mandatory dependencies.</p>")
        tone_for = {"critical": "err", "high": "warn"}
        parts.append(_rows(
            [[f"<span class='tag {tone_for.get(r.priority, 'info')}'>"
              f"{escape(r.priority)}</span>",
              f"<strong>{escape(r.title)}</strong><br>"
              f"<span class='small'>{escape(r.reason)}</span>",
              escape(r.pillar),
              escape(r.difficulty),
              ("—" if r.monthly_delta_usd == 0
               else f"{'−' if r.monthly_delta_usd < 0 else '+'}"
                    f"${abs(r.monthly_delta_usd):,.2f}")]
             for r in recommendations],
            ["Priority", "Recommendation", "Pillar", "Effort", "Monthly"]))

    # ---- cost -------------------------------------------------------------
    priced = sorted([e for e in explanations if e.monthly_cost_usd > 0],
                    key=lambda e: e.monthly_cost_usd, reverse=True)
    total = summary["estimated_monthly_cost_usd"]
    parts.append("<h2>Cost breakdown</h2>")
    if priced:
        parts.append(
            f"<p>About <strong>${total:,.2f} a month</strong>, "
            f"${total / 30.44:,.2f} a day, ${total * 12:,.2f} a year.</p>")
        parts.append(_rows(
            [[escape(e.name), f"<code>{escape(e.resource_id)}</code>",
              f"${e.monthly_cost_usd:,.2f}"] for e in priced],
            ["Resource", "Identifier", "Monthly"]))
    else:
        parts.append("<p>Nothing in this design carries a fixed monthly "
                     "charge.</p>")
    parts.append(
        "<p class='small'>Figures are static per-service rates, not live AWS "
        "pricing, and exclude data transfer, request charges and storage "
        "growth. They are intended for comparing designs, not for forecasting "
        "a bill.</p>")

    # ---- decision trace ---------------------------------------------------
    parts += [
        "<h2>Decision trace</h2>",
        "<p>Why each resource is present. A requested resource cites the words "
        "that asked for it; a dependency cites the policy rule that required "
        "it and the resource that triggered it.</p>",
        _rows(
            [[escape(e.name),
              "<span class='tag req'>requested</span>" if e.requested
              else "<span class='tag dep'>dependency</span>",
              (f"“{escape(e.evidence)}”" if e.requested and e.evidence
               else f"<code>{escape(e.rule)}</code>" if e.rule else "—"),
              f"<code>{escape(e.triggered_by)}</code>" if e.triggered_by else "—",
              f"{e.confidence:.0%}"]
             for e in explanations],
            ["Resource", "Origin", "Evidence or rule", "Triggered by", "Confidence"]),
    ]

    # ---- deployment -------------------------------------------------------
    parts += [
        "<h2>Deployment notes</h2>",
        "<ol class='steps'>"
        "<li>Unpack the downloaded project.</li>"
        "<li><code>terraform init</code> — downloads the AWS provider.</li>"
        "<li><code>terraform plan</code> — read this before applying.</li>"
        "<li><code>terraform apply</code> — creates real, billable resources.</li>"
        "</ol>",
        "<div class='note caution'><strong>Before applying.</strong> State is "
        "local by default; configure an S3 backend with locking before anyone "
        "else runs this. Generated database passwords live in state until "
        "moved to Secrets Manager. Confirm the region is "
        f"<code>{escape(summary['region'])}</code>.</div>",
    ]

    parts += [
        "<footer><span>Generated by Cloudweaver AI</span>"
        f"<span>{escape(spec.name)} · {escape(generated)}</span></footer>",
        "</div></body></html>",
    ]
    return "".join(parts)
