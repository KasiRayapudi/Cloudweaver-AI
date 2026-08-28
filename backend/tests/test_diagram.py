"""Tests for the diagram generators."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.engine.mapper import ResourceMapper
from app.generators.diagram.layout import INTERNET_ID, LayoutEngine
from app.generators.diagram.mermaid import MermaidRenderer
from app.generators.diagram.svg_renderer import SvgRenderer
from app.models.ir import Kind
from app.nlp.rule_extractor import RuleExtractor
from tests.conftest import ALL_PROMPTS


def spec_for(text: str):
    return ResourceMapper().map(RuleExtractor().extract(text))


def layout_for(text: str):
    return LayoutEngine().build(spec_for(text))


# -- layout ----------------------------------------------------------------

def test_nodes_are_layered_top_to_bottom():
    layout = layout_for("web servers behind a load balancer with a postgres database")
    alb = layout.node("alb")
    app = layout.node("app_server")
    db = layout.node("app_db")
    assert alb.y < app.y < db.y


def test_nodes_in_a_band_do_not_overlap():
    layout = layout_for(
        "a load balancer, an s3 bucket, a postgres database, a redis cache, "
        "a dynamodb table and a web server"
    )
    for a in layout.nodes:
        for b in layout.nodes:
            if a is b:
                continue
            overlap_x = a.x < b.x + b.w and b.x < a.x + a.w
            overlap_y = a.y < b.y + b.h and b.y < a.y + a.h
            assert not (overlap_x and overlap_y), f"{a.id} overlaps {b.id}"


def test_internet_node_present_only_for_public_entry_points():
    assert layout_for("a public load balancer with web servers").node(INTERNET_ID)
    assert layout_for("a dynamodb table").node(INTERNET_ID) is None


def test_structural_resources_are_not_drawn_as_nodes():
    layout = layout_for("two web servers with a database")
    ids = {n.id for n in layout.nodes}
    assert "main" not in ids        # the VPC is a container, not a box
    assert "public_rt" not in ids   # route tables are implementation detail


def test_vpc_container_wraps_its_contents():
    layout = layout_for("web servers behind a load balancer with a database")
    assert layout.groups
    vpc = layout.groups[0]
    inside = [n for n in layout.nodes if n.band in (2, 3, 4)]
    for node in inside:
        assert vpc.x <= node.x and node.x + node.w <= vpc.x + vpc.w
        assert vpc.y <= node.y and node.y + node.h <= vpc.y + vpc.h


def test_canvas_contains_every_node():
    layout = layout_for(ALL_PROMPTS[0])
    for node in layout.nodes:
        assert node.x + node.w <= layout.width
        assert node.y + node.h <= layout.height


def test_edges_have_at_least_two_points():
    layout = layout_for(ALL_PROMPTS[0])
    assert layout.edges
    for edge in layout.edges:
        assert len(edge.points) >= 2


def test_empty_spec_produces_an_empty_canvas():
    layout = LayoutEngine().build(spec_for("a poem about clouds"))
    assert layout.nodes == []
    assert layout.width > 0


# -- SVG -------------------------------------------------------------------

@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_svg_is_well_formed_xml(prompt):
    svg = SvgRenderer().render(layout_for(prompt))
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")


def test_svg_is_self_contained():
    svg = SvgRenderer().render(layout_for(ALL_PROMPTS[0]))
    assert "<script" not in svg
    assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in svg


def test_svg_labels_every_resource():
    layout = layout_for("a load balancer with web servers and a postgres database")
    svg = SvgRenderer().render(layout)
    for label in ("Application Load Balancer", "EC2 Instance", "RDS Instance"):
        assert label in svg


def test_svg_escapes_user_derived_text():
    layout = layout_for("a <script>alert(1)</script> web server")
    svg = SvgRenderer().render(layout)
    assert "<script>" not in svg


def test_svg_supports_dark_mode():
    svg = SvgRenderer().render(layout_for(ALL_PROMPTS[0]))
    assert "prefers-color-scheme: dark" in svg


# -- Mermaid ---------------------------------------------------------------

@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_mermaid_starts_with_a_flowchart(prompt):
    text = MermaidRenderer().render(spec_for(prompt))
    assert text.startswith("flowchart TD")


def test_mermaid_subgraphs_are_balanced():
    text = MermaidRenderer().render(spec_for(ALL_PROMPTS[0]))
    ends = [line for line in text.splitlines() if line.strip() == "end"]
    assert text.count("subgraph") == len(ends)


def test_mermaid_only_links_declared_nodes():
    spec = spec_for(ALL_PROMPTS[0])
    text = MermaidRenderer().render(spec)
    declared = {line.strip().split("[")[0] for line in text.splitlines()
                if "[" in line and "-->" not in line and "subgraph" not in line}
    for line in text.splitlines():
        if "-->" in line or "-.->" in line:
            source = line.strip().split()[0]
            assert source in declared


def test_mermaid_handles_empty_spec():
    assert "empty" in MermaidRenderer().render(spec_for("hello there"))


def test_diagram_and_terraform_describe_the_same_resources(three_tier):
    """The core promise of the paper: one IR, two consistent outputs."""
    spec = three_tier.spec
    terraform = "\n".join(three_tier.terraform.values())
    svg = three_tier.diagram_svg

    for resource in spec.resources:
        if resource.kind in (Kind.ROUTE_TABLE,):
            continue
        assert resource.id in terraform or resource.name in terraform, resource.id
    drawn = {n.id for n in LayoutEngine().build(spec).nodes}
    for resource in spec.resources:
        if resource.kind in (Kind.VPC, Kind.SUBNET_PUBLIC, Kind.SUBNET_PRIVATE,
                             Kind.ROUTE_TABLE, Kind.INTERNET_GATEWAY):
            continue
        assert resource.id in drawn, f"{resource.id} generated but not drawn"
    assert svg


# --------------------------------------------------------------------------
# node identity, for the interactive viewer
# --------------------------------------------------------------------------

def test_every_drawn_node_carries_its_resource_id():
    """The viewer maps a click back to the shared model through this id.

    Without it a click could only be matched on the visible label, which is
    ambiguous the moment two resources share a display name.
    """
    spec = spec_for("a load balancer with web servers, a database and a cache")
    layout = LayoutEngine().build(spec)
    svg = SvgRenderer().render(layout)
    for node in layout.nodes:
        assert f'data-resource-id="{node.id}"' in svg, node.id


def test_node_ids_in_the_svg_exist_in_the_model():
    import re as _re
    spec = spec_for(ALL_PROMPTS[0])
    svg = SvgRenderer().render(LayoutEngine().build(spec))
    ids = set(_re.findall(r'data-resource-id="([^"]+)"', svg))
    known = {r.id for r in spec.resources} | {INTERNET_ID}
    assert ids <= known, ids - known


def test_nodes_are_reachable_by_keyboard():
    svg = SvgRenderer().render(layout_for(ALL_PROMPTS[0]))
    assert 'tabindex="0"' in svg
    assert 'role="button"' in svg


def test_node_identity_is_escaped():
    """Ids reach the markup, so they must not be able to break out of it."""
    from app.models.ir import InfrastructureSpec, Resource, Tier
    spec = InfrastructureSpec(name="x")
    spec.add(Resource(id='a"><script>', kind=Kind.VM, name="EC2", tier=Tier.APP, reason="p"))
    svg = SvgRenderer().render(LayoutEngine().build(spec))
    assert "<script>" not in svg
