"""Deterministic layered layout for architecture diagrams.

Graphviz is not a dependency: the graphs this system produces are small,
strongly layered (internet -> edge -> public -> app -> data) and benefit from
knowing what a cloud diagram is supposed to look like.  A purpose-built layout
gives better results here than a general graph layout engine, and it removes a
native binary from the install story.

The layout runs in four passes:

1. **Rank** every resource into a horizontal band from its tier.
2. **Order** nodes within a band to minimise edge crossings (barycentre sort
   against the band above, which converges in one pass for graphs this shape).
3. **Position** nodes, centring each band over the widest one.
4. **Route** edges as orthogonal polylines, nudging parallel segments apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.ir import EdgeKind, InfrastructureSpec, Kind, Resource, Tier
from app.nlp.catalog import service_for

# Geometry. Widened and given more air than the first pass: at 178x68 with a
# 34px gutter the bands read as a wall of boxes rather than as tiers, and long
# service names had nowhere to go.
NODE_W = 200
NODE_H = 78
H_GAP = 44
V_GAP = 96
MARGIN = 44
BAND_LABEL_W = 104
SIDEBAR_GAP = 64
CORNER = 12          # radius used when a connector turns

# Bands, top to bottom. Resources are ranked into these.
BAND_ORDER: tuple[tuple[str, tuple[Tier, ...]], ...] = (
    ("Internet", ()),
    ("Global / Edge", (Tier.GLOBAL, Tier.EDGE)),
    ("Public subnets", (Tier.PUBLIC,)),
    ("Private subnets", (Tier.APP,)),
    ("Data layer", (Tier.DATA,)),
)

# Rendered beside the main flow rather than inside it: these attach to many
# nodes at once and would turn the diagram into a hairball if laid out inline.
SIDEBAR_TIERS: frozenset[Tier] = frozenset({Tier.SECURITY, Tier.OPS})

# Network scaffolding is drawn as the VPC container and its band labels, not
# as free-standing boxes.
STRUCTURAL_KINDS: frozenset[Kind] = frozenset({
    Kind.VPC, Kind.SUBNET_PUBLIC, Kind.SUBNET_PRIVATE, Kind.ROUTE_TABLE,
    Kind.INTERNET_GATEWAY,
})

INTERNET_ID = "__internet__"


@dataclass
class Node:
    id: str
    label: str
    sublabel: str
    glyph: str
    category: str
    x: float = 0.0
    y: float = 0.0
    w: float = NODE_W
    h: float = NODE_H
    band: int = 0
    count: int = 1
    origin: str = "explicit"
    detail: str = ""

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class RoutedEdge:
    source: str
    target: str
    kind: str
    label: str | None
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Group:
    label: str
    sublabel: str
    x: float
    y: float
    w: float
    h: float
    style: str = "vpc"


@dataclass
class Layout:
    width: float
    height: float
    nodes: list[Node]
    edges: list[RoutedEdge]
    groups: list[Group]
    band_labels: list[tuple[str, float]]
    title: str
    subtitle: str

    def node(self, node_id: str) -> Node | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None


class LayoutEngine:
    """Turns an ``InfrastructureSpec`` into positioned diagram primitives."""

    def build(self, spec: InfrastructureSpec) -> Layout:
        nodes, sidebar = self._make_nodes(spec)
        if not nodes:
            return Layout(
                width=520, height=180, nodes=[], edges=[], groups=[], band_labels=[],
                title=spec.name, subtitle="No resources to display",
            )

        for node in nodes:
            node.band = self._band_for(node, spec)

        bands = self._rank(nodes)
        self._order(bands, spec)
        width = self._position(bands)
        edges = self._route(spec, nodes, sidebar)
        self._place_sidebar(sidebar, bands, width)

        all_nodes = nodes + sidebar
        groups = self._groups(spec, bands)
        if sidebar:
            groups.append(Group(
                label="Cross-cutting",
                sublabel="applied across the stack",
                x=min(n.x for n in sidebar) - 14,
                y=min(n.y for n in sidebar) - 30,
                w=max(n.w for n in sidebar) + 28,
                h=(max(n.y + n.h for n in sidebar) - min(n.y for n in sidebar)) + 44,
                style="sidebar",
            ))
        # Sized last so a container box can never spill past the canvas edge.
        content_w = max(
            [n.x + n.w for n in all_nodes] + [g.x + g.w for g in groups] or [width]
        )
        content_h = max(
            [n.y + n.h for n in all_nodes] + [g.y + g.h for g in groups] or [200]
        )

        band_labels = [
            (BAND_ORDER[index][0], min(n.y for n in band) + NODE_H / 2)
            for index, band in bands.items()
            if band and index > 0
        ]

        return Layout(
            width=content_w + MARGIN,
            height=content_h + MARGIN,
            nodes=all_nodes,
            edges=edges,
            groups=groups,
            band_labels=band_labels,
            title=spec.name,
            subtitle=f"{spec.provider.value.upper()} - {spec.region} - {spec.environment}",
        )

    # -- pass 1: node construction ----------------------------------------

    def _make_nodes(self, spec: InfrastructureSpec) -> tuple[list[Node], list[Node]]:
        main: list[Node] = []
        sidebar: list[Node] = []

        entry_points = [
            r for r in spec.resources
            if r.tier in (Tier.EDGE, Tier.GLOBAL)
            or (r.kind is Kind.LOAD_BALANCER and not r.properties.get("internal"))
        ]
        if entry_points:
            main.append(Node(
                id=INTERNET_ID, label="Internet", sublabel="public traffic",
                glyph="WWW", category="internet", band=0, w=140, h=52,
            ))

        for resource in spec.resources:
            if resource.kind in STRUCTURAL_KINDS:
                continue
            node = self._to_node(resource)
            if resource.tier in SIDEBAR_TIERS:
                sidebar.append(node)
            else:
                main.append(node)
        return main, sidebar

    @staticmethod
    def _to_node(resource: Resource) -> Node:
        info = service_for(resource.kind)
        sub_bits: list[str] = []
        props = resource.properties
        for key in ("instance_type", "engine", "node_type", "runtime", "billing_mode",
                    "node_instance_type"):
            if key in props:
                sub_bits.append(str(props[key]))
                break
        if resource.count > 1:
            sub_bits.append(f"x{resource.count}")
        if not sub_bits:
            sub_bits.append(resource.id.replace("_", " "))

        detail_bits = [f"{k}={v}" for k, v in list(props.items())[:6]
                       if not isinstance(v, (dict, list))]
        return Node(
            id=resource.id,
            label=info.display,
            sublabel=" - ".join(sub_bits)[:32],
            glyph=info.glyph,
            category=info.category,
            count=resource.count,
            origin=resource.origin.value,
            detail="; ".join(detail_bits),
        )

    # -- pass 2: ranking ---------------------------------------------------

    @staticmethod
    def _band_for(node: Node, spec: InfrastructureSpec) -> int:
        if node.id == INTERNET_ID:
            return 0
        resource = spec.get(node.id)
        if resource is None:
            return 3
        for index, (_, tiers) in enumerate(BAND_ORDER):
            if resource.tier in tiers:
                return index
        return 3

    def _rank(self, nodes: list[Node]) -> dict[int, list[Node]]:
        bands: dict[int, list[Node]] = {}
        for node in nodes:
            bands.setdefault(node.band, []).append(node)
        return bands

    # -- pass 3: ordering --------------------------------------------------

    def _order(self, bands: dict[int, list[Node]], spec: InfrastructureSpec) -> None:
        """Barycentre ordering: pull each node toward the average x of its parents."""
        parents: dict[str, list[str]] = {}
        for edge in spec.edges:
            if edge.kind is EdgeKind.CONTAINMENT:
                continue
            parents.setdefault(edge.target, []).append(edge.source)

        placed: dict[str, int] = {}
        for index in sorted(bands):
            band = bands[index]
            if index == 0:
                for position, node in enumerate(band):
                    placed[node.id] = position
                continue

            def key(node: Node) -> tuple[float, str]:
                positions = [placed[p] for p in parents.get(node.id, []) if p in placed]
                bary = sum(positions) / len(positions) if positions else 99.0
                return (bary, node.label)

            band.sort(key=key)
            for position, node in enumerate(band):
                placed[node.id] = position

    # -- pass 4: positioning ----------------------------------------------

    def _position(self, bands: dict[int, list[Node]]) -> float:
        widths = {
            index: len(band) * NODE_W + (len(band) - 1) * H_GAP
            for index, band in bands.items() if band
        }
        max_width = max(widths.values(), default=NODE_W)
        left = MARGIN + BAND_LABEL_W

        y = MARGIN + 46  # leave room for the title
        for index in sorted(bands):
            band = bands[index]
            if not band:
                continue
            row_width = widths[index]
            x = left + (max_width - row_width) / 2
            row_height = max(n.h for n in band)
            for node in band:
                node.x = x
                node.y = y + (row_height - node.h) / 2
                x += node.w + H_GAP
            y += row_height + V_GAP
        return left + max_width

    def _place_sidebar(
        self, sidebar: list[Node], bands: dict[int, list[Node]], width: float
    ) -> None:
        if not sidebar:
            return
        x = width + SIDEBAR_GAP
        y = MARGIN + 46
        for node in sidebar:
            node.x = x
            node.y = y
            node.w = 168
            node.h = 56
            y += node.h + 16

    # -- edge routing ------------------------------------------------------

    def _route(
        self, spec: InfrastructureSpec, nodes: list[Node], sidebar: list[Node]
    ) -> list[RoutedEdge]:
        # Security groups, IAM roles and alarms attach to many nodes at once.
        # Drawing those edges turns the canvas into a hairball for no gain, so
        # the sidebar column is shown without connectors -- the Mermaid export
        # and the shared-model view still carry the full graph.
        index = {n.id: n for n in nodes}
        sidebar_ids = {n.id for n in sidebar}
        routed: list[RoutedEdge] = []

        entry = [
            n for n in nodes
            if n.band == 1 or (n.band == 2 and n.category == "traffic")
        ]
        if INTERNET_ID in index:
            internet = index[INTERNET_ID]
            for node in entry:
                routed.append(RoutedEdge(
                    INTERNET_ID, node.id, "traffic", None,
                    self._points(internet, node),
                ))

        for edge in spec.edges:
            if edge.kind is EdgeKind.CONTAINMENT:
                continue
            if edge.source in sidebar_ids or edge.target in sidebar_ids:
                continue
            source = index.get(edge.source)
            target = index.get(edge.target)
            if source is None or target is None:
                continue
            routed.append(RoutedEdge(
                edge.source, edge.target, edge.kind.value, edge.label,
                self._points(source, target),
            ))
        return routed

    @staticmethod
    def _points(source: Node, target: Node) -> list[tuple[float, float]]:
        """Orthogonal-ish route between two boxes."""
        if target.y > source.y + source.h:          # downward
            start = (source.cx, source.y + source.h)
            end = (target.cx, target.y)
            mid_y = (start[1] + end[1]) / 2
            if abs(start[0] - end[0]) < 2:
                return [start, end]
            return [start, (start[0], mid_y), (end[0], mid_y), end]
        if source.y > target.y + target.h:          # upward
            start = (source.cx, source.y)
            end = (target.cx, target.y + target.h)
            mid_y = (start[1] + end[1]) / 2
            return [start, (start[0], mid_y), (end[0], mid_y), end]
        # same band: route sideways
        if target.x >= source.x:
            start = (source.x + source.w, source.cy)
            end = (target.x, target.cy)
        else:
            start = (source.x, source.cy)
            end = (target.x + target.w, target.cy)
        mid_x = (start[0] + end[0]) / 2
        if abs(start[1] - end[1]) < 2:
            return [start, end]
        return [start, (mid_x, start[1]), (mid_x, end[1]), end]

    # -- containers --------------------------------------------------------

    def _groups(self, spec: InfrastructureSpec, bands: dict[int, list[Node]]) -> list[Group]:
        vpc = spec.first(Kind.VPC)
        if vpc is None:
            return []
        inside = [n for index in (2, 3, 4) for n in bands.get(index, [])]
        if not inside:
            return []
        x = min(n.x for n in inside) - 26
        y = min(n.y for n in inside) - 34
        right = max(n.x + n.w for n in inside) + 26
        bottom = max(n.y + n.h for n in inside) + 24
        return [Group(
            label="VPC",
            sublabel=f"{vpc.properties.get('cidr_block', '10.0.0.0/16')} - "
                     f"{spec.availability_zones} AZs",
            x=x, y=y, w=right - x, h=bottom - y,
        )]
