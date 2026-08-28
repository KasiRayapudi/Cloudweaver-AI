"""AWS API constraints (regression suite for C3).

`terraform validate` checks schema, not provider semantics. An over-long load
balancer name and an Aurora engine on `aws_db_instance` both validate cleanly
and are then rejected at apply. These tests cover the gap between "Terraform
accepts it" and "AWS accepts it".
"""

from __future__ import annotations

import re

import pytest

from app.engine.constraints import (
    MAX_SUFFIX,
    NAME_RULES,
    TIGHTEST_LIMIT,
    check,
    fit,
    prefix_budget,
)
from app.engine.pipeline import Pipeline
from app.models.ir import InfrastructureSpec, Kind, Resource, Tier
from app.nlp.rule_extractor import RuleExtractor

PIPELINE = Pipeline()

LONG_PROMPT = (
    "Deploy the customer analytics reporting platform infrastructure with an "
    "application load balancer, an RDS postgres database, a redis cache and an "
    "s3 bucket in production"
)


# --------------------------------------------------------------------------
# fit()
# --------------------------------------------------------------------------

def test_short_names_are_untouched():
    assert fit("web-prod", 32) == "web-prod"


def test_long_names_are_shortened_to_the_limit():
    result = fit("customer-analytics-reporting-platform-production", 32)
    assert len(result) <= 32


def test_shortening_is_deterministic():
    name = "customer-analytics-reporting-platform-production"
    assert fit(name, 32) == fit(name, 32)


def test_shortening_does_not_collide_on_a_shared_prefix():
    """Plain truncation would give these two the same name."""
    a = fit("customer-analytics-reporting-alpha", 24)
    b = fit("customer-analytics-reporting-beta", 24)
    assert a != b


def test_shortened_names_never_end_in_a_hyphen():
    """AWS rejects names ending in '-'."""
    for length in range(10, 60):
        result = fit("a" * length + "-" + "b" * length, 20)
        assert not result.endswith("-")


@pytest.mark.parametrize("limit", [8, 12, 20, 32, 63])
def test_fit_always_respects_the_limit(limit):
    for length in (1, 5, 31, 64, 200):
        assert len(fit("x" * length, limit)) <= limit


# --------------------------------------------------------------------------
# the reported regression
# --------------------------------------------------------------------------

def test_project_name_no_longer_repeats_the_environment():
    """`analytics-prod-prod` wasted budget and read as a bug."""
    spec = RuleExtractor().extract(LONG_PROMPT)
    assert spec.environment == "prod"
    assert not spec.name.endswith("-prod")


def test_name_prefix_fits_the_tightest_limit():
    spec = RuleExtractor().extract(LONG_PROMPT)
    prefix = f"{spec.name}-{spec.environment}"
    assert len(prefix) + MAX_SUFFIX <= TIGHTEST_LIMIT + MAX_SUFFIX
    assert len(spec.name) <= prefix_budget()


def test_capped_names_use_the_shortened_prefix_in_hcl():
    """The final length is only known at apply, so HCL must do the trimming."""
    result = PIPELINE.run(LONG_PROMPT)
    edge = result.terraform["edge.tf"]
    assert '"${local.name_short}-alb"' in edge
    assert '"${local.name_short}-tg"' in edge

    locals_tf = result.terraform["locals.tf"]
    match = re.search(r"name_short\s*=\s*(.*)", locals_tf)
    assert match and "substr(" in match.group(1) and "trimsuffix(" in match.group(1)


def test_the_substr_budget_leaves_room_for_every_suffix():
    result = PIPELINE.run(LONG_PROMPT)
    match = re.search(r"substr\([^,]+,\s*0,\s*(\d+)\)", result.terraform["locals.tf"])
    assert match
    budget = int(match.group(1))
    assert budget + MAX_SUFFIX <= TIGHTEST_LIMIT


# --------------------------------------------------------------------------
# shape constraints
# --------------------------------------------------------------------------

def test_single_az_load_balancer_is_an_error():
    result = PIPELINE.run("a load balancer with web servers in 1 availability zone")
    codes = {f.code: f.severity for f in result.findings}
    assert codes.get("load_balancer_single_az") == "error"


def test_two_az_load_balancer_is_fine():
    result = PIPELINE.run("a load balancer with web servers in 2 availability zones")
    assert "load_balancer_single_az" not in {f.code for f in result.findings}


def test_single_az_kubernetes_is_an_error():
    result = PIPELINE.run("an EKS cluster in 1 availability zone")
    assert "eks_single_az" in {f.code for f in result.findings}


def test_aurora_on_an_instance_would_be_reported():
    """Belt and braces: the extractor cannot produce this any more (C2)."""
    spec = InfrastructureSpec(name="p", environment="dev")
    spec.add(Resource(
        id="db", kind=Kind.SQL_DATABASE, name="RDS Instance", tier=Tier.DATA,
        reason="probe", properties={"engine": "aurora-postgresql"},
    ))
    codes = {code for _, code, _ in check(spec)}
    assert "aurora_as_instance" in codes


def test_the_extractor_never_produces_aurora_on_an_instance():
    for prompt in ("an aurora postgresql database", "aurora mysql", "an aurora cluster"):
        spec = RuleExtractor().extract(prompt)
        codes = {code for _, code, _ in check(spec)}
        assert "aurora_as_instance" not in codes


def test_clean_designs_raise_no_constraint_findings():
    result = PIPELINE.run("one EC2 instance with an s3 bucket")
    constraint_codes = {c for _, c, _ in check(result.spec)}
    assert not constraint_codes


# --------------------------------------------------------------------------
# the rule table
# --------------------------------------------------------------------------

def test_every_rule_has_a_positive_limit():
    for key, rule in NAME_RULES.items():
        assert rule.max_length > 0, key
        assert rule.label


def test_the_tightest_limit_matches_the_table():
    assert TIGHTEST_LIMIT == min(
        NAME_RULES[k].max_length for k in ("load_balancer", "target_group")
    )
