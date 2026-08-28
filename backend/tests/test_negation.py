"""Negation and hedging (regression suite for C4).

Matching a service phrase says the words appeared, not that the user wanted
the service. "an EC2 instance without a load balancer" contains the words
"load balancer" and is a refusal of it. The policy engine cannot catch this:
by the time the mapper runs the resource is already marked explicit, so the
refusal has to be understood during extraction or not at all.
"""

from __future__ import annotations

import pytest

from app.engine.mapper import ResourceMapper
from app.engine.pipeline import Pipeline
from app.models.ir import Kind
from app.nlp.rule_extractor import RuleExtractor

EXTRACTOR = RuleExtractor()


def extract(prompt: str):
    return EXTRACTOR.extract(prompt)


def kinds(prompt: str) -> set[Kind]:
    return {r.kind for r in extract(prompt).resources}


def built(prompt: str) -> set[Kind]:
    """After the mapper: dependencies may legitimately reintroduce a kind."""
    spec = ResourceMapper().map(extract(prompt))
    return {r.kind for r in spec.resources}


# --------------------------------------------------------------------------
# the reported regressions
# --------------------------------------------------------------------------

def test_no_database():
    assert Kind.SQL_DATABASE not in kinds("a web server but no database")


def test_without_a_load_balancer():
    assert Kind.LOAD_BALANCER not in kinds("an EC2 instance without a load balancer")


def test_deferred_database_is_not_built_today():
    spec = extract("maybe add a database later, for now just an EC2")
    assert Kind.SQL_DATABASE not in {r.kind for r in spec.resources}
    assert Kind.VM in {r.kind for r in spec.resources}


# --------------------------------------------------------------------------
# negation cues
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "a web server with no database",
    "a web server without a database",
    "a web server, excluding the database",
    "a web server but not a database",
    "a web server. do not need a database",
    "a web server and skip the database",
    "a web server, omit the database",
    "a web server rather than a database",
])
def test_refusals_are_honoured(prompt):
    assert Kind.SQL_DATABASE not in kinds(prompt), prompt
    assert Kind.VM in kinds(prompt), prompt


@pytest.mark.parametrize("prompt", [
    "an EC2 and maybe a database later",
    "an EC2, we might add a database eventually",
    "an EC2 now, a database in the future",
    "an EC2; a database is not yet needed",
    "an EC2 and possibly a database down the line",
])
def test_hedged_services_are_deferred(prompt):
    assert Kind.SQL_DATABASE not in kinds(prompt), prompt


# --------------------------------------------------------------------------
# what must NOT be treated as a refusal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "a web server with a database",
    "a highly available web server with no single point of failure",
    "an EC2 instance with no public IP in a private subnet",
    "no database, just a web server",
    "no load balancer but a web server",
])
def test_ordinary_requests_are_not_misread_as_refusals(prompt):
    assert Kind.VM in kinds(prompt), prompt


def test_availability_language_is_not_a_refusal():
    """"no single point of failure" is a requirement, not a negation."""
    spec = extract("a highly available web server with no single point of failure")
    assert spec.high_availability
    assert Kind.VM in {r.kind for r in spec.resources}
    assert not any(e.kind is Kind.VM for e in spec.exclusions)


def test_a_contrast_resets_the_refusal():
    """In "no database but a web server", the web server is still wanted."""
    spec = extract("no database but a web server")
    assert Kind.VM in {r.kind for r in spec.resources}
    assert Kind.SQL_DATABASE not in {r.kind for r in spec.resources}


def test_for_now_resets_a_hedge():
    spec = extract("we might add redis eventually, right now an EC2 and a database")
    assert {Kind.VM, Kind.SQL_DATABASE} <= {r.kind for r in spec.resources}
    assert Kind.CACHE not in {r.kind for r in spec.resources}


# --------------------------------------------------------------------------
# the exclusion record
# --------------------------------------------------------------------------

def test_exclusions_are_recorded_with_their_cue():
    spec = extract("an EC2 instance without a load balancer")
    assert len(spec.exclusions) == 1
    exclusion = spec.exclusions[0]
    assert exclusion.kind is Kind.LOAD_BALANCER
    assert exclusion.cue == "without"
    assert "load balancer" in exclusion.phrase
    assert "without" in exclusion.reason


def test_exclusions_survive_to_the_api_payload():
    payload = Pipeline().run("an EC2 instance without a load balancer").to_dict()
    assert payload["exclusions"]
    assert payload["exclusions"][0]["kind"] == "load_balancer"
    assert payload["exclusions"][0]["cue"] == "without"


# --------------------------------------------------------------------------
# refusals must survive inference and the closure
# --------------------------------------------------------------------------

def test_an_excluded_load_balancer_is_not_re_added_by_a_trigger():
    """"highly available" normally infers a load balancer. Not if refused."""
    spec = extract("a highly available web server without a load balancer")
    assert Kind.LOAD_BALANCER not in {r.kind for r in spec.resources}


def test_an_excluded_scaling_group_is_not_re_added_by_a_trigger():
    spec = extract("a highly available web server, no auto scaling")
    assert Kind.AUTOSCALING_GROUP not in {r.kind for r in spec.resources}


def test_a_refusal_cannot_override_a_mandatory_dependency():
    """A refusal removes a request, not a law of the platform.

    An EC2 instance cannot exist outside a VPC, so "without a VPC" is not
    satisfiable; the dependency still appears and the design stays deployable.
    """
    assert Kind.VPC in built("an EC2 instance without a VPC")


def test_refused_services_do_not_reach_terraform():
    result = Pipeline().run("an EC2 instance without a load balancer")
    text = "\n".join(v for k, v in result.terraform.items() if k.endswith(".tf"))
    assert 'resource "aws_lb"' not in text
