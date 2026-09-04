"""Diagram layout: grouping, spacing, wrapping and determinism.

The layout engine is presentation, but it makes claims a reader will believe:
that a box drawn inside "Private subnets" really is deployed there, that two
boxes which do not overlap are two distinct resources, and that the same
requirement always draws the same picture.

These tests hold it to those claims. They are separate from test_diagram.py,
which covers what the renderer emits; this file covers where things are put.
"""

from __future__ import annotations

import pytest

from app.engine.mapper import ResourceMapper
from app.generators.diagram.layout import (
    INTERNET_ID,
    MAX_COLUMNS,
    NODE_H,
    NODE_W,
    SIDEBAR_TIERS,
    LayoutEngine,
)
from app.generators.diagram.svg_renderer import SvgRenderer
from app.models.ir import InfrastructureSpec, Kind, Resource, Tier
from app.nlp.rule_extractor import RuleExtractor

EXTRACTOR = RuleExtractor()
MAPPER = ResourceMapper()
ENGINE = LayoutEngine()


def spec_for(prompt: str) -> InfrastructureSpec:
    return MAPPER.map(EXTRACTOR.extract(prompt))


def layout_for(prompt: str):
    return ENGINE.build(spec_for(prompt))


def main_nodes(layout, spec):
    """Nodes in the main flow.

    Cross-cutting resources -- security groups, IAM roles, alarms -- are drawn
    in a deliberately smaller sidebar column rather than in the bands, so they
    are excluded from tests about band geometry.
    """
    sidebar = {
        r.id for r in spec.resources if r.tier in SIDEBAR_TIERS
    }
    return [n for n in layout.nodes if n.id != INTERNET_ID and n.id not in sidebar]


def overlap(a, b, tolerance: float = 0.5) -> bool:
    return (
        a.x + a.w - tolerance > b.x
        and b.x + b.w - tolerance > a.x
        and a.y + a.h - tolerance > b.y
        and b.y + b.h - tolerance > a.y
    )


CORPUS = [
    "one EC2 instance",
    "two web servers behind a load balancer with a postgres database",
    "a production three-tier app, highly available, with a cache and a bucket",
    "a serverless API with lambda, api gateway and dynamodb",
    "an EKS cluster with 4 nodes, a bastion host and an S3 bucket",
    "an aurora postgresql cluster in private subnets with a NAT gateway",
    "a static site in S3 behind CloudFront with a WAF and route 53",
    "a redshift warehouse in private subnets with a KMS key",
]


# --------------------------------------------------------------------------
# spacing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", CORPUS)
def test_no_two_nodes_overlap(prompt):
    """Overlapping boxes are the failure a reader notices first."""
    layout = layout_for(prompt)
    for index, first in enumerate(layout.nodes):
        for second in layout.nodes[index + 1:]:
            assert not overlap(first, second), f"{first.id} overlaps {second.id}"


@pytest.mark.parametrize("prompt", CORPUS)
def test_every_node_sits_inside_the_canvas(prompt):
    layout = layout_for(prompt)
    for node in layout.nodes:
        assert node.x >= 0 and node.y >= 0, node.id
        assert node.x + node.w <= layout.width, node.id
        assert node.y + node.h <= layout.height, node.id


@pytest.mark.parametrize("prompt", CORPUS)
def test_every_boundary_fits_the_canvas(prompt):
    """A box that spills off the canvas is clipped in every export."""
    layout = layout_for(prompt)
    for group in layout.groups:
        assert group.x >= 0 and group.y >= 0, group.label
        assert group.x + group.w <= layout.width, group.label
        assert group.y + group.h <= layout.height, group.label


def test_node_geometry_is_uniform():
    """Consistent spacing depends on consistent node sizes.

    Sidebar nodes are excluded deliberately: they are drawn smaller because
    they represent cross-cutting resources rather than a step in the flow.
    """
    prompt = "a production three-tier app with a database and a cache"
    spec = spec_for(prompt)
    layout = ENGINE.build(spec)
    for node in main_nodes(layout, spec):
        assert node.w == NODE_W, node.id
        assert node.h == NODE_H, node.id


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", CORPUS)
def test_layout_is_deterministic(prompt):
    """The same spec must draw the same picture, byte for byte."""
    spec = spec_for(prompt)
    first = SvgRenderer().render(ENGINE.build(spec))
    second = SvgRenderer().render(ENGINE.build(spec))
    assert first == second


def test_two_runs_of_one_prompt_agree():
    prompt = "a production web app with a load balancer, a database and a cache"
    assert (
        SvgRenderer().render(ENGINE.build(spec_for(prompt)))
        == SvgRenderer().render(ENGINE.build(spec_for(prompt)))
    )


# --------------------------------------------------------------------------
# grouping
# --------------------------------------------------------------------------

def test_a_vpc_design_gets_nested_boundaries():
    layout = layout_for(
        "a web app with a load balancer and a postgres database in private subnets"
    )
    styles = {g.style for g in layout.groups}
    assert {"region", "vpc", "private"} <= styles


def test_a_design_without_a_vpc_gets_no_vpc_boundary():
    """Concentric rectangles around a Lambda would say nothing."""
    layout = layout_for("a lambda function with a dynamodb table")
    styles = {g.style for g in layout.groups}
    assert "vpc" not in styles
    assert "region" not in styles


