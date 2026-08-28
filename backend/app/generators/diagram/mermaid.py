"""Mermaid export of the same graph the SVG renderer draws.

A second textual rendering of the IR costs almost nothing and makes the output
pasteable into GitHub, Notion or a wiki, where an inline SVG is awkward.
"""

from __future__ import annotations

import re

from app.models.ir import EdgeKind, InfrastructureSpec, Kind, Tier
from app.nlp.catalog import service_for

SUBGRAPHS: tuple[tuple[str, str, tuple[Tier, ...]], ...] = (
    ("edge", "Edge & Global", (Tier.GLOBAL, Tier.EDGE)),
    ("public", "Public Subnets", (Tier.PUBLIC,)),
    ("app", "Private Subnets", (Tier.APP,)),
    ("data", "Data Layer", (Tier.DATA,)),
    ("sec", "Security & Ops", (Tier.SECURITY, Tier.OPS)),
)

SKIP: frozenset[Kind] = frozenset({
    Kind.VPC, Kind.SUBNET_PUBLIC, Kind.SUBNET_PRIVATE, Kind.ROUTE_TABLE,
})

CLASS_DEFS = (
    "  classDef compute fill:#fdf0e6,stroke:#e08b3c,color:#8a4c11;",
    "  classDef traffic fill:#e8f1fd,stroke:#4a86d8,color:#1c4b8f;",
    "  classDef data fill:#eaf5ee,stroke:#3f9c5f,color:#1d5c33;",
    "  classDef storage fill:#f0edfb,stroke:#7b62d4,color:#3f2f8a;",
    "  classDef security fill:#fdecec,stroke:#d9534f,color:#8c2b28;",
    "  classDef integration fill:#fdf2f7,stroke:#c8558f,color:#7c2c53;",
    "  classDef network fill:#eef2f7,stroke:#7d8fa5,color:#3f4f63;",
    "  classDef ops fill:#f5f2e8,stroke:#a58a3c,color:#5d4c17;",
)


def _sanitize(text: str) -> str:
    """Mermaid labels choke on quotes and brackets."""
    return re.sub(r'["\[\]{}|]', "", text)


class MermaidRenderer:
    """Renders the IR as a Mermaid ``flowchart TD``."""

    def render(self, spec: InfrastructureSpec) -> str:
        drawn = [r for r in spec.resources if r.kind not in SKIP]
        if not drawn:
            return "flowchart TD\n  empty[No resources identified]"

        lines = ["flowchart TD"]
        classes: list[str] = []

        for key, title, tiers in SUBGRAPHS:
            members = [r for r in drawn if r.tier in tiers]
            if not members:
                continue
            lines.append(f'  subgraph {key}["{title}"]')
            lines.append("    direction LR")
            for r in members:
                info = service_for(r.kind)
                count = f" x{r.count}" if r.count > 1 else ""
                lines.append(f'    {r.id}["{_sanitize(info.display)}{count}"]')
                classes.append(f"  class {r.id} {info.category};")
            lines.append("  end")

        arrows = {
            EdgeKind.TRAFFIC: "-->",
            EdgeKind.DATA: "-.->",
            EdgeKind.DEPENDENCY: "-.-",
        }
        drawn_ids = {r.id for r in drawn}
        for edge in spec.edges:
            if edge.kind is EdgeKind.CONTAINMENT:
                continue
            if edge.source not in drawn_ids or edge.target not in drawn_ids:
                continue
            arrow = arrows.get(edge.kind, "-->")
            # Only solid arrows take an inline label; the dotted forms render
            # their labels inconsistently across Mermaid versions.
            label = f"|{_sanitize(edge.label)}|" if edge.label and arrow == "-->" else ""
            lines.append(f"  {edge.source} {arrow}{label} {edge.target}")

        lines.extend(CLASS_DEFS)
        lines.extend(dict.fromkeys(classes))
        return "\n".join(lines)
