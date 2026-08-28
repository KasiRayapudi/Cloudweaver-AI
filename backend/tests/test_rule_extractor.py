"""Tests for the deterministic requirement extractor."""

from __future__ import annotations

import pytest

from app.models.ir import Kind
from app.nlp.rule_extractor import RuleExtractor


@pytest.fixture(scope="module")
def extractor() -> RuleExtractor:
    return RuleExtractor()


def kinds(spec) -> set[Kind]:
    return {r.kind for r in spec.resources}


def test_identifies_core_services(extractor):
    spec = extractor.extract(
        "two EC2 web servers behind a load balancer with a PostgreSQL database"
    )
    assert kinds(spec) == {Kind.VM, Kind.LOAD_BALANCER, Kind.SQL_DATABASE}


def test_reads_explicit_count(extractor):
    spec = extractor.extract("three web servers")
    assert spec.first(Kind.VM).count == 3


def test_reads_written_number(extractor):
    spec = extractor.extract("five EC2 instances")
    assert spec.first(Kind.VM).count == 5


def test_matches_plural_phrases(extractor):
    spec = extractor.extract("a couple of lambda functions and two sqs queues")
    assert spec.first(Kind.FUNCTION).count == 2
    assert spec.first(Kind.QUEUE).count == 2


def test_digits_in_product_names_are_not_counts(extractor):
    """"Route 53 custom domain" must not be read as 53 domains."""
    spec = extractor.extract("a Route 53 custom domain in front of CloudFront")
    assert spec.first(Kind.DNS_ZONE).count == 1


def test_longest_phrase_wins(extractor):
    spec = extractor.extract("a NAT gateway and an internet gateway")
    assert kinds(spec) == {Kind.NAT_GATEWAY, Kind.INTERNET_GATEWAY}


def test_region_from_city_name(extractor):
    assert extractor.extract("a server in mumbai").region == "ap-south-1"


def test_region_defaults_with_assumption(extractor):
    spec = extractor.extract("a server")
    assert spec.region == "us-east-1"
    assert any("region" in note for note in spec.assumptions)


def test_environment_detection(extractor):
    assert extractor.extract("a production database").environment == "prod"
    assert extractor.extract("a staging database").environment == "staging"
    assert extractor.extract("a database").environment == "dev"


def test_high_availability_markers(extractor):
    spec = extractor.extract("a highly available multi-az database")
    assert spec.high_availability
    assert spec.availability_zones == 3


def test_explicit_az_count(extractor):
    spec = extractor.extract("web servers across 4 availability zones")
    assert spec.availability_zones == 4


@pytest.mark.parametrize(
    "text,engine",
    [
        ("a mysql database", "mysql"),
        ("a postgres database", "postgres"),
        ("an aurora mysql cluster", "aurora-mysql"),
        ("a mariadb instance", "mariadb"),
        ("a database", "postgres"),
    ],
)
def test_database_engine(extractor, text, engine):
    spec = extractor.extract(text)
    assert spec.first(Kind.SQL_DATABASE).properties["engine"] == engine


def test_instance_type_is_picked_up(extractor):
    spec = extractor.extract("two m5.large application servers")
    assert spec.first(Kind.VM).properties["instance_type"] == "m5.large"


def test_lambda_runtime_from_language(extractor):
    spec = extractor.extract("a node.js lambda function")
    assert spec.first(Kind.FUNCTION).properties["runtime"] == "nodejs20.x"


def test_asg_absorbs_its_member_instances(extractor):
    spec = extractor.extract("an auto scaling group of EC2 instances")
    # "auto scaling" also justifies a load balancer to spread traffic across
    # the group, which is the one inference the resource rules allow.
    assert Kind.AUTOSCALING_GROUP in kinds(spec)
    assert Kind.VM not in kinds(spec)
    assert any("auto scaling group" in note for note in spec.assumptions)


def test_separate_instances_and_asg_are_kept_apart(extractor):
    spec = extractor.extract(
        "an auto scaling group behind a load balancer, and separately a "
        "standalone jenkins virtual machine for builds sitting well away from "
        "the rest of the described application stack entirely"
    )
    assert Kind.VM in kinds(spec)
    assert Kind.AUTOSCALING_GROUP in kinds(spec)


def test_empty_input_is_reported_not_crashed(extractor):
    spec = extractor.extract("   ")
    assert spec.resources == []
    assert spec.warnings


def test_unrelated_text_produces_no_resources(extractor):
    spec = extractor.extract("write me a poem about the ocean")
    assert spec.resources == []
    assert spec.warnings


def test_evidence_is_recorded(extractor):
    spec = extractor.extract("I want an S3 bucket for user uploads")
    assert "bucket" in spec.first(Kind.OBJECT_STORAGE).evidence


def test_extraction_is_deterministic(extractor):
    a = extractor.extract("two web servers and a redis cache in oregon")
    b = extractor.extract("two web servers and a redis cache in oregon")
    assert a.model_dump() == b.model_dump()
