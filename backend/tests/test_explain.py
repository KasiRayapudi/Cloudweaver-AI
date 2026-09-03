"""Per-resource explanation.

The explainer is a *view* over the specification. It adds no resource,
changes no property and holds no state, so the tests below fall into three
groups: that it stays a view, that its provenance is true of this design, and
that its service knowledge is complete for every kind the catalog can emit.

The last group matters most. A missing entry in a knowledge table is not a
crash — it is a silently empty field in an explanation a reviewer is relying
on, which is exactly the failure mode this project exists to avoid.
"""

from __future__ import annotations

import pytest

from app.engine.explain import (
    ALTERNATIVES,
    BEST_PRACTICES,
    NETWORKING_NOTES,
    PILLAR,
    SECURITY_NOTES,
    Explainer,
)
from app.engine.pipeline import Pipeline
from app.models.ir import Kind

PIPELINE = Pipeline()
EXPLAINER = Explainer()

WELL_ARCHITECTED = {
    "Security", "Reliability", "Performance Efficiency",
    "Cost Optimization", "Operational Excellence", "Sustainability",
}

CORPUS = [
    "two web servers behind a load balancer with a postgres database",
    "a production three-tier app, highly available, with a cache and a bucket",
    "a serverless API with lambda, api gateway and dynamodb",
    "an EKS cluster with 3 nodes and an S3 bucket",
    "an aurora postgresql cluster in private subnets with a bastion host",
    "a static site in S3 behind CloudFront with a WAF",
    "an EC2 instance in my existing VPC vpc-0abc123def456",
]


# --------------------------------------------------------------------------
# it stays a view
# --------------------------------------------------------------------------

def test_explaining_does_not_mutate_the_spec():
    result = PIPELINE.run("a web server with a postgres database")
    before = result.spec.model_dump(mode="json")
    EXPLAINER.explain_all(result.spec, result.terraform)
    assert result.spec.model_dump(mode="json") == before


def test_explanations_are_deterministic():
    """An explanation that varied between runs would be worthless as a record."""
    prompt = "a production web app with a database, a cache and a bucket"
    first = [e.to_dict() for e in PIPELINE.run(prompt).explanations]
    second = [e.to_dict() for e in PIPELINE.run(prompt).explanations]
    assert first == second


def test_there_is_exactly_one_explanation_per_resource():
    result = PIPELINE.run("a production three-tier app with a database and a cache")
    assert len(result.explanations) == len(result.spec.resources)
    assert {e.resource_id for e in result.explanations} == {
        r.id for r in result.spec.resources
    }


# --------------------------------------------------------------------------
# provenance is true of this design
# --------------------------------------------------------------------------

def test_a_requested_resource_is_marked_requested_and_cites_the_prompt():
    result = PIPELINE.run("a postgres database")
    database = next(e for e in result.explanations if e.kind == "sql_database")
    assert database.requested
    assert database.origin == "explicit"
    assert database.evidence, "a requested resource must quote the words that asked for it"
    assert database.rule is None, "nothing forced it; the user asked"


def test_a_dependency_names_the_rule_and_the_resource_that_triggered_it():
    result = PIPELINE.run("two web servers behind a load balancer")
    vpc = next(e for e in result.explanations if e.kind == "vpc")
    assert not vpc.requested
    assert vpc.origin == "implied"
    assert vpc.rule and vpc.rule.startswith("policy."), vpc.rule
    assert vpc.triggered_by, "a dependency must name what required it"


@pytest.mark.parametrize("prompt", CORPUS)
def test_every_rule_is_well_formed(prompt):
    for item in PIPELINE.run(prompt).explanations:
        if item.rule is None:
            continue
        parts = item.rule.split(".")
        assert parts[0] == "policy"
        assert parts[2] == "requires"
        assert len(parts) in (4, 5), item.rule


@pytest.mark.parametrize("prompt", CORPUS)
def test_triggering_resources_exist_in_the_design(prompt):
    result = PIPELINE.run(prompt)
    known = {r.id for r in result.spec.resources}
    for item in result.explanations:
        if item.triggered_by:
            assert item.triggered_by in known, item.resource_id


@pytest.mark.parametrize("prompt", CORPUS)
def test_dependency_lists_only_reference_real_resources(prompt):
    result = PIPELINE.run(prompt)
    known = {r.id for r in result.spec.resources}
    for item in result.explanations:
        assert set(item.depends_on) <= known
        assert set(item.required_by) <= known


def test_dependency_direction_is_consistent():
    """If A depends on B then B is required by A. Both come from one graph."""
    result = PIPELINE.run("a production web app with a database and a cache")
    by_id = {e.resource_id: e for e in result.explanations}
    for item in result.explanations:
        for parent in item.depends_on:
            assert item.resource_id in by_id[parent].required_by


# --------------------------------------------------------------------------
# code and cost
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", CORPUS)
def test_a_snippet_names_the_file_it_came_from(prompt):
    for item in PIPELINE.run(prompt).explanations:
        if item.terraform_snippet:
            assert item.terraform_file, item.resource_id
            assert item.terraform_file.endswith(".tf")
            assert item.resource_id in item.terraform_snippet


