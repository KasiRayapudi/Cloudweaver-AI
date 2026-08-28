"""Concrete values the user stated must survive into the output.

A stated CIDR, name or resource id is the least ambiguous thing in a prompt.
Overriding them with defaults produced designs that quietly disagreed with the
requirement they came from: the user asked for 172.16.0.0/16 and got
10.0.0.0/16, asked to deploy into vpc-0abc123 and got a second VPC.

Three categories, handled differently:

* configuration (CIDR, ports, OS)  -> overrides the default
* identity (names)                 -> becomes the resource name and Name tag
* external references (vpc-0abc)   -> looked up, never created
"""

from __future__ import annotations

import re

import pytest

from app.engine.pipeline import Pipeline
from app.models.ir import Kind
from app.nlp.rule_extractor import RuleExtractor

PIPELINE = Pipeline()
EXTRACTOR = RuleExtractor()


def hcl(prompt: str) -> str:
    files = PIPELINE.run(prompt).terraform
    return "\n".join(v for k, v in files.items() if k.endswith(".tf"))


# --------------------------------------------------------------------------
# CIDRs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cidr", [
    "172.16.0.0/16", "10.50.0.0/16", "192.168.0.0/20", "10.0.0.0/8",
])
def test_a_stated_vpc_cidr_is_used(cidr):
    spec = EXTRACTOR.extract(f"a VPC with CIDR {cidr} and an EC2 instance")
    assert spec.first(Kind.VPC).properties["cidr_block"] == cidr


def test_the_stated_cidr_reaches_terraform():
    files = PIPELINE.run("a VPC with CIDR 172.16.0.0/16 and an EC2 instance").terraform
    assert '"172.16.0.0/16"' in files["terraform.tfvars"]


def test_the_widest_range_is_the_vpc():
    """Narrower ranges in the same prompt are subnets inside it."""
    spec = EXTRACTOR.extract(
        "a VPC 10.50.0.0/16 with public subnets 10.50.1.0/24 and an EC2"
    )
    assert spec.first(Kind.VPC).properties["cidr_block"] == "10.50.0.0/16"


def test_a_firewall_range_is_not_mistaken_for_an_allocation():
    """0.0.0.0/0 is an ingress rule, not an address range to carve up."""
    spec = EXTRACTOR.extract(
        "a VPC and a security group allowing 0.0.0.0/0 on port 443 with an EC2"
    )
    assert spec.first(Kind.VPC).properties.get("cidr_block") != "0.0.0.0/0"


def test_no_stated_cidr_keeps_the_default():
    spec = EXTRACTOR.extract("a VPC with an EC2 instance")
    assert spec.first(Kind.VPC).properties.get("cidr_block") in (None, "10.0.0.0/16")


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

@pytest.mark.parametrize("phrasing", ["called", "named", "tagged"])
def test_a_stated_instance_name_is_used(phrasing):
    spec = EXTRACTOR.extract(f"an EC2 instance {phrasing} web-01")
    assert spec.first(Kind.VM).display_name == "web-01"


def test_names_bind_to_the_service_they_follow():
    spec = EXTRACTOR.extract(
        "an EC2 instance called web-01 and an S3 bucket named my-uploads"
    )
    assert spec.first(Kind.VM).display_name == "web-01"
    assert spec.first(Kind.OBJECT_STORAGE).display_name == "my-uploads"


def test_a_stated_name_reaches_the_name_tag():
    assert 'Name = "web-01"' in hcl("an EC2 instance called web-01")


def test_a_stated_bucket_name_is_used_verbatim():
    """S3 names are global; a user naming one has chosen it deliberately."""
    assert 'bucket = "my-uploads"' in hcl("an S3 bucket named my-uploads")


def test_unnamed_resources_keep_the_generated_name():
    text = hcl("an EC2 instance")
    assert "local.name_prefix" in text


# --------------------------------------------------------------------------
# external references
# --------------------------------------------------------------------------

def test_an_existing_vpc_is_looked_up_not_created():
    spec = PIPELINE.run("an EC2 instance in my existing VPC vpc-0abc123def456").spec
    vpc = spec.first(Kind.VPC)
    assert vpc.is_external
    assert vpc.external_id == "vpc-0abc123def456"


def test_the_existing_vpc_becomes_a_data_source():
    text = hcl("an EC2 instance in my existing VPC vpc-0abc123def456")
    assert 'data "aws_vpc" "main"' in text
    assert 'resource "aws_vpc"' not in text


def test_everything_references_the_data_source():
    text = hcl("an EC2 instance in my existing VPC vpc-0abc123def456")
    assert "data.aws_vpc.main.id" in text
    assert re.search(r"(?<!data\.)aws_vpc\.main\.id", text) is None


def test_subnets_derive_from_the_real_vpc_range():
    """Deriving from var.vpc_cidr would place them outside the actual VPC."""
    text = hcl("an EC2 instance in my existing VPC vpc-0abc123def456")
    assert "cidrsubnet(data.aws_vpc.main.cidr_block" in text


def test_an_existing_security_group_is_looked_up():
    spec = PIPELINE.run("an EC2 using security group sg-0123456789abcdef").spec
    assert spec.first(Kind.SECURITY_GROUP).external_id == "sg-0123456789abcdef"
    text = hcl("an EC2 using security group sg-0123456789abcdef")
    assert 'data "aws_security_group"' in text


def test_a_stated_ami_is_used_directly():
    spec = EXTRACTOR.extract("an EC2 instance from ami-0abcdef1234567890")
    assert spec.first(Kind.VM).properties["ami_id"] == "ami-0abcdef1234567890"


def test_external_resources_are_not_reported_as_missing():
    """They emit a data source, so the emission audit must not flag them."""
    result = PIPELINE.run("an EC2 instance in my existing VPC vpc-0abc123def456")
    codes = {f.code for f in result.findings}
    assert "resource_not_generated" not in codes


def test_an_unsupported_reference_is_reported_not_guessed():
    result = PIPELINE.run("an EC2 instance using route table rtb-0abc123def456")
    assert any("rtb-0abc123def456" in w for w in result.spec.warnings)


# --------------------------------------------------------------------------
# multiplicity the model cannot yet hold
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,dimension", [
    ("an S3 bucket in us-east-1 replicated to eu-west-1", "region"),
    ("an EC2 in mumbai and a database in london", "region"),
    ("a dev EC2 and a prod EC2", "environment"),
    ("staging and production web servers", "environment"),
    ("two VPCs peered together", "VPC"),
    ("EC2 instances across multiple accounts", "account"),
])
def test_multiplicity_is_reported_rather_than_silently_collapsed(prompt, dimension):
    result = PIPELINE.run(prompt)
    assert any(dimension in w for w in result.spec.warnings), (
        f"{prompt!r} describes more than one {dimension} and said nothing"
    )


@pytest.mark.parametrize("prompt", [
    "an EC2 instance",
    "a production EC2 instance in eu-west-1",
    "a web server with a database in us-west-2",
])
def test_single_designs_raise_no_multiplicity_warning(prompt):
    result = PIPELINE.run(prompt)
    assert not any("more than one" in w for w in result.spec.warnings), prompt


# --------------------------------------------------------------------------
# determinism holds for all of it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "a VPC with CIDR 172.16.0.0/16 and an EC2 called web-01",
    "an EC2 instance in my existing VPC vpc-0abc123def456",
    "an S3 bucket named my-uploads with an EC2 from ami-0abcdef1234567890",
])
def test_stated_values_are_deterministic(prompt):
    assert PIPELINE.run(prompt).terraform == PIPELINE.run(prompt).terraform
