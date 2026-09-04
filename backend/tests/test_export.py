"""Export: themed diagrams and the architecture report.

The property these tests exist to protect is that **an exported artefact
looks the same wherever it is opened**. The on-screen diagram carries a
prefers-color-scheme query, which is right for the embedded viewer and wrong
for a figure in a paper: the same file would invert itself on a reviewer's
dark-themed laptop. Several tests below check that no export carries one.

The report is HTML rather than a binary PDF, which is what makes its content
assertable here at all. A hand-rolled PDF could only be checked by opening it.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.engine.pipeline import Pipeline
from app.export.report import DOMAINS, build_report
from app.generators.diagram.layout import LayoutEngine
from app.generators.diagram.svg_renderer import THEMES, SvgRenderer
from app.main import app

PIPELINE = Pipeline()
RENDERER = SvgRenderer()
ENGINE = LayoutEngine()

THREE_TIER = (
    "a production three-tier web app in eu-west-1 with an auto scaling group "
    "behind a load balancer, a Multi-AZ PostgreSQL database in private "
    "subnets, a Redis cache, an S3 bucket and a NAT gateway"
)

EXPORT_THEMES = ("light", "dark", "print")


@pytest.fixture(scope="module")
def design():
    return PIPELINE.run(THREE_TIER)


def svg_for(design, theme: str, transparent: bool = False) -> str:
    return RENDERER.render(ENGINE.build(design.spec), theme, transparent)


def node_geometry(svg: str) -> list[tuple[str, str]]:
    return re.findall(r'<rect class="node[^"]*" x="([\d.]+)" y="([\d.]+)"', svg)


# ==========================================================================
# the property that matters: an export looks the same everywhere
# ==========================================================================

@pytest.mark.parametrize("theme", EXPORT_THEMES)
def test_an_export_carries_no_media_query(theme, design):
    """A figure that inverts on the reader's laptop is a defect, not a taste."""
    assert "prefers-color-scheme" not in svg_for(design, theme)


def test_the_embedded_viewer_still_follows_the_system_theme():
    """The on-screen copy should adapt; only exports must be fixed."""
    result = PIPELINE.run("a web server with a database")
    assert "prefers-color-scheme" in result.diagram_svg


@pytest.mark.parametrize("theme", EXPORT_THEMES)
def test_geometry_is_identical_across_themes(theme, design):
    """An export must be the same drawing the user reviewed on screen."""
    assert node_geometry(svg_for(design, theme)) == node_geometry(
        svg_for(design, "light")
    )


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_renders_deterministically(theme, design):
    assert svg_for(design, theme) == svg_for(design, theme)


# ==========================================================================
# themes
# ==========================================================================

def test_light_and_dark_differ_in_ground_not_in_shape(design):
    light = svg_for(design, "light")
    dark = svg_for(design, "dark")
    assert light != dark
    assert node_geometry(light) == node_geometry(dark)
    assert "#ffffff" in light
    assert "#0f141b" in dark


def test_the_print_theme_uses_white_fills_so_greyscale_survives(design):
    """A pale tint disappears in a greyscale reproduction; a stroke does not."""
    svg = svg_for(design, "print")
    fills = re.findall(r"\.fill-\w+ \{ fill: (#\w+);", svg)
    assert fills, "the print theme defines no fills"
    assert set(fills) == {"#ffffff"}


def test_the_print_theme_draws_heavier_strokes(design):
    """Line weight is what carries at 300 dpi."""
    print_svg = svg_for(design, "print")
    light_svg = svg_for(design, "light")
    print_weight = float(re.search(r"\.node \{ stroke-width: ([\d.]+)", print_svg).group(1))
    light_weight = float(re.search(r"\.node \{ stroke-width: ([\d.]+)", light_svg).group(1))
    assert print_weight > light_weight


@pytest.mark.parametrize("theme", EXPORT_THEMES)
def test_transparent_export_drops_only_the_background(theme, design):
    solid = svg_for(design, theme)
    clear = svg_for(design, theme, transparent=True)
    assert "fill-opacity: 0" in clear
    assert "fill-opacity: 1" in solid
    assert node_geometry(clear) == node_geometry(solid)


# ==========================================================================
# nothing is clipped
# ==========================================================================

CLIPPING_CORPUS = [
    "one EC2 instance",
    "a production three-tier app with a database, a cache and a bucket",
    "an EKS cluster with 4 nodes, a bastion and an S3 bucket",
    "a static site in S3 behind CloudFront with a WAF",
]


@pytest.mark.parametrize("prompt", CLIPPING_CORPUS)
@pytest.mark.parametrize("theme", EXPORT_THEMES)
def test_nothing_is_clipped_in_any_theme(prompt, theme):
    """The viewBox must contain every node and every boundary."""
    layout = ENGINE.build(PIPELINE.run(prompt).spec)
    svg = RENDERER.render(layout, theme)

    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert box, "no viewBox"
    width, height = float(box.group(1)), float(box.group(2))

    for node in layout.nodes:
        assert node.x >= 0 and node.y >= 0, node.id
        assert node.x + node.w <= width, node.id
        assert node.y + node.h <= height, node.id
    for group in layout.groups:
        assert group.x >= 0 and group.y >= 0, group.label
        assert group.x + group.w <= width, group.label
        assert group.y + group.h <= height, group.label


def test_the_svg_declares_a_viewbox_so_it_scales(design):
    """Without a viewBox an SVG cannot be resized without clipping."""
    for theme in EXPORT_THEMES:
        assert 'viewBox="0 0 ' in svg_for(design, theme)


