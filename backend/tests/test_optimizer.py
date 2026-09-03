"""Architecture optimisation.

The optimiser answers "what would make this better?", which is a different
question from the validator's "what is wrong with it?". A finding says the
design is broken; a recommendation says it works and could be improved, at a
stated cost.

The invariant these tests exist to protect: **a recommendation is advice, never
a resource.** The system's central guarantee is that a generated resource was
either asked for or is a mandatory dependency. An optimiser that quietly added
a NAT gateway because it seemed wise would destroy that guarantee, so several
tests below check that nothing it does reaches the spec or the Terraform.
"""

from __future__ import annotations

import copy

import pytest

from app.engine.optimizer import PILLARS, PRIORITY_RANK, Optimizer, summarise
from app.engine.pipeline import Pipeline
from app.models.ir import Kind

PIPELINE = Pipeline()
OPTIMIZER = Optimizer()


def analyse(prompt: str):
    result = PIPELINE.run(prompt)
    return result, OPTIMIZER.analyse(result.spec)


def ids(recommendations) -> set[str]:
    return {r.id for r in recommendations}


# --------------------------------------------------------------------------
# the invariant: advice, never resources
# --------------------------------------------------------------------------

def test_analysis_does_not_mutate_the_spec():
    """The strongest form of the guarantee: the spec is unchanged afterwards."""
    result = PIPELINE.run("a web server with a postgres database")
    before = result.spec.model_dump(mode="json")
    OPTIMIZER.analyse(result.spec)
    assert result.spec.model_dump(mode="json") == before


def test_recommending_a_resource_does_not_create_it():
    """"Add a WAF" must not put a WAF in the design or in the Terraform."""
    result = PIPELINE.run("two web servers behind a load balancer")
    assert "sec.waf" in {r.id for r in result.recommendations}
    assert not result.spec.has(Kind.WAF)
    text = "\n".join(v for k, v in result.terraform.items() if k.endswith(".tf"))
    assert "aws_wafv2_web_acl" not in text


def test_recommendations_do_not_change_the_resource_count():
    result = PIPELINE.run(
        "a production web app with a database and an S3 bucket in one availability zone"
    )
    assert len(result.spec.resources) == result.to_dict()["summary"]["resource_count"]
    assert result.recommendations, "this design should produce recommendations"


def test_two_runs_produce_identical_recommendations():
    """Determinism is what makes a recommendation citable in a report."""
    prompt = "a production three-tier app with a database, a cache and a bucket"
    first = [r.to_dict() for r in PIPELINE.run(prompt).recommendations]
    second = [r.to_dict() for r in PIPELINE.run(prompt).recommendations]
    assert first == second


# --------------------------------------------------------------------------
# rules fire when they should
# --------------------------------------------------------------------------

def test_plain_http_balancer_is_told_to_terminate_tls():
    _, recs = analyse("two web servers behind a load balancer")
    assert "sec.tls" in ids(recs)


def test_https_balancer_is_not_told_to_add_tls():
    _, recs = analyse("two web servers behind a load balancer with HTTPS")
    assert "sec.tls" not in ids(recs)


def test_a_database_without_a_secret_store_is_flagged():
    _, recs = analyse("a web server with a postgres database")
    assert "sec.secrets_manager" in ids(recs)


def test_a_database_with_secrets_manager_is_not_flagged():
    _, recs = analyse("a web server with a postgres database and secrets manager")
    assert "sec.secrets_manager" not in ids(recs)


def test_single_az_production_is_critical():
    _, recs = analyse("a production web app with a database in 1 availability zone")
    critical = [r for r in recs if r.id == "rel.multi_az"]
    assert critical and critical[0].priority == "critical"


def test_multi_az_production_is_not_flagged():
    _, recs = analyse("a highly available production web app with a database")
    assert "rel.multi_az" not in ids(recs)


def test_nat_gateways_suggest_consolidation_with_a_real_saving():
    _, recs = analyse(
        "a dev web app in private subnets with a NAT gateway across 2 availability zones"
    )
    found = [r for r in recs if r.id == "cost.single_nat"]
    assert found, "a multi-AZ NAT design should offer consolidation"
    assert found[0].monthly_delta_usd < 0, "consolidating must be shown as a saving"


def test_object_storage_beside_nat_suggests_a_gateway_endpoint():
    _, recs = analyse(
        "a web app in private subnets with a NAT gateway and an S3 bucket"
    )
    assert "net.s3_endpoint" in ids(recs)


def test_compute_without_monitoring_is_flagged():
    _, recs = analyse("two web servers behind a load balancer")
    assert "ops.monitoring" in ids(recs)


def test_monitoring_present_is_not_flagged():
    _, recs = analyse("two web servers with cloudwatch monitoring")
    assert "ops.monitoring" not in ids(recs)


def test_production_suggests_remote_state():
    _, recs = analyse("a production web server with a database")
    assert "ops.remote_state" in ids(recs)


