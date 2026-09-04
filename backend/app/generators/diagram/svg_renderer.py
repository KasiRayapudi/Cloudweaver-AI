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
    "security": "M8 1.8 13.2 4v4.2c0 3.1-2.2 5.2-5.2 6-3-.8-5.2-2.9-5.2-6V4z"
                " M5.9 8l1.5 1.5L10.3 6.6",
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

# Printer-friendly: near-white fills that survive a greyscale reproduction,
# dark text, and strokes heavy enough to hold at 300 dpi. Colour is carried by
# the stroke rather than the fill, so a black-and-white print stays legible.
PRINT_PALETTE: dict[str, tuple[str, str, str]] = {
    "internet": ("#ffffff", "#4a5563", "#1a2029"),
    "traffic": ("#ffffff", "#1f4f9c", "#12233d"),
    "compute": ("#ffffff", "#9c5c14", "#3d2508"),
    "data": ("#ffffff", "#1c6b38", "#0d2b17"),
    "storage": ("#ffffff", "#4a3596", "#1d1540"),
    "integration": ("#ffffff", "#96225d", "#3d0d26"),
    "security": ("#ffffff", "#9c2420", "#3d0e0c"),
    "network": ("#ffffff", "#44525f", "#1a2129"),
    "ops": ("#ffffff", "#75601f", "#2e260c"),
}

#: Chrome per theme: (page background, title, subtitle, band label).
THEME_CHROME: dict[str, tuple[str, str, str, str]] = {
    "light": ("#ffffff", "#1b2430", "#64748b", "#94a3b8"),
    "dark": ("#0f141b", "#e6edf5", "#93a3b6", "#6b7c91"),
    "print": ("#ffffff", "#000000", "#333333", "#555555"),
}

THEMES: tuple[str, ...] = ("auto", "light", "dark", "print")

EDGE_STYLE: dict[str, tuple[str, str]] = {
    # kind -> (stroke class suffix, dash array)
    "traffic": ("solid", ""),
    "data": ("data", "6 4"),
    "depends": ("dep", "2 5"),
}


def _palette_rules(palette: dict[str, tuple[str, str, str]], indent: str) -> str:
    return "\n".join(
        f"{indent}.fill-{cat} {{ fill: {fill}; stroke: {stroke}; }}\n"
        f"{indent}.text-{cat} {{ fill: {text}; }}\n"
        f"{indent}.badge-{cat} {{ fill: {stroke}; }}\n"
        f"{indent}.cat-{cat} .icon-mark path {{ stroke: {text}; }}"
        for cat, (fill, stroke, text) in palette.items()
    )


def _group_rules(theme: str) -> str:
    """Boundary colours.

    Print gets weight rather than tint: a pale fill vanishes in a greyscale
    reproduction, while a heavier stroke survives it.
    """
    if theme == "dark":
        return """
    .group-region { stroke: #8a6a2f; fill: #1c1810; fill-opacity: .55; }
    .group-vpc { stroke: #46608f; fill: #121821; fill-opacity: .6; }
    .group-public { stroke: #35704a; fill: #101a14; fill-opacity: .5; }
    .group-private { stroke: #414f61; fill: #141920; fill-opacity: .5; }
    .group-label-region { fill: #c9a765; }
    .group-label-vpc { fill: #8fb0e0; }
    .group-label-public { fill: #7fc79a; }
    .group-label-private { fill: #93a3b6; }"""
    if theme == "print":
        return """
    .group-region { stroke: #444444; stroke-width: 2; fill: none; }
    .group-vpc { stroke: #222222; stroke-width: 1.8; fill: none; }
    .group-public { stroke: #666666; stroke-width: 1.4; fill: none; }
    .group-private { stroke: #666666; stroke-width: 1.4; fill: none; }
    .group-label-region, .group-label-vpc,
    .group-label-public, .group-label-private { fill: #000000; }"""
    return """
    .group-region { stroke: #b0771f; stroke-width: 1.6; stroke-dasharray: none;
                    fill: #fbf7ef; fill-opacity: .5; }
    .group-vpc { stroke: #5b7cc4; stroke-width: 1.6; stroke-dasharray: none;
                 fill: #f2f6fd; fill-opacity: .55; }
    .group-public { stroke: #3f9c5f; stroke-dasharray: 6 4; fill: #f1f9f4;
                    fill-opacity: .5; }
    .group-private { stroke: #7d8fa5; stroke-dasharray: 6 4; fill: #f4f6f9;
                     fill-opacity: .5; }
    .group-label-region { fill: #8a5c12; }
    .group-label-vpc { fill: #3f5c9c; }
    .group-label-public { fill: #2c7346; }
    .group-label-private { fill: #55677d; }"""


