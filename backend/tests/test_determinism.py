"""The same requirement must always produce the same infrastructure.

Reproducibility is the reason the rule-based extractor is the default. If a
prompt produced a different resource set on Tuesday, the Terraform in review
would not be the Terraform that gets applied, and no amount of policy would
make the output trustworthy.

Determinism is checked three ways: repeated runs, insensitivity to irrelevant
wording, and stable ordering.
"""

from __future__ import annotations

import json

import pytest

from app.engine.mapper import ResourceMapper
from app.engine.pipeline import Pipeline
from app.nlp.rule_extractor import RuleExtractor

PROMPTS = [
    "Create one EC2 instance.",
    "Create a development environment in us-east-1 with one Ubuntu EC2 instance "
    "inside a VPC. Add an Internet Gateway, Security Group allowing SSH and HTTP, "
    "IAM role, and Elastic IP.",
    "a load balancer with three web servers and a mysql database",
    "a highly available auto scaling group in private subnets with an aurora "
    "postgresql database, a redis cache and an s3 bucket",
    "api gateway in front of a python lambda function with a dynamodb table",
    "an EKS cluster with 4 t3.medium nodes and an ingress load balancer",
    "a static website in an s3 bucket behind cloudfront with a waf",
    "a bastion host, an efs file system and a redshift warehouse in private subnets",
]


def build(prompt: str):
    return ResourceMapper().map(RuleExtractor().extract(prompt))


# --------------------------------------------------------------------------
# repeated runs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", PROMPTS)
def test_spec_is_identical_across_runs(prompt):
    first = build(prompt).model_dump(mode="json")
    for _ in range(3):
        assert build(prompt).model_dump(mode="json") == first


@pytest.mark.parametrize("prompt", PROMPTS)
def test_terraform_is_byte_identical_across_runs(prompt):
    pipeline = Pipeline()
    first = pipeline.run(prompt).terraform
    assert pipeline.run(prompt).terraform == first


@pytest.mark.parametrize("prompt", PROMPTS)
def test_diagram_is_identical_across_runs(prompt):
    pipeline = Pipeline()
    first = pipeline.run(prompt)
    second = pipeline.run(prompt)
    assert first.diagram_svg == second.diagram_svg
    assert first.diagram_mermaid == second.diagram_mermaid


@pytest.mark.parametrize("prompt", PROMPTS)
def test_findings_are_identical_across_runs(prompt):
    pipeline = Pipeline()
    a = [f.to_dict() for f in pipeline.run(prompt).findings]
    b = [f.to_dict() for f in pipeline.run(prompt).findings]
    assert a == b


def test_a_fresh_pipeline_produces_the_same_output():
    """No state may leak between pipeline instances."""
    prompt = PROMPTS[3]
    assert Pipeline().run(prompt).terraform == Pipeline().run(prompt).terraform


# --------------------------------------------------------------------------
# ordering stability
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", PROMPTS)
def test_resource_order_is_stable(prompt):
    order = [r.id for r in build(prompt).resources]
    assert [r.id for r in build(prompt).resources] == order


@pytest.mark.parametrize("prompt", PROMPTS)
def test_creation_order_is_stable_and_complete(prompt):
    spec = build(prompt)
    order = spec.creation_order()
    assert order == build(prompt).creation_order()
    assert sorted(order) == sorted(r.id for r in spec.resources)


@pytest.mark.parametrize("prompt", PROMPTS)
def test_dependencies_precede_their_dependants(prompt):
    spec = build(prompt)
    position = {rid: i for i, rid in enumerate(spec.creation_order())}
    for resource_id, parents in spec.dependency_graph().items():
        for parent in parents:
            assert position[parent] < position[resource_id], (
                f"{parent} must be created before {resource_id}"
            )


# --------------------------------------------------------------------------
# irrelevant wording must not change the design
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Create one EC2 instance.", "create one ec2 instance"),
    ("Create one EC2 instance.", "Please create one EC2 instance, thanks!"),
    ("an s3 bucket and a lambda function", "a lambda function and an s3 bucket"),
    ("a web server with a mysql database", "a mysql database with a web server"),
])
def test_wording_that_carries_no_meaning_is_ignored(a, b):
    assert {r.kind for r in build(a).resources} == {r.kind for r in build(b).resources}


@pytest.mark.parametrize("phrase", ["EC2", "ec2", "Ec2", "  EC2  "])
def test_casing_and_spacing_do_not_matter(phrase):
    spec = build(f"one {phrase} instance")
    assert {r.id for r in spec.resources} == {
        "app_server", "main", "public", "public_rt", "igw", "app_sg", "instance_role",
    }


# --------------------------------------------------------------------------
# the whole payload
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", PROMPTS)
def test_full_response_serialises_identically(prompt):
    pipeline = Pipeline()

    def snapshot() -> str:
        payload = pipeline.run(prompt).to_dict()
        payload["summary"].pop("duration_ms", None)  # wall clock, not output
        return json.dumps(payload, sort_keys=True)

    assert snapshot() == snapshot()
