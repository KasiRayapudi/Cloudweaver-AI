"""Quantity extraction (regression suite for C1).

A number in a prompt used to donate its trailing digits to whatever service
phrase followed it: "Windows Server 2022 EC2 instance" produced 22 instances,
"172.16.0.0/16" produced 16, and a count above one then invented a load
balancer to spread traffic across the machines nobody asked for.

Numbers are unavoidable in infrastructure prompts -- versions, CIDR masks,
ports, instance sizes, dates, AZ counts -- so this file is exhaustive about
where digits may and may not appear.
"""

from __future__ import annotations

import random

import pytest

from app.models.ir import Kind
from app.nlp.rule_extractor import RuleExtractor

EXTRACTOR = RuleExtractor()


def count_of(prompt: str, kind: Kind = Kind.VM) -> int:
    resource = EXTRACTOR.extract(prompt).first(kind)
    return resource.count if resource else 0


# --------------------------------------------------------------------------
# counts that must be read
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected", [
    ("three web servers", 3),
    ("3 web servers", 3),
    ("5 EC2 instances", 5),
    ("two virtual machines", 2),
    ("a couple of EC2 instances", 2),
    ("a pair of web servers", 2),
    ("several application servers", 3),
    ("ten EC2 instances", 10),
    ("12 web servers", 12),
    ("one EC2 instance", 1),
    ("an EC2 instance", 1),
    ("4 t3.medium web servers", 4),
])
def test_stated_counts_are_read(prompt, expected):
    assert count_of(prompt) == expected


# --------------------------------------------------------------------------
# digits that are part of something else
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "a Windows Server 2022 EC2 instance",
    "a Windows Server 2019 EC2 instance with RDP",
    "a VPC with CIDR 172.16.0.0/16 and an EC2",
    "an EC2 in 10.0.0.0/24",
    "EC2 running Ubuntu 22.04",
    "EC2 running Ubuntu 20.04 LTS",
    "an m5.2xlarge EC2 instance",
    "a t3.micro EC2 instance",
    "an EC2 instance on port 8080",
    "an EC2 instance listening on port 443",
    "an EC2 instance deployed in 2024",
    "an EC2 with 100 GB of storage",
    "an EC2 instance running PostgreSQL 15.5",
    "an ec2 instance with 250gb disk",
])
def test_digits_belonging_to_other_things_are_not_counts(prompt):
    assert count_of(prompt) == 1, f"{prompt!r} misread a number as a quantity"


def test_the_reported_regression_exactly():
    """The prompt from the audit, asserted end to end."""
    spec = EXTRACTOR.extract("a Windows Server 2022 EC2 instance with RDP access")
    assert spec.first(Kind.VM).count == 1
    assert not spec.has(Kind.LOAD_BALANCER), (
        "a phantom load balancer was inferred from a misread instance count"
    )


def test_cidr_regression_exactly():
    spec = EXTRACTOR.extract("a VPC with CIDR 172.16.0.0/16 and an EC2")
    assert spec.first(Kind.VM).count == 1
    assert not spec.has(Kind.LOAD_BALANCER)


# --------------------------------------------------------------------------
# generative: any embedded number, anywhere
# --------------------------------------------------------------------------

# Shapes numbers actually take in infrastructure prompts.
NUMERIC_CONTEXTS = [
    "Windows Server {n}", "Ubuntu {n}.04", "version {n}", "port {n}",
    "{n} GB", "{n}.0.0.0/16", "release {n}", "in {n}", "PostgreSQL {n}.5",
    "t{n}.medium", "{n}%", "SLA {n}",
]


@pytest.mark.parametrize("template", NUMERIC_CONTEXTS)
def test_numbers_in_context_never_become_counts(template):
    """Fixed seed keeps this deterministic while still covering the space."""
    rng = random.Random(20260828)
    for _ in range(40):
        number = rng.randint(0, 9999)
        prompt = f"an EC2 instance, {template.format(n=number)}"
        assert count_of(prompt) == 1, f"{prompt!r} -> count {count_of(prompt)}"


def test_every_standalone_number_up_to_100_is_read_as_itself():
    for number in range(1, 101):
        assert count_of(f"{number} web servers") == number


def test_counts_above_the_cap_are_clamped_not_dropped():
    assert count_of("500 web servers") == 100


# --------------------------------------------------------------------------
# the inference that depends on counts
# --------------------------------------------------------------------------

def test_a_single_instance_gets_no_load_balancer():
    assert not EXTRACTOR.extract("one EC2 instance").has(Kind.LOAD_BALANCER)


def test_multiple_instances_still_get_a_load_balancer():
    spec = EXTRACTOR.extract("three web servers")
    assert spec.has(Kind.LOAD_BALANCER)
    assert "3 instances" in spec.first(Kind.LOAD_BALANCER).reason


def test_counts_apply_to_the_right_resource():
    spec = EXTRACTOR.extract("2 web servers and 3 s3 buckets")
    assert spec.first(Kind.VM).count == 2
    assert spec.first(Kind.OBJECT_STORAGE).count == 3
