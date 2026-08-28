"""The rule this project is judged on.

    A resource may exist only if the user asked for it, or if something the
    user asked for cannot be deployed without it.

Each test below states an expected resource set exactly -- ``==``, not
``issubset`` -- because the failure being guarded against is *extra*
infrastructure, and a subset assertion would not catch it.
"""

from __future__ import annotations

import pytest

from app.engine.mapper import ResourceMapper
from app.engine.policy import NON_DEPENDENCIES, REQUIREMENTS
from app.models.ir import Kind, Origin
from app.nlp.rule_extractor import RuleExtractor

# Services that must never appear unless named. These are the ones the
# generator used to invent.
ENTERPRISE_SERVICES: frozenset[Kind] = frozenset({
    Kind.LOAD_BALANCER, Kind.AUTOSCALING_GROUP, Kind.TARGET_GROUP,
    Kind.SQL_DATABASE, Kind.NOSQL_TABLE, Kind.CACHE, Kind.SECRET_STORE,
    Kind.NAT_GATEWAY, Kind.CDN, Kind.KUBERNETES_CLUSTER, Kind.CONTAINER_SERVICE,
    Kind.MONITORING, Kind.WAF, Kind.DATA_WAREHOUSE, Kind.KEY_MANAGEMENT,
})


def build(prompt: str):
    return ResourceMapper().map(RuleExtractor().extract(prompt))


def kinds(spec) -> set[Kind]:
    return {r.kind for r in spec.resources}


# --------------------------------------------------------------------------
# the four required scenarios
# --------------------------------------------------------------------------

def test_case_1_simple_ec2_generates_nothing_extra():
    """The reported bug: a single instance must not become a web platform."""
    spec = build(
        "Create a development environment in us-east-1 with one Ubuntu EC2 "
        "instance inside a VPC. Add an Internet Gateway, Security Group "
        "allowing SSH and HTTP, IAM role, and Elastic IP."
    )
    assert kinds(spec) == {
        Kind.VPC, Kind.SUBNET_PUBLIC, Kind.ROUTE_TABLE, Kind.INTERNET_GATEWAY,
        Kind.VM, Kind.ELASTIC_IP, Kind.SECURITY_GROUP, Kind.IAM_ROLE,
    }
    assert spec.region == "us-east-1"
    assert spec.environment == "dev"


def test_case_1_minimal_form():
    spec = build("Create one EC2 instance.")
    assert kinds(spec) == {
        Kind.VPC, Kind.SUBNET_PUBLIC, Kind.ROUTE_TABLE, Kind.INTERNET_GATEWAY,
        Kind.VM, Kind.SECURITY_GROUP, Kind.IAM_ROLE,
    }


def test_case_2_three_tier_application():
    spec = build(
        "A three tier application with an application load balancer, an auto "
        "scaling group of EC2 instances in private subnets, and an RDS "
        "PostgreSQL database. Highly available."
    )
    for expected in (Kind.LOAD_BALANCER, Kind.AUTOSCALING_GROUP, Kind.SQL_DATABASE,
                     Kind.SUBNET_PRIVATE, Kind.NAT_GATEWAY):
        assert expected in kinds(spec), expected
    # Still nothing gratuitous.
    assert not spec.has(Kind.CACHE)
    assert not spec.has(Kind.CDN)
    assert not spec.has(Kind.MONITORING)


def test_case_3_serverless_stays_serverless():
    """No VPC, no subnets, no gateways for a Lambda API."""
    spec = build(
        "A serverless API: API Gateway in front of a Lambda function that "
        "reads and writes a DynamoDB table."
    )
    assert kinds(spec) == {
        Kind.API_GATEWAY, Kind.FUNCTION, Kind.NOSQL_TABLE, Kind.IAM_ROLE,
    }
    assert not spec.has(Kind.VPC)


