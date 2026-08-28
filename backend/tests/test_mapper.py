"""Tests for the resource mapping engine."""

from __future__ import annotations

import pytest

from app.engine.mapper import ResourceMapper
from app.models.ir import EdgeKind, Kind
from app.nlp.rule_extractor import RuleExtractor


def mapped(text: str):
    return ResourceMapper().map(RuleExtractor().extract(text))


def has_edge(spec, source: str, target: str) -> bool:
    return any(e.source == source and e.target == target for e in spec.edges)


def test_vpc_is_implied_for_vpc_scoped_workloads():
    """Only the network a public instance actually needs, and nothing more."""
    spec = mapped("two web servers")
    assert spec.has(Kind.VPC)
    assert spec.has(Kind.SUBNET_PUBLIC)
    assert spec.has(Kind.INTERNET_GATEWAY)
    # Private networking was never requested, so it must not appear.
    assert not spec.has(Kind.SUBNET_PRIVATE)
    assert not spec.has(Kind.NAT_GATEWAY)


def test_serverless_design_gets_no_vpc():
    """A Lambda + DynamoDB API needs no VPC, and adding one would be noise."""
    spec = mapped("a lambda function that writes to a dynamodb table")
    assert not spec.has(Kind.VPC)
    assert not spec.has(Kind.NAT_GATEWAY)


def test_nat_gateway_added_only_for_private_compute():
    """A NAT gateway follows private compute, not merely a private subnet.

    A managed database needs private subnets but no egress, so a database
    alone must never produce a NAT gateway.
    """
    with_private_compute = mapped("a web server in a private subnet")
    assert with_private_compute.has(Kind.NAT_GATEWAY)

    database_only = mapped("two web servers with a database")
    assert database_only.has(Kind.SUBNET_PRIVATE)
    assert not database_only.has(Kind.NAT_GATEWAY)


def test_nat_gateway_per_az_when_highly_available():
    spec = mapped(
        "a highly available auto scaling group in private subnets with a database"
    )
    assert spec.first(Kind.NAT_GATEWAY).count == spec.availability_zones


def test_load_balancer_gets_target_group_and_targets_compute():
    spec = mapped("an auto scaling group behind a load balancer")
    tg = spec.first(Kind.TARGET_GROUP)
    assert tg is not None
    assert has_edge(spec, "alb", tg.id)
    assert has_edge(spec, tg.id, "app_asg")


def test_security_group_chain_is_least_privilege():
    spec = mapped("web servers behind a load balancer with a postgres database")
    assert spec.get("alb_sg").properties["ingress_from"] == "0.0.0.0/0"
    assert spec.get("app_sg").properties["ingress_from"] == "alb_sg"
    assert spec.get("db_sg").properties["ingress_from"] == "app_sg"


def test_database_port_follows_engine():
    assert mapped("a mysql database with a server").get("db_sg").properties[
        "ingress_ports"
    ] == [3306]
    assert mapped("a postgres database with a server").get("db_sg").properties[
        "ingress_ports"
    ] == [5432]


def test_compute_is_connected_to_every_backend():
    spec = mapped("a web server with a postgres database, a redis cache and an s3 bucket")
    assert has_edge(spec, "app_server", "app_db")
    assert has_edge(spec, "app_server", "app_cache")
    assert has_edge(spec, "app_server", "assets")


def test_cdn_origin_is_the_bucket_when_one_exists():
    spec = mapped("a static website in an s3 bucket behind cloudfront")
    cdn = spec.first(Kind.CDN)
    assert cdn.properties["origin_type"] == "s3"
    assert has_edge(spec, cdn.id, "assets")
    # Fronting by CloudFront means the bucket itself stays private.
    assert spec.first(Kind.OBJECT_STORAGE).properties["public_read"] is False


def test_api_gateway_is_wired_to_the_function():
    spec = mapped("api gateway in front of a lambda function")
    assert has_edge(spec, "api_gateway", "lambda_fn")


def test_queue_triggers_the_function():
    spec = mapped("a lambda function with an sqs queue")
    assert has_edge(spec, "work_queue", "lambda_fn")


def test_iam_role_matches_the_compute_type():
    assert mapped("a lambda function").get("lambda_role") is not None
    assert mapped("two web servers").get("instance_role") is not None
    assert mapped("an ecs fargate service").get("task_role") is not None


def test_secret_store_is_not_invented_for_a_database():
    """Terraform generates the password; a secret store was never requested."""
    spec = mapped("a web server with a postgres database")
    assert not spec.has(Kind.SECRET_STORE)
    assert mapped("a postgres database with secrets manager").has(Kind.SECRET_STORE)


def test_monitoring_is_never_invented():
    """"Production" is not a request for CloudWatch alarms."""
    assert not mapped("a production web server with a database").has(Kind.MONITORING)
    assert mapped("a web server with cloudwatch monitoring").has(Kind.MONITORING)


def test_mapping_is_idempotent():
    """Running the mapper twice must not duplicate implied resources."""
    spec = mapped("web servers behind a load balancer with a database")
    before = len(spec.resources), len(spec.edges)
    ResourceMapper().map(spec)
    assert (len(spec.resources), len(spec.edges)) == before


def test_no_self_referential_edges():
    spec = mapped("a web server with a database and a cache and a bucket")
    assert all(e.source != e.target for e in spec.edges)


def test_every_edge_points_at_a_real_resource():
    spec = mapped(
        "a production ecs fargate service behind a load balancer with an aurora "
        "mysql database, a redis cache, an s3 bucket and a bastion host"
    )
    ids = {r.id for r in spec.resources}
    for edge in spec.edges:
        assert edge.source in ids and edge.target in ids


@pytest.mark.parametrize("kind", [EdgeKind.TRAFFIC, EdgeKind.DATA, EdgeKind.DEPENDENCY])
def test_edge_kinds_are_used(kind):
    spec = mapped("web servers behind a load balancer with a postgres database")
    assert any(e.kind is kind for e in spec.edges)