def test_a_large_architecture_exports_without_clipping():
    prompt = (
        "a production platform with an EKS cluster of 6 nodes, an auto scaling "
        "group behind a load balancer, an aurora postgresql cluster, a redis "
        "cache, three S3 buckets, a NAT gateway, a bastion host, an SQS queue "
        "and cloudwatch monitoring"
    )
    layout = ENGINE.build(PIPELINE.run(prompt).spec)
    svg = RENDERER.render(layout, "print")
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    width, height = float(box.group(1)), float(box.group(2))
    for node in layout.nodes:
        assert node.x + node.w <= width and node.y + node.h <= height, node.id


# ==========================================================================
# the architecture report
# ==========================================================================

def test_the_report_contains_every_expected_section(design):
    report = build_report(design)
    for heading in (
        "Executive summary", "Architecture diagram", "Infrastructure components",
        "Networking", "Security", "Terraform", "Validation",
        "Optimisation recommendations", "Cost breakdown", "Decision trace",
        "Deployment notes",
    ):
        assert f"<h2>{heading}</h2>" in report, heading


def test_the_report_embeds_a_theme_independent_diagram(design):
    report = build_report(design)
    assert "<svg" in report
    assert "prefers-color-scheme" not in report


def test_the_report_states_the_real_figures(design):
    report = build_report(design)
    summary = design.to_dict()["summary"]
    assert str(summary["resource_count"]) in report
    assert summary["region"] in report
    assert design.spec.name in report


def test_the_report_names_every_resource(design):
    report = build_report(design)
    for resource in design.spec.resources:
        assert resource.id in report, resource.id


def test_the_report_carries_the_decision_trace(design):
    """A report without provenance is a picture with a caption."""
    report = build_report(design)
    dependencies = [e for e in design.explanations if e.rule]
    assert dependencies, "this design should contain mandatory dependencies"
    for explanation in dependencies[:3]:
        assert explanation.rule in report, explanation.rule


def test_the_report_says_recommendations_are_not_applied(design):
    report = build_report(design)
    assert "advisory" in report.lower()
    assert "has been applied" in report.lower()


def test_the_report_admits_the_cost_figures_are_approximate(design):
    report = build_report(design)
    assert "not live AWS" in report or "static per-service" in report


def test_the_report_is_deterministic_apart_from_its_timestamp(design):
    """Two runs must differ only in the generated-at line."""
    strip = lambda text: re.sub(r"generated \d.*?UTC", "", text)  # noqa: E731
    assert strip(build_report(design)) == strip(build_report(design))


def test_the_report_carries_a_footer(design):
    assert "Generated by Cloudweaver AI" in build_report(design)


def test_the_report_declares_print_rules(design):
    """Without @page the PDF would use browser defaults and reflow."""
    report = build_report(design)
    assert "@page" in report
    assert "@media print" in report


def test_a_section_is_omitted_when_it_would_be_empty():
    """An empty "Analytics" heading is worse than no heading."""
    report = build_report(PIPELINE.run("an S3 bucket"))
    assert "<h3>Compute</h3>" not in report
    assert "<h3>Storage</h3>" in report


def test_every_domain_maps_to_real_kinds():
    from app.models.ir import Kind

    for label, kinds in DOMAINS:
        assert kinds, label
        for kind in kinds:
            assert isinstance(kind, Kind), label


def test_the_report_escapes_content_from_the_prompt():
    """Resource names come from user text and reach the markup."""
    result = PIPELINE.run('an EC2 instance called <script>alert(1)</script>')
    report = build_report(result)
    assert "<script>alert(1)</script>" not in report


# ==========================================================================
# the endpoints
# ==========================================================================

@pytest.mark.parametrize("theme", EXPORT_THEMES)
def test_the_diagram_endpoint_returns_svg(theme):
    client = TestClient(app)
    response = client.post("/api/export/diagram",
                           json={"prompt": THREE_TIER, "theme": theme})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "attachment" in response.headers["content-disposition"]
    assert "prefers-color-scheme" not in response.text


def test_the_diagram_endpoint_supports_transparency():
    client = TestClient(app)
    response = client.post("/api/export/diagram", json={
        "prompt": THREE_TIER, "theme": "light", "transparent": True,
    })
    assert response.status_code == 200
    assert "fill-opacity: 0" in response.text


def test_the_report_endpoint_returns_html():
    client = TestClient(app)
    response = client.post("/api/export/report", json={"prompt": THREE_TIER})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h2>Executive summary</h2>" in response.text


def test_the_export_endpoints_refuse_an_empty_design():
    client = TestClient(app)
    for path in ("/api/export/diagram", "/api/export/report"):
        response = client.post(path, json={"prompt": "hello there"})
        assert response.status_code == 422, path


def test_the_export_endpoints_refuse_an_unsupported_provider():
    client = TestClient(app)
    for path in ("/api/export/diagram", "/api/export/report"):
        response = client.post(path, json={"prompt": "an Azure virtual machine"})
        assert response.status_code == 422, path


def test_an_invalid_theme_is_rejected():
    client = TestClient(app)
    response = client.post("/api/export/diagram",
                           json={"prompt": THREE_TIER, "theme": "neon"})
    assert response.status_code == 422


def test_two_identical_export_requests_agree():
    """No session state, so the same request must give the same bytes."""
    client = TestClient(app)
    body = {"prompt": THREE_TIER, "theme": "print"}
    first = client.post("/api/export/diagram", json=body).text
    second = client.post("/api/export/diagram", json=body).text
    assert first == second