def _css(theme: str = "auto", transparent: bool = False) -> str:
    """Style block for one theme.

    ``auto`` keeps the prefers-color-scheme query, which suits the embedded
    viewer. Every other theme emits one fixed palette and no query at all: an
    exported figure must look the same on every machine that opens it, and a
    media query cannot promise that. A diagram in a paper that inverts itself
    on the reviewer's laptop is a defect, not a preference.
    """
    resolved = theme if theme in ("dark", "print") else "light"
    palette = {"dark": DARK_PALETTE, "print": PRINT_PALETTE}.get(resolved, PALETTE)
    background, title, subtitle, band = THEME_CHROME[resolved]

    edge = {"dark": "#63768c", "print": "#333333"}.get(resolved, "#7c8ea3")
    edge_dep = {"dark": "#3c4a5c", "print": "#888888"}.get(resolved, "#b0bccb")
    edge_data = "#1c6b38" if resolved == "print" else "#3f9c5f"
    edge_label = {"dark": "#7b8da2", "print": "#555555"}.get(resolved, "#8194a8")
    sublabel_fill = "#444444" if resolved == "print" else (
        "#6b7c91" if resolved == "dark" else "#94a3b8")

    node_stroke = "1.9" if resolved == "print" else "1.5"
    edge_stroke = "1.9" if resolved == "print" else "1.6"
    sub_opacity = ".95" if resolved == "print" else ".8"
    tag_opacity = ".8" if resolved == "print" else ".58"
    tile_opacity = ".08" if resolved == "print" else ".16"
    bg_opacity = "0" if transparent else "1"

    common = f"""
    .bg {{ fill: {background}; fill-opacity: {bg_opacity}; }}
    .node {{ stroke-width: {node_stroke}; }}
    .label {{ font: 600 13.5px 'Inter', 'Segoe UI', system-ui, sans-serif;
              letter-spacing: -.008em; }}
    .sublabel {{ font: 400 11px 'Inter', 'Segoe UI', system-ui, sans-serif;
                 opacity: {sub_opacity}; }}
    .tag {{ font: 600 9px 'JetBrains Mono', ui-monospace, monospace;
            letter-spacing: .09em; opacity: {tag_opacity}; }}
    .badge {{ font: 700 9px 'Inter', 'Segoe UI', system-ui, sans-serif; fill: #ffffff; }}
    .icon-tile {{ opacity: {tile_opacity}; }}
    .icon-mark path {{ fill: none; stroke-width: 1.35; stroke-linecap: round;
                       stroke-linejoin: round; }}
    .title {{ font: 700 17px 'Inter', 'Segoe UI', system-ui, sans-serif; fill: {title}; }}
    .subtitle {{ font: 400 11.5px 'Inter', 'Segoe UI', system-ui, sans-serif;
                 fill: {subtitle}; }}
    .band {{ font: 600 10px 'Inter', 'Segoe UI', system-ui, sans-serif; fill: {band};
             letter-spacing: .08em; }}
    .group-box {{ fill: none; stroke: #94a3b8; stroke-width: 1.4; stroke-dasharray: 7 5; }}
    .group-label {{ font: 600 11px 'Inter', 'Segoe UI', system-ui, sans-serif;
                    fill: {subtitle}; letter-spacing: .02em; }}
    .group-sublabel {{ font: 400 10px 'Inter', 'Segoe UI', system-ui, sans-serif;
                       fill: {sublabel_fill}; }}
    .edge {{ fill: none; stroke: {edge}; stroke-width: {edge_stroke}; }}
    .edge.data {{ stroke: {edge_data}; }}
    .edge.dep {{ stroke: {edge_dep}; }}
    .edge-label {{ font: 400 9.5px 'Inter', 'Segoe UI', system-ui, sans-serif;
                   fill: {edge_label}; }}
    .implied {{ stroke-dasharray: 5 3; }}
{_group_rules(resolved)}
{_palette_rules(palette, "    ")}"""

    if theme != "auto":
        return "\n  <style>" + common + "\n  </style>\n"

    dark_bg, dark_title, dark_sub, dark_band = THEME_CHROME["dark"]
    return f"""
  <style>{common}
    @media (prefers-color-scheme: dark) {{
      .bg {{ fill: {dark_bg}; }}
      .title {{ fill: {dark_title}; }}
      .subtitle {{ fill: {dark_sub}; }}
      .band {{ fill: {dark_band}; }}
      .group-box {{ stroke: #47566a; }}
      .group-sublabel {{ fill: #6b7c91; }}
      .edge {{ stroke: #63768c; }}
      .edge.dep {{ stroke: #3c4a5c; }}
      .edge-label {{ fill: #7b8da2; }}
{_group_rules("dark")}
{_palette_rules(DARK_PALETTE, "      ")}
    }}
  </style>
"""