def test_a_bastion_offers_session_manager_as_a_saving():
    _, recs = analyse("a bastion host and a web server in private subnets")
    found = [r for r in recs if r.id == "sec.ssm_over_bastion"]
    assert found and found[0].monthly_delta_usd < 0


# --------------------------------------------------------------------------
# shape and quality of every recommendation
# --------------------------------------------------------------------------

CORPUS = [
    "two web servers behind a load balancer with a postgres database",
    "a production three-tier app, highly available, with a cache and a bucket",
    "a serverless API with lambda and dynamodb",
    "an EKS cluster with 3 nodes and an S3 bucket",
    "a dev EC2 instance",
    "an aurora postgresql cluster in private subnets",
    "a static site in S3 behind CloudFront",
    "a redshift warehouse with an S3 bucket in production",
]


@pytest.mark.parametrize("prompt", CORPUS)
def test_every_recommendation_is_complete(prompt):
    _, recs = analyse(prompt)
    for item in recs:
        assert item.id and "." in item.id, "ids are namespaced by category"
        assert item.title and not item.title.endswith("."), item.id
        assert len(item.reason) > 40, f"{item.id} must explain the consequence"
        assert item.action, f"{item.id} must say what to do"
        assert item.category in PILLARS
        assert item.priority in PRIORITY_RANK
        assert item.difficulty in ("trivial", "moderate", "involved")
        assert 0.0 < item.confidence <= 1.0
        assert item.pillar, "every recommendation maps to a Well-Architected pillar"


@pytest.mark.parametrize("prompt", CORPUS)
def test_recommendations_are_ranked_by_priority(prompt):
    _, recs = analyse(prompt)
    ranks = [PRIORITY_RANK[r.priority] for r in recs]
    assert ranks == sorted(ranks)


@pytest.mark.parametrize("prompt", CORPUS)
def test_referenced_resources_exist_in_the_design(prompt):
    result, recs = analyse(prompt)
    known = {r.id for r in result.spec.resources}
    for item in recs:
        assert set(item.resources) <= known, f"{item.id} names an unknown resource"


@pytest.mark.parametrize("prompt", CORPUS)
def test_no_duplicate_recommendation_ids(prompt):
    _, recs = analyse(prompt)
    seen = [r.id for r in recs]
    assert len(seen) == len(set(seen))


def test_an_empty_design_produces_no_recommendations():
    result = PIPELINE.run("")
    assert OPTIMIZER.analyse(result.spec) == []


def test_an_unsupported_provider_produces_no_recommendations():
    """Advising on a design that was never generated would be nonsense."""
    result = PIPELINE.run("an Azure virtual machine with a storage account")
    assert result.recommendations == []


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------

def test_summary_counts_match_the_recommendations():
    _, recs = analyse("a production web app with a database and a bucket")
    summary = summarise(recs)
    assert summary["total"] == len(recs)
    assert sum(summary["by_category"].values()) == len(recs)
    assert sum(summary["by_priority"].values()) == len(recs)


def test_summary_separates_savings_from_new_spend():
    _, recs = analyse(
        "a dev web app in private subnets with a NAT gateway across 2 availability zones"
    )
    summary = summarise(recs)
    assert summary["potential_monthly_saving_usd"] >= 0
    assert summary["additional_monthly_spend_usd"] >= 0
    # Both are reported as positive magnitudes so neither cancels the other.
    assert summary["potential_monthly_saving_usd"] > 0


def test_summary_of_nothing_is_zero_not_an_error():
    summary = summarise([])
    assert summary["total"] == 0
    assert summary["potential_monthly_saving_usd"] == 0
    assert summary["by_category"] == {}


# --------------------------------------------------------------------------
# the API carries it
# --------------------------------------------------------------------------

def test_recommendations_reach_the_api_payload():
    payload = PIPELINE.run("a production web app with a database").to_dict()
    assert payload["recommendations"], "the response must carry recommendations"
    assert payload["optimization"]["total"] == len(payload["recommendations"])
    first = payload["recommendations"][0]
    for key in ("id", "category", "priority", "title", "reason", "action",
                "resources", "difficulty", "monthly_delta_usd", "confidence", "pillar"):
        assert key in first


def test_adding_the_optimiser_did_not_change_existing_response_keys():
    """Additive only: every field callers already relied on is still present."""
    payload = PIPELINE.run("a web server with a database").to_dict()
    for key in ("spec", "diagram", "terraform", "findings", "dependency_graph",
                "exclusions", "extraction", "summary"):
        assert key in payload


def test_the_spec_survives_a_round_trip_unchanged():
    """Guards against a rule reaching into a resource's properties."""
    result = PIPELINE.run("a production web app with a database and a cache")
    snapshot = copy.deepcopy(result.spec.model_dump(mode="json"))
    for _ in range(3):
        OPTIMIZER.analyse(result.spec)
    assert result.spec.model_dump(mode="json") == snapshot
