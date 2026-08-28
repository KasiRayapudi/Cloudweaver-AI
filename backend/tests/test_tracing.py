"""Every generated resource must be able to explain itself.

The decision record -- origin, reason, confidence, source text -- is what lets
a user challenge an unwanted resource. A resource that cannot say why it exists
is indistinguishable from a hallucination even when it is correct.
"""

from __future__ import annotations

import pytest

from app.engine.mapper import ResourceMapper
from app.engine.pipeline import Pipeline
from app.models.ir import Kind, Origin
from app.nlp.llm_extractor import LLMExtractor
from app.nlp.rule_extractor import RuleExtractor

CORPUS = [
    "Create one EC2 instance.",
    "Create a development environment in us-east-1 with one Ubuntu EC2 instance "
    "inside a VPC. Add an Internet Gateway, Security Group allowing SSH and HTTP, "
    "IAM role, and Elastic IP.",
    "a load balancer with three web servers and a mysql database",
    "an EKS cluster with an ingress load balancer and an s3 bucket",
    "api gateway in front of a lambda function with a dynamodb table and a queue",
    "a highly available auto scaling group in private subnets with an aurora "
    "postgresql database and a redis cache",
    "a static website in an s3 bucket behind cloudfront with a route 53 domain",
    "a bastion host and an efs shared file system",
    "a redshift data warehouse in private subnets",
]


def build(prompt: str):
    return ResourceMapper().map(RuleExtractor().extract(prompt))


# --------------------------------------------------------------------------
# the record itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", CORPUS)
def test_every_resource_records_a_reason(prompt):
    for resource in build(prompt).resources:
        assert resource.reason, f"{resource.id} has no reason"
        assert resource.reason.strip().endswith("."), (
            f"{resource.id} reason is not a sentence: {resource.reason!r}"
        )


@pytest.mark.parametrize("prompt", CORPUS)
def test_origin_is_explicit_or_required(prompt):
    for resource in build(prompt).resources:
        assert resource.origin in (Origin.EXPLICIT, Origin.REQUIRED)


@pytest.mark.parametrize("prompt", CORPUS)
def test_explicit_resources_quote_the_prompt(prompt):
    for resource in build(prompt).resources:
        if resource.origin is Origin.EXPLICIT:
            assert resource.evidence, f"{resource.id} cites no source text"


@pytest.mark.parametrize("prompt", CORPUS)
def test_confidence_is_meaningful(prompt):
    """Dependencies are certain; extractions are not."""
    for resource in build(prompt).resources:
        assert 0.0 < resource.confidence <= 1.0
        if resource.origin is Origin.REQUIRED:
            assert resource.confidence == 1.0, (
                f"{resource.id} is a mandatory dependency, so it is certain"
            )


def test_inferred_resources_are_less_confident_than_named_ones():
    spec = build("a highly available web server")
    named = spec.first(Kind.VM)
    inferred = spec.first(Kind.LOAD_BALANCER)
    assert inferred is not None
    assert inferred.confidence < named.confidence
    assert "highly available" in inferred.reason


def test_dependency_reasons_name_the_resource_that_needed_them():
    spec = build("Create one EC2 instance.")
    subnet = spec.first(Kind.SUBNET_PUBLIC)
    assert "EC2 Instance" in subnet.reason
    assert "no private subnet was requested" in subnet.reason


def test_assumptions_mirror_the_dependency_reasons():
    spec = build("Create one EC2 instance.")
    for resource in spec.resources:
        if resource.origin is Origin.REQUIRED:
            assert any(resource.reason in note for note in spec.assumptions)


# --------------------------------------------------------------------------
# exposed through the pipeline
# --------------------------------------------------------------------------

def test_pipeline_exposes_the_full_record():
    payload = Pipeline().run(CORPUS[1]).to_dict()
    assert payload["extraction"]
    for entry in payload["extraction"]:
        assert entry["reason"]
        assert entry["origin"] in ("explicit", "implied")
        assert 0.0 < entry["confidence"] <= 1.0
    graph = payload["dependency_graph"]
    assert graph["creation_order"]
    assert graph["cycles"] == []


# --------------------------------------------------------------------------
# the LLM path, stubbed so it runs offline
# --------------------------------------------------------------------------

class _Block:
    type = "tool_use"

    def __init__(self, payload):
        self.input = payload


class _Response:
    def __init__(self, payload):
        self.content = [_Block(payload)]


class _StubClient:
    """Stands in for anthropic.Anthropic and replays one canned tool call."""

    def __init__(self, payload):
        self._payload = payload
        self.messages = self

    def create(self, **kwargs):
        return _Response(self._payload)


class _OfflineLLMExtractor(LLMExtractor):
    """The real extractor with the SDK availability check stubbed out."""

    @property
    def available(self) -> bool:
        return True


def _llm_with(payload) -> LLMExtractor:
    extractor = _OfflineLLMExtractor(api_key="test-key")
    extractor._client = _StubClient(payload)
    return extractor


def test_llm_extraction_carries_the_model_justification():
    extractor = _llm_with({
        "region": "eu-west-1",
        "environment": "prod",
        "summary": "One instance.",
        "resources": [{
            "id": "web", "kind": "vm", "count": 2,
            "evidence": "two web servers",
            "reason": "The user asked for two web servers.",
            "confidence": 0.93,
        }],
    })
    spec = extractor.extract("two web servers in ireland")
    vm = spec.first(Kind.VM)
    assert vm.count == 2
    assert vm.reason == "The user asked for two web servers."
    assert vm.confidence == 0.93
    assert vm.evidence == "two web servers"


def test_llm_resources_without_a_reason_still_get_one():
    """Tracing is not optional, even when the model omits it."""
    extractor = _llm_with({
        "region": "us-east-1", "environment": "dev", "summary": "",
        "resources": [{"id": "b", "kind": "object_storage", "evidence": "a bucket"}],
    })
    bucket = extractor.extract("a bucket").first(Kind.OBJECT_STORAGE)
    assert bucket.reason
    assert "a bucket" in bucket.reason


def test_llm_output_goes_through_the_same_policy_engine():
    """The model reports; the policy decides. No VPC arrives by model whim."""
    extractor = _llm_with({
        "region": "us-east-1", "environment": "dev", "summary": "",
        "resources": [{
            "id": "app", "kind": "vm", "evidence": "a server",
            "reason": "The user asked for a server.", "confidence": 0.9,
        }],
    })
    spec = ResourceMapper().map(extractor.extract("a server"))
    assert {r.kind for r in spec.resources} == {
        Kind.VM, Kind.VPC, Kind.SUBNET_PUBLIC, Kind.ROUTE_TABLE,
        Kind.INTERNET_GATEWAY, Kind.SECURITY_GROUP, Kind.IAM_ROLE,
    }


def test_llm_rejects_kinds_outside_the_catalog():
    extractor = _llm_with({
        "region": "us-east-1", "environment": "dev", "summary": "",
        "resources": [
            {"id": "x", "kind": "quantum_computer", "evidence": "", "reason": "", "confidence": 1},
            {"id": "b", "kind": "object_storage", "evidence": "bucket",
             "reason": "asked for a bucket", "confidence": 0.9},
        ],
    })
    spec = extractor.extract("a bucket and a quantum computer")
    assert {r.kind for r in spec.resources} == {Kind.OBJECT_STORAGE}
    assert any("unsupported" in w for w in spec.warnings)