def test_the_snippet_matches_the_generated_file_exactly():
    """The API, the viewer and a report must quote the same text."""
    result = PIPELINE.run("a web server with a postgres database")
    for item in result.explanations:
        if not item.terraform_snippet:
            continue
        assert item.terraform_snippet in result.terraform[item.terraform_file]


def test_an_external_resource_costs_this_project_nothing():
    """It already exists; it is not created here, so it is not billed here."""
    result = PIPELINE.run("an EC2 instance in my existing VPC vpc-0abc123def456")
    vpc = next(e for e in result.explanations if e.kind == "vpc")
    assert vpc.external_id == "vpc-0abc123def456"
    assert vpc.monthly_cost_usd == 0.0


def test_cost_scales_with_the_resource_count():
    single = PIPELINE.run("one web server")
    triple = PIPELINE.run("three web servers")
    one = next(e for e in single.explanations if e.kind == "vm").monthly_cost_usd
    three = next(e for e in triple.explanations if e.kind == "vm").monthly_cost_usd
    assert three == pytest.approx(one * 3)


# --------------------------------------------------------------------------
# service knowledge is complete
# --------------------------------------------------------------------------

def test_every_kind_has_a_well_architected_pillar():
    missing = [k.value for k in Kind if k not in PILLAR]
    assert not missing, f"kinds with no pillar: {missing}"


def test_every_pillar_is_a_real_one():
    """A pillar name that is not in the framework helps nobody."""
    assert set(PILLAR.values()) <= WELL_ARCHITECTED


@pytest.mark.parametrize("table,label", [
    (SECURITY_NOTES, "security"),
    (NETWORKING_NOTES, "networking"),
    (BEST_PRACTICES, "best practice"),
    (ALTERNATIVES, "alternative"),
])
def test_knowledge_tables_are_keyed_by_real_kinds(table, label):
    for kind in table:
        assert isinstance(kind, Kind), f"{label} table has a non-Kind key: {kind}"


def test_notes_are_sentences_not_labels():
    for kind, note in {**SECURITY_NOTES, **NETWORKING_NOTES}.items():
        assert len(note) > 40, f"{kind.value}: note is too short to be useful"
        assert note.endswith("."), f"{kind.value}: note is not a sentence"


def test_every_alternative_says_when_to_choose_it():
    """"You could also use X" is useless without the reason to."""
    for kind, options in ALTERNATIVES.items():
        for option in options:
            assert option["service"], kind.value
            assert len(option["when"]) > 30, f"{kind.value}/{option['service']}"


def test_the_most_common_services_carry_full_knowledge():
    """The kinds a demo will actually show must not have gaps."""
    core = [
        Kind.VM, Kind.AUTOSCALING_GROUP, Kind.SQL_DATABASE, Kind.SQL_CLUSTER,
        Kind.LOAD_BALANCER, Kind.OBJECT_STORAGE, Kind.SECURITY_GROUP,
        Kind.NAT_GATEWAY, Kind.CACHE, Kind.FUNCTION,
    ]
    for kind in core:
        assert kind in PILLAR, kind.value
        assert kind in SECURITY_NOTES or kind in NETWORKING_NOTES, kind.value
        assert BEST_PRACTICES.get(kind) or ALTERNATIVES.get(kind), kind.value


# --------------------------------------------------------------------------
# cross-references into findings and recommendations
# --------------------------------------------------------------------------

def test_findings_are_attached_to_the_resource_they_name():
    result = PIPELINE.run(
        "a production web app with a publicly accessible database and SSH open to the world"
    )
    referenced = {
        code for item in result.explanations for code in item.finding_codes
    }
    named = {f.code for f in result.findings if f.resource_id}
    assert referenced <= named


def test_recommendations_are_attached_to_the_resources_they_affect():
    result = PIPELINE.run("two web servers behind a load balancer")
    balancer = next(e for e in result.explanations if e.kind == "load_balancer")
    assert "sec.tls" in balancer.recommendation_ids


# --------------------------------------------------------------------------
# the API carries it
# --------------------------------------------------------------------------

def test_explanations_reach_the_api_payload():
    payload = PIPELINE.run("a production web app with a database").to_dict()
    assert payload["explanations"]
    first = payload["explanations"][0]
    for key in ("resource_id", "requested", "origin", "reason", "pillar",
                "depends_on", "required_by", "monthly_cost_usd",
                "alternatives", "best_practices", "confidence"):
        assert key in first


def test_adding_explanations_did_not_change_existing_keys():
    payload = PIPELINE.run("a web server with a database").to_dict()
    for key in ("spec", "diagram", "terraform", "findings", "recommendations",
                "optimization", "dependency_graph", "exclusions", "extraction",
                "summary"):
        assert key in payload


def test_explaining_an_unknown_resource_returns_nothing():
    result = PIPELINE.run("a web server")
    assert EXPLAINER.explain(result.spec, "no_such_resource") is None