def test_case_4_kubernetes():
    spec = build("An EKS cluster with worker nodes and an ingress load balancer.")
    for expected in (Kind.KUBERNETES_CLUSTER, Kind.VPC, Kind.LOAD_BALANCER, Kind.IAM_ROLE):
        assert expected in kinds(spec), expected
    assert not spec.has(Kind.SQL_DATABASE)
    assert not spec.has(Kind.CACHE)


def test_eks_without_ingress_gets_no_load_balancer():
    """EKS creates load balancers at runtime from Service objects."""
    spec = build("An EKS cluster with 3 worker nodes.")
    assert Kind.KUBERNETES_CLUSTER in kinds(spec)
    assert not spec.has(Kind.LOAD_BALANCER)


# --------------------------------------------------------------------------
# negative space
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "Create one EC2 instance.",
    "an s3 bucket",
    "a dynamodb table",
    "a lambda function",
    "one virtual machine in mumbai",
    "a development environment with a single server",
])
def test_no_enterprise_services_appear_uninvited(prompt):
    """A named service is fine; an unnamed one is the bug."""
    spec = build(prompt)
    invented = {
        r.kind for r in spec.resources
        if r.kind in ENTERPRISE_SERVICES and r.origin is not Origin.EXPLICIT
    }
    assert not invented, f"{prompt!r} invented {[k.value for k in invented]}"


@pytest.mark.parametrize("prompt,forbidden", [
    ("a web server", Kind.LOAD_BALANCER),
    ("a web server", Kind.SQL_DATABASE),
    ("a production web server with a database", Kind.MONITORING),
    ("a production web server with a database", Kind.SECRET_STORE),
    ("an s3 bucket", Kind.CDN),
    ("a lambda function", Kind.API_GATEWAY),
    ("a lambda function", Kind.VPC),
    ("one EC2 instance", Kind.NAT_GATEWAY),
    ("one EC2 instance", Kind.SUBNET_PRIVATE),
    ("a postgres database", Kind.CACHE),
])
def test_specific_service_is_not_invented(prompt, forbidden):
    assert forbidden not in kinds(build(prompt))


def test_every_resource_is_explicit_or_a_dependency():
    """No resource may exist without a stated justification."""
    for prompt in (
        "Create one EC2 instance.",
        "a load balancer with three web servers and a mysql database",
        "an EKS cluster with an ingress load balancer",
        "a lambda function behind api gateway with a dynamodb table",
        "a bastion host and an s3 bucket in a private subnet",
    ):
        spec = build(prompt)
        for resource in spec.resources:
            assert resource.origin in (Origin.EXPLICIT, Origin.REQUIRED)
            assert resource.reason, f"{resource.id} has no reason recorded"
            if resource.origin is Origin.EXPLICIT:
                assert resource.evidence, f"{resource.id} has no source text"


# --------------------------------------------------------------------------
# the policy table itself
# --------------------------------------------------------------------------

def test_every_kind_has_a_policy_entry():
    """A new service without a policy entry would silently have no rules."""
    missing = [k.value for k in Kind if k not in REQUIREMENTS]
    assert not missing, f"kinds missing from REQUIREMENTS: {missing}"


def test_requirements_never_reference_optional_services():
    """The two tables must not contradict each other."""
    for kind, forbidden in NON_DEPENDENCIES.items():
        required = {r.kind for r in REQUIREMENTS.get(kind, ())}
        for candidate, _ in forbidden:
            assert candidate not in required, (
                f"{kind.value} lists {candidate.value} as both required and optional"
            )


def test_dependency_closure_terminates():
    """The fixed-point loop must settle rather than hit its pass limit."""
    spec = build(
        "a highly available three tier app in private subnets with a load "
        "balancer, auto scaling, an aurora mysql database, a redis cache, "
        "an s3 bucket, a bastion host and cloudfront"
    )
    assert not any("did not settle" in w for w in spec.warnings)


def test_dependency_graph_is_acyclic():
    spec = build("a load balancer with web servers, a database and a cache")
    assert spec.find_cycles() == []
    order = spec.creation_order()
    assert len(order) == len(spec.resources)
    # The VPC must be creatable before anything that lives inside it.
    assert order.index("main") < order.index("app_server")
