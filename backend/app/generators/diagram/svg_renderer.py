"""SVG renderer for the laid-out architecture diagram.

Output is a single self-contained ``<svg>`` string: no external stylesheet, no
web fonts, no JavaScript.  That makes it equally valid embedded in the web UI,
saved to a file, or dropped into a report.

Colours are defined once per category and adapt to the viewer's colour scheme
through a ``prefers-color-scheme`` media query inside the SVG's own style block.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.generators.diagram.layout import INTERNET_ID, Layout, Node, RoutedEdge

# category -> (fill, stroke, text)
PALETTE: dict[str, tuple[str, str, str]] = {
    "internet": ("#eef2f7", "#8fa3b8", "#41536b"),
    "traffic": ("#e8f1fd", "#4a86d8", "#1c4b8f"),
    "compute": ("#fdf0e6", "#e08b3c", "#8a4c11"),
    "data": ("#eaf5ee", "#3f9c5f", "#1d5c33"),
    "storage": ("#f0edfb", "#7b62d4", "#3f2f8a"),
    "integration": ("#fdf2f7", "#c8558f", "#7c2c53"),
    "security": ("#fdecec", "#d9534f", "#8c2b28"),
    "network": ("#eef2f7", "#7d8fa5", "#3f4f63"),
    "ops": ("#f5f2e8", "#a58a3c", "#5d4c17"),
}

DARK_PALETTE: dict[str, tuple[str, str, str]] = {
    "internet": ("#232a33", "#6d7f93", "#c6d2e0"),
    "traffic": ("#182634", "#4a86d8", "#a9cbf5"),
    "compute": ("#2d2318", "#e08b3c", "#f0c294"),
    "data": ("#182a1f", "#3f9c5f", "#a4d9b6"),
    "storage": ("#221d33", "#7b62d4", "#c3b6f0"),
    "integration": ("#2d1c26", "#c8558f", "#f0aecd"),
    "security": ("#2e1b1b", "#d9534f", "#f2a9a7"),
    "network": ("#232a33", "#7d8fa5", "#c1ccd9"),
    "ops": ("#2a2617", "#a58a3c", "#ded0a2"),
}

EDGE_STYLE: dict[str, tuple[str, str]] = {
    # kind -> (stroke class suffix, dash array)
    "traffic": ("solid", ""),
    "data": ("data", "6 4"),
    "depends": ("dep", "2 5"),
}


def _css() -> str:
    light = "\n".join(
        f"    .fill-{cat} {{ fill: {fill}; stroke: {stroke}; }}\n"
        f"    .text-{cat} {{ fill: {text}; }}\n"
        f"    .badge-{cat} {{ fill: {stroke}; }}"
        for cat, (fill, stroke, text) in PALETTE.items()
    )
    dark = "\n".join(
        f"      .fill-{cat} {{ fill: {fill}; stroke: {stroke}; }}\n"
        f"      .text-{cat} {{ fill: {text}; }}\n"
        f"      .badge-{cat} {{ fill: {stroke}; }}"
        for cat, (fill, stroke, text) in DARK_PALETTE.items()
    )
    return f"""
  <style>
    .bg {{ fill: #ffffff; }}
    .node {{ stroke-width: 1.5; }}
    .label {{ font: 600 13px 'Segoe UI', system-ui, sans-serif; }}
    .sublabel {{ font: 400 10.5px 'Segoe UI', system-ui, sans-serif; opacity: .78; }}
    .badge {{ font: 700 9px 'Segoe UI', system-ui, sans-serif; fill: #ffffff; }}
    .title {{ font: 700 17px 'Segoe UI', system-ui, sans-serif; fill: #1b2430; }}
    .subtitle {{ font: 400 11.5px 'Segoe UI', system-ui, sans-serif; fill: #64748b; }}
    .band {{ font: 600 10px 'Segoe UI', system-ui, sans-serif; fill: #94a3b8;
             letter-spacing: .08em; }}
    .group-box {{ fill: none; stroke: #94a3b8; stroke-width: 1.4; stroke-dasharray: 7 5; }}
    .group-label {{ font: 600 11px 'Segoe UI', system-ui, sans-serif; fill: #64748b; }}
    .edge {{ fill: none; stroke: #7c8ea3; stroke-width: 1.6; }}
    .edge.data {{ stroke: #3f9c5f; }}
    .edge.dep {{ stroke: #b0bccb; }}
    .edge-label {{ font: 400 9.5px 'Segoe UI', system-ui, sans-serif; fill: #8194a8; }}
    .implied {{ stroke-dasharray: 5 3; }}
{light}
    @media (prefers-color-scheme: dark) {{
      .bg {{ fill: #0f141b; }}
      .title {{ fill: #e6edf5; }}
      .subtitle {{ fill: #93a3b6; }}
      .band {{ fill: #6b7c91; }}
      .group-box {{ stroke: #47566a; }}
      .group-label {{ fill: #93a3b6; }}
      .edge {{ stroke: #63768c; }}
      .edge.dep {{ stroke: #3c4a5c; }}
      .edge-label {{ fill: #7b8da2; }}
{dark}
    }}
  </style>
"""


class SvgRenderer:
    """Renders a :class:`~app.generators.diagram.layout.Layout` as SVG."""

    def render(self, layout: Layout) -> str:
        w = round(layout.width, 1)
        h = round(layout.height, 1)
        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="Cloud architecture diagram for {escape(layout.title)}">',
            _css(),
            self._defs(),
            f'  <rect class="bg" width="{w}" height="{h}" rx="8"/>',
            f'  <text class="title" x="{20}" y="{28}">{escape(layout.title)}</text>',
            f'  <text class="subtitle" x="{20}" y="{45}">{escape(layout.subtitle)}</text>',
        ]

        for group in layout.groups:
            parts.append(
                f'  <rect class="group-box" x="{group.x:.1f}" y="{group.y:.1f}" '
                f'width="{group.w:.1f}" height="{group.h:.1f}" rx="12"/>'
            )
            parts.append(
                f'  <text class="group-label" x="{group.x + 12:.1f}" '
                f'y="{group.y + 17:.1f}">{escape(group.label)} - '
                f"{escape(group.sublabel)}</text>"
            )

        for label, y in layout.band_labels:
            parts.append(
                f'  <text class="band" x="18" y="{y + 4:.1f}">{escape(label.upper())}</text>'
            )

        for edge in layout.edges:
            parts.append(self._edge(edge))

        for node in layout.nodes:
            parts.append(self._node(node))

        parts.append("</svg>")
        return "\n".join(parts)

    # -- primitives --------------------------------------------------------

    @staticmethod
    def _defs() -> str:
        return (
            "  <defs>\n"
            '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
            '      <path d="M 0 0 L 10 5 L 0 10 z" fill="#7c8ea3"/>\n'
            "    </marker>\n"
            '    <marker id="arrow-data" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
            '      <path d="M 0 0 L 10 5 L 0 10 z" fill="#3f9c5f"/>\n'
            "    </marker>\n"
            "  </defs>"
        )

    @staticmethod
    def _edge(edge: RoutedEdge) -> str:
        if len(edge.points) < 2:
            return ""
        style, dash = EDGE_STYLE.get(edge.kind, ("solid", ""))
        cls = "edge" if style == "solid" else f"edge {style}"
        marker = "arrow-data" if style == "data" else "arrow"
        path = " ".join(
            ("M" if i == 0 else "L") + f" {x:.1f} {y:.1f}"
            for i, (x, y) in enumerate(edge.points)
        )
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        out = (
            f'  <path class="{cls}" d="{path}"{dash_attr} '
            f'marker-end="url(#{marker})"/>'
        )
        if edge.label:
            mid = edge.points[len(edge.points) // 2]
            out += (
                f'\n  <text class="edge-label" x="{mid[0] + 6:.1f}" '
                f'y="{mid[1] - 4:.1f}">{escape(edge.label)}</text>'
            )
        return out

    @staticmethod
    def _node(node: Node) -> str:
        cat = node.category
        implied = " implied" if node.origin != "explicit" else ""
        title = escape(node.detail or node.label)
        parts = [
            f'  <g><title>{title}</title>',
            f'    <rect class="node fill-{cat}{implied}" x="{node.x:.1f}" y="{node.y:.1f}" '
            f'width="{node.w:.1f}" height="{node.h:.1f}" rx="10"/>',
        ]
        if node.id != INTERNET_ID and node.glyph:
            parts.append(
                f'    <rect class="badge-{cat}" x="{node.x + 12:.1f}" '
                f'y="{node.y + 12:.1f}" width="34" height="17" rx="4"/>'
            )
            parts.append(
                f'    <text class="badge" x="{node.x + 29:.1f}" y="{node.y + 24:.1f}" '
                f'text-anchor="middle">{escape(node.glyph)}</text>'
            )
            text_x = node.x + 54
        else:
            text_x = node.x + 14

        parts.append(
            f'    <text class="label text-{cat}" x="{text_x:.1f}" '
            f'y="{node.y + 26:.1f}">{escape(node.label)}</text>'
        )
        parts.append(
            f'    <text class="sublabel text-{cat}" x="{node.x + 14:.1f}" '
            f'y="{node.y + 48:.1f}">{escape(node.sublabel)}</text>'
        )
        parts.append("  </g>")
        return "\n".join(parts)
