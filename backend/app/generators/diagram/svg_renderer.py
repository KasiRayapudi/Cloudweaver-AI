"""SVG renderer for the laid-out architecture diagram.

Output is a single self-contained ``<svg>`` string: no external stylesheet, no
web fonts, no JavaScript.  That makes it equally valid embedded in the web UI,
saved to a file, or dropped into a report.

Colours are defined once per category and adapt to the viewer's colour scheme
through a ``prefers-color-scheme`` media query inside the SVG's own style block.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.generators.diagram.layout import CORNER, INTERNET_ID, Layout, Node, RoutedEdge

# Service marks, drawn in a 16x16 box inside each node's icon tile.
#
# Deliberately geometric rather than facsimiles of the AWS icon set: those are
# trademarked artwork, and a consistent hand-drawn set reads better at 16px
# than shrunken official icons do. Each is a single stroked path so it takes
# the node's own colour and stays legible in both themes.
ICONS: dict[str, str] = {
    "compute": "M2.5 4.2h11v3.1h-11z M2.5 8.7h11v3.1h-11z M4.4 5.8h.01 M4.4 10.3h.01",
    "traffic": "M8 2.4v3.4 M8 5.8 3.4 9.6 M8 5.8l4.6 3.8 M2 9.8h2.8v3.4H2z"
               " M6.6 9.8h2.8v3.4H6.6z M11.2 9.8H14v3.4h-2.8z",
    "data": "M8 2.4c2.9 0 5 .9 5 2v7.2c0 1.1-2.1 2-5 2s-5-.9-5-2V4.4c0-1.1 2.1-2 5-2z"
            " M3 4.4c0 1.1 2.1 2 5 2s5-.9 5-2 M3 8c0 1.1 2.1 2 5 2s5-.9 5-2",
    "storage": "M2.8 3.4h10.4l-1.2 9.4a1 1 0 0 1-1 .8H5a1 1 0 0 1-1-.8z M2.8 3.4 8 1.6l5.2 1.8",
    "security": "M8 1.8 13.2 4v4.2c0 3.1-2.2 5.2-5.2 6-3-.8-5.2-2.9-5.2-6V4z M5.9 8l1.5 1.5L10.3 6.6",
    "network": "M8 2.2 13.4 5.3v6.2L8 14.6 2.6 11.5V5.3z M8 2.2v12.4 M2.6 5.3 8 8.4l5.4-3.1",
    "integration": "M2.4 4.4h11.2v7.2H2.4z M2.4 4.4 8 8.8l5.6-4.4",
    "ops": "M2.4 12.8h11.2 M4.4 12.8V8.6 M7.2 12.8V4.4 M10 12.8V7 M12.8 12.8V5.6",
    "internet": "M8 1.9a6.1 6.1 0 1 0 0 12.2A6.1 6.1 0 0 0 8 1.9z M1.9 8h12.2"
                " M8 1.9c1.7 1.8 2.6 4 2.6 6.1S9.7 12.3 8 14.1"
                " M8 1.9C6.3 3.7 5.4 5.9 5.4 8s.9 4.3 2.6 6.1",
}

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
        f"    .badge-{cat} {{ fill: {stroke}; }}\n"
        f"    .cat-{cat} .icon-mark path {{ stroke: {text}; }}"
        for cat, (fill, stroke, text) in PALETTE.items()
    )
    dark = "\n".join(
        f"      .fill-{cat} {{ fill: {fill}; stroke: {stroke}; }}\n"
        f"      .text-{cat} {{ fill: {text}; }}\n"
        f"      .badge-{cat} {{ fill: {stroke}; }}\n"
        f"      .cat-{cat} .icon-mark path {{ stroke: {text}; }}"
        for cat, (fill, stroke, text) in DARK_PALETTE.items()
    )
    return f"""
  <style>
    .bg {{ fill: #ffffff; }}
    .node {{ stroke-width: 1.5; }}
    .label {{ font: 600 13.5px 'Inter', 'Segoe UI', system-ui, sans-serif;
              letter-spacing: -.008em; }}
    .sublabel {{ font: 400 11px 'Inter', 'Segoe UI', system-ui, sans-serif; opacity: .8; }}
    .tag {{ font: 600 9px 'JetBrains Mono', ui-monospace, monospace;
            letter-spacing: .09em; opacity: .58; }}
    .badge {{ font: 700 9px 'Inter', 'Segoe UI', system-ui, sans-serif; fill: #ffffff; }}
    .icon-tile {{ opacity: .16; }}
    .icon-mark path {{ fill: none; stroke-width: 1.35; stroke-linecap: round;
                       stroke-linejoin: round; }}
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
    def _rounded_path(points: list[tuple[float, float]]) -> str:
        """Polyline with arcs at the turns.

        A right-angled polyline reads as a wiring diagram; softening each
        corner reads as a considered connector. The radius shrinks on short
        segments so a tight route never produces a distorted arc.
        """
        if len(points) < 3:
            return " ".join(
                ("M" if i == 0 else "L") + f" {x:.1f} {y:.1f}"
                for i, (x, y) in enumerate(points)
            )

        out = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
        for index in range(1, len(points) - 1):
            previous, corner, following = points[index - 1], points[index], points[index + 1]

            def shorten(origin, toward, limit):
                dx, dy = toward[0] - origin[0], toward[1] - origin[1]
                length = (dx * dx + dy * dy) ** 0.5 or 1
                radius = min(limit, length / 2)
                return (origin[0] + dx / length * radius, origin[1] + dy / length * radius)

            entry = shorten(corner, previous, CORNER)
            exit_ = shorten(corner, following, CORNER)
            out.append(f"L {entry[0]:.1f} {entry[1]:.1f}")
            out.append(f"Q {corner[0]:.1f} {corner[1]:.1f} {exit_[0]:.1f} {exit_[1]:.1f}")

        last = points[-1]
        out.append(f"L {last[0]:.1f} {last[1]:.1f}")
        return " ".join(out)

    @staticmethod
    def _edge(edge: RoutedEdge) -> str:
        if len(edge.points) < 2:
            return ""
        style, dash = EDGE_STYLE.get(edge.kind, ("solid", ""))
        cls = "edge" if style == "solid" else f"edge {style}"
        marker = "arrow-data" if style == "data" else "arrow"
        path = SvgRenderer._rounded_path(edge.points)
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
        # data-resource-id lets an interactive viewer map a click back to the
        # resource in the shared model. Additive only: it changes no geometry,
        # no colour and no other output.
        parts = [
            f'  <g class="node-group cat-{cat}" data-resource-id="{escape(node.id)}" '
            f'data-category="{escape(cat)}" tabindex="0" role="button" '
            f'aria-label="{escape(node.label)}">'
            f'<title>{title}</title>',
            f'    <rect class="node fill-{cat}{implied}" x="{node.x:.1f}" y="{node.y:.1f}" '
            f'width="{node.w:.1f}" height="{node.h:.1f}" rx="10"/>',
        ]
        # An icon tile in place of the old three-letter badge: at a glance the
        # shape says "database" faster than the letters RDS do, and the
        # service name is already spelled out on the line beside it.
        icon = ICONS.get(cat)
        if icon:
            tile_x, tile_y = node.x + 13, node.y + 13
            parts.append(
                f'    <rect class="icon-tile badge-{cat}" x="{tile_x:.1f}" '
                f'y="{tile_y:.1f}" width="30" height="30" rx="8"/>'
            )
            parts.append(
                f'    <g class="icon-mark" transform="translate({tile_x + 7:.1f} '
                f'{tile_y + 7:.1f}) scale(1)"><path d="{icon}"/></g>'
            )
            text_x = node.x + 54
        else:
            text_x = node.x + 16

        parts.append(
            f'    <text class="label text-{cat}" x="{text_x:.1f}" '
            f'y="{node.y + 30:.1f}">{escape(node.label)}</text>'
        )
        parts.append(
            f'    <text class="sublabel text-{cat}" x="{text_x:.1f}" '
            f'y="{node.y + 47:.1f}">{escape(node.sublabel)}</text>'
        )
        if node.glyph and node.id != INTERNET_ID:
            parts.append(
                f'    <text class="tag text-{cat}" x="{node.x + 16:.1f}" '
                f'y="{node.y + 68:.1f}">{escape(node.glyph)}</text>'
            )
        parts.append("  </g>")
        return "\n".join(parts)
