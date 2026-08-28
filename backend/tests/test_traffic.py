"""Load balancer types, TLS termination and provider handling.

Three balancer types exist because they are three different services, not one
service with a flag: the listener protocol, health check shape, target group
protocol and security group attachment all follow from the choice. Emitting an
application load balancer for a "network load balancer" request gave the user
layer 7 semantics for a layer 4 requirement.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.engine.pipeline import Pipeline
from app.main import app
from app.models.ir import Kind
from app.nlp.rule_extractor import RuleExtractor

PIPELINE = Pipeline()
EXTRACTOR = RuleExtractor()
BALANCERS = (Kind.LOAD_BALANCER, Kind.NETWORK_LOAD_BALANCER, Kind.GATEWAY_LOAD_BALANCER)


def edge_of(prompt: str) -> str:
    return PIPELINE.run(prompt).terraform.get("edge.tf", "")


def balancer_types(prompt: str) -> list[str]:
    return re.findall(r'load_balancer_type\s+=\s+"(\w+)"', edge_of(prompt))


def listeners(prompt: str) -> list[tuple[str, str]]:
    return re.findall(
        r'resource "aws_lb_listener" "(\w+)"[\s\S]*?protocol\s+=\s+"(\w+)"', edge_of(prompt)
    )


# --------------------------------------------------------------------------
# balancer type selection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected", [
    ("web servers behind a load balancer", "application"),
    ("web servers behind an application load balancer", "application"),
    ("an ALB in front of web servers", "application"),
    ("a network load balancer in front of web servers", "network"),
    ("an NLB in front of web servers", "network"),
    ("a layer 4 load balancer for web servers", "network"),
    ("a TCP load balancer for web servers", "network"),
    ("a gateway load balancer for an appliance fleet", "gateway"),
    ("a GWLB for a firewall fleet", "gateway"),
])
def test_the_requested_balancer_type_is_generated(prompt, expected):
    assert balancer_types(prompt) == [expected], prompt


def test_only_one_balancer_is_created():
    """"two web servers" infers a balancer; an explicit NLB must not add a second."""
    prompt = "a network load balancer in front of two web servers"
    spec = EXTRACTOR.extract(prompt)
    assert len([r for r in spec.resources if r.kind in BALANCERS]) == 1
    assert balancer_types(prompt) == ["network"]


def test_network_balancers_carry_no_security_group():
    """AWS attaches security groups to application load balancers."""
    edge = edge_of("a network load balancer for web servers")
    block = re.search(r'resource "aws_lb"[\s\S]*?\n\}', edge).group(0)
    assert "security_groups" not in block


def test_application_balancers_do_carry_one():
    edge = edge_of("an application load balancer for web servers")
    block = re.search(r'resource "aws_lb"[\s\S]*?\n\}', edge).group(0)
    assert "security_groups" in block


def test_gateway_balancers_are_internal_and_private():
    edge = edge_of("a gateway load balancer for an appliance fleet")
    block = re.search(r'resource "aws_lb"[\s\S]*?\n\}', edge).group(0)
    # Matched by regex rather than literal text: the writer aligns the `=`,
    # so the padding depends on the longest attribute name in the block.
    assert re.search(r"internal\s+=\s+true", block)
    assert "aws_subnet.private" in block


# --------------------------------------------------------------------------
# target group protocol follows the balancer
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,protocol", [
    ("web servers behind a load balancer", "HTTP"),
    ("a network load balancer for web servers", "TCP"),
    ("a gateway load balancer for an appliance fleet", "GENEVE"),
])
def test_target_group_protocol_matches_the_balancer(prompt, protocol):
    edge = edge_of(prompt)
    match = re.search(r'resource "aws_lb_target_group"[\s\S]*?protocol\s+=\s+"(\w+)"', edge)
    assert match and match.group(1) == protocol


def test_layer_4_health_checks_have_no_http_path():
    """A TCP health check cannot carry a path or a status matcher."""
    edge = edge_of("a network load balancer for web servers")
    health = re.search(r"health_check \{[\s\S]*?\n  \}", edge).group(0)
    assert "path" not in health
    assert "matcher" not in health


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "an application load balancer with HTTPS for web servers",
    "a load balancer with TLS for web servers",
    "a load balancer with an SSL certificate for web servers",
    "a load balancer with an ACM certificate for web servers",
    "a load balancer on port 443 for web servers",
])
def test_tls_requests_produce_a_certificate(prompt):
    assert PIPELINE.run(prompt).spec.has(Kind.CERTIFICATE), prompt


def test_a_certificate_is_not_invented_without_tls():
    assert not PIPELINE.run("web servers behind a load balancer").spec.has(Kind.CERTIFICATE)


def test_https_produces_both_listeners():
    """The plain listener redirects rather than disappearing."""
    found = dict(listeners("an application load balancer with https for web servers"))
    assert found.get("https") == "HTTPS"
    assert found.get("http") == "HTTP"
    edge = edge_of("an application load balancer with https for web servers")
    assert "redirect" in edge
    assert "HTTP_301" in edge


def test_network_balancers_terminate_tls_not_https():
    """A layer 4 listener speaks TLS; HTTPS is a layer 7 protocol name."""
    found = dict(listeners("a network load balancer with TLS termination for web servers"))
    assert found.get("https") == "TLS"
    assert found.get("http") == "TCP"


def test_the_certificate_is_a_mandatory_dependency_of_a_tls_listener():
    spec = PIPELINE.run("a load balancer with https for web servers").spec
    certificate = spec.first(Kind.CERTIFICATE)
    assert certificate is not None
    assert "cannot be created without a certificate" in certificate.reason


def test_tls_listener_pins_a_modern_policy():
    edge = edge_of("a load balancer with https for web servers")
    assert "ELBSecurityPolicy-TLS13" in edge


def test_domain_name_variable_is_declared_once():
    """A DNS zone declares it too; two declarations would not plan."""
    files = PIPELINE.run(
        "a load balancer with https and a route 53 domain for web servers"
    ).terraform
    text = "\n".join(v for k, v in files.items() if k.endswith(".tf"))
    assert text.count('variable "domain_name"') == 1


# --------------------------------------------------------------------------
# provider handling
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,provider", [
    ("an Azure virtual machine with a storage account", "azure"),
    ("an Azure Kubernetes Service cluster", "azure"),
    ("a GCP compute engine instance", "gcp"),
    ("a BigQuery dataset and a cloud run service", "gcp"),
    ("a google cloud storage bucket", "gcp"),
    ("an Oracle Cloud Infrastructure instance", "oci"),
])
def test_other_clouds_are_refused_not_substituted(prompt, provider):
    spec = PIPELINE.run(prompt).spec
    assert spec.unsupported_provider == provider
    assert spec.resources == [], "AWS resources were generated for a non-AWS request"


def test_the_refusal_is_an_error_finding():
    result = PIPELINE.run("an Azure virtual machine")
    codes = {f.code: f.severity for f in result.findings}
    assert codes.get("unsupported_provider") == "error"


def test_naming_aws_wins_the_tie():
    """"migrate from Azure to AWS" is an AWS request."""
    spec = PIPELINE.run("migrate from Azure to AWS with EC2 instances and S3").spec
    assert spec.unsupported_provider is None
    assert spec.has(Kind.VM)


def test_the_api_explains_the_refusal():
    client = TestClient(app)
    response = client.post("/api/generate", json={"prompt": "an Azure virtual machine"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Microsoft Azure" in detail
    assert "AWS Terraform only" in detail


def test_aws_requests_are_unaffected():
    assert PIPELINE.run("an EC2 instance").spec.unsupported_provider is None