class SvgRenderer:
    """Renders a :class:`~app.generators.diagram.layout.Layout` as SVG."""

    def render(
        self,
        layout: Layout,
        theme: str = "auto",
        transparent: bool = False,
    ) -> str:
        """Render the layout as a self-contained SVG document.

        ``theme`` is "auto" for the embedded viewer and one of light, dark or
        print for an export. Geometry is identical across themes -- only the
        style block differs -- so an exported figure is the same drawing the
        user reviewed on screen, which is the point of exporting it.
        """
        w = round(layout.width, 1)
        h = round(layout.height, 1)
        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="Cloud architecture diagram for {escape(layout.title)}">',
            _css(theme, transparent),
            self._defs(theme),
            f'  <rect class="bg" width="{w}" height="{h}" rx="8"/>',
            f'  <text class="title" x="{20}" y="{28}">{escape(layout.title)}</text>',
            f'  <text class="subtitle" x="{20}" y="{45}">{escape(layout.subtitle)}</text>',
        ]

        # Painted in ascending depth so an inner boundary is never hidden
        # behind the one containing it.
        for group in sorted(layout.groups, key=lambda g: g.depth):
            parts.append(
                f'  <rect class="group-box group-{group.style}" '
                f'x="{group.x:.1f}" y="{group.y:.1f}" '
                f'width="{group.w:.1f}" height="{group.h:.1f}" rx="14"/>'
            )
            parts.append(
                f'  <text class="group-label group-label-{group.style}" '
                f'x="{group.x + 14:.1f}" y="{group.y + 18:.1f}">'
                f"{escape(group.label)}</text>"
            )
            if group.sublabel:
                offset = 14 + len(group.label) * 6.6 + 10
                parts.append(
                    f'  <text class="group-sublabel" x="{group.x + offset:.1f}" '
                    f'y="{group.y + 18:.1f}">{escape(group.sublabel)}</text>'
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
    def _defs(theme: str = "auto") -> str:
        arrow = "#333333" if theme == "print" else "#7c8ea3"
        data_arrow = "#1c6b38" if theme == "print" else "#3f9c5f"
        return (
            "  <defs>\n"
            '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
            f'      <path d="M 0 0 L 10 5 L 0 10 z" fill="{arrow}"/>\n'
            "    </marker>\n"
            '    <marker id="arrow-data" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">\n'
            f'      <path d="M 0 0 L 10 5 L 0 10 z" fill="{data_arrow}"/>\n'
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
        # Endpoint ids let an interactive viewer fade an edge with the nodes
        # it joins. Additive: no geometry or colour changes.
        out = (
            f'  <path class="{cls}" d="{path}"{dash_attr} '
            f'data-from="{escape(edge.source)}" data-to="{escape(edge.target)}" '
            f'data-kind="{escape(edge.kind)}" '
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