def test_boundaries_nest_rather_than_intersect():
    layout = layout_for(
        "a production web app with a load balancer, a database in private "
        "subnets and a cache"
    )
    by_depth: dict[int, list] = {}
    for group in layout.groups:
        if group.style == "sidebar":
            continue
        by_depth.setdefault(group.depth, []).append(group)

    for depth in sorted(by_depth):
        for inner in by_depth.get(depth + 1, []):
            assert any(
                parent.x <= inner.x
                and parent.y <= inner.y
                and parent.x + parent.w >= inner.x + inner.w
                and parent.y + parent.h >= inner.y + inner.h
                for parent in by_depth[depth]
            ), f"{inner.label} is not contained by any depth-{depth} boundary"


def test_boundaries_at_the_same_depth_do_not_overlap():
    layout = layout_for(
        "a load balancer in public subnets with a database in private subnets"
    )
    same = [g for g in layout.groups if g.depth == 2]
    for index, first in enumerate(same):
        for second in same[index + 1:]:
            assert not overlap(first, second), f"{first.label} overlaps {second.label}"


def test_the_subnet_boundary_agrees_with_the_mapper():
    """A box drawn in "Private subnets" must really deploy there."""
    spec = spec_for(
        "a web app with a load balancer and a postgres database in private subnets"
    )
    layout = ENGINE.build(spec)
    private = next((g for g in layout.groups if g.style == "private"), None)
    assert private is not None

    for resource in spec.resources:
        if resource.properties.get("subnet_band") != "private":
            continue
        node = layout.node(resource.id)
        if node is None:          # structural kinds are drawn as boundaries
            continue
        assert private.x <= node.x, resource.id
        assert node.x + node.w <= private.x + private.w, resource.id


def test_every_group_label_says_something():
    for prompt in CORPUS:
        for group in layout_for(prompt).groups:
            assert group.label.strip(), prompt


# --------------------------------------------------------------------------
# large architectures
# --------------------------------------------------------------------------

def build(resources: list[tuple[str, Kind, Tier]]):
    """A synthetic design of a given shape, returned with its spec."""
    spec = InfrastructureSpec(name="synthetic", environment="dev")
    for resource_id, kind, tier in resources:
        spec.add(Resource(id=resource_id, kind=kind, name=resource_id,
                          tier=tier, reason="probe"))
    mapped = MAPPER.map(spec)
    return ENGINE.build(mapped), mapped


def test_a_wide_band_wraps_instead_of_growing_forever():
    """One band of eighteen boxes is a scroll bar, not a diagram."""
    layout, _ = build([(f"fn_{i}", Kind.FUNCTION, Tier.APP) for i in range(18)])
    widest_row = MAX_COLUMNS * NODE_W + (MAX_COLUMNS - 1) * 44
    assert layout.width < widest_row + 700, "the band did not wrap"

    for index, first in enumerate(layout.nodes):
        for second in layout.nodes[index + 1:]:
            assert not overlap(first, second)


def test_wrapped_rows_are_balanced():
    """Eight nodes become two rows of four, not a six and a two."""
    layout, spec = build([(f"fn_{i}", Kind.FUNCTION, Tier.APP) for i in range(8)])
    rows: dict[int, int] = {}
    for node in main_nodes(layout, spec):
        rows[round(node.y)] = rows.get(round(node.y), 0) + 1
    counts = sorted(rows.values())
    assert max(counts) - min(counts) <= 1, f"ragged rows: {counts}"


def test_a_large_architecture_stays_proportionate():
    """Aspect ratio decides whether a big diagram is readable at all."""
    layout, _ = build(
        [(f"vm_{i}", Kind.VM, Tier.APP) for i in range(12)]
        + [(f"db_{i}", Kind.SQL_DATABASE, Tier.DATA) for i in range(8)]
    )
    ratio = layout.width / layout.height
    assert 0.3 < ratio < 4.0, f"aspect ratio {ratio:.2f} is unusable"


def test_a_very_large_architecture_still_renders_without_overlap():
    """The 200-resource case, at the scale the layout must survive."""
    layout, _ = build(
        [(f"vm_{i}", Kind.VM, Tier.APP) for i in range(40)]
        + [(f"fn_{i}", Kind.FUNCTION, Tier.APP) for i in range(30)]
        + [(f"db_{i}", Kind.SQL_DATABASE, Tier.DATA) for i in range(20)]
        + [(f"s3_{i}", Kind.OBJECT_STORAGE, Tier.DATA) for i in range(20)]
    )
    assert len(layout.nodes) >= 110
    for index, first in enumerate(layout.nodes):
        for second in layout.nodes[index + 1:]:
            assert not overlap(first, second), f"{first.id} overlaps {second.id}"


def test_a_very_large_architecture_renders_quickly():
    """Layout is O(n) in practice; a regression here would be quadratic."""
    import time

    resources = (
        [(f"vm_{i}", Kind.VM, Tier.APP) for i in range(60)]
        + [(f"db_{i}", Kind.SQL_DATABASE, Tier.DATA) for i in range(40)]
    )
    started = time.perf_counter()
    layout, _ = build(resources)
    SvgRenderer().render(layout)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"layout and render took {elapsed:.2f}s"
