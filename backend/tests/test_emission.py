"""Emission audit (regression suite for C5).

The generator reaches for ``spec.first(kind)`` in many places, which assumes
one resource per kind. A design holding two databases had both drawn on the
diagram and one written to the Terraform -- the divergence this project exists
to prevent, invisible because nothing compared the two artefacts.

Multi-instance emission is still limited (Phase 3 removes the limit). What
these tests guarantee is that a shortfall can never ship *silently*.
"""

from __future__ import annotations

import pytest

from app.engine.emission import audit, declared_types
from app.engine.mapper import ResourceMapper
from app.engine.pipeline import Pipeline
from app.generators.terraform.generator import TerraformGenerator
from app.models.ir import InfrastructureSpec, Kind, Resource
from app.nlp.catalog import service_for

PIPELINE = Pipeline()

CLEAN_PROMPTS = [
    "one EC2 instance",
    "an s3 bucket",
    "a lambda function with api gateway and a dynamodb table",
    "a three tier app with a load balancer, auto scaling and a postgres database",
    "an EKS cluster with an ingress load balancer",
    "a redshift data warehouse in private subnets",
    "an aurora postgresql cluster",
    "an EC2 instance with cloudwatch monitoring",
    "a static site in s3 behind cloudfront with route 53 and a waf",
    "a bastion host and an efs file system",
    "a kms key and secrets manager",
    "an elastic ip and a nat gateway",
]

EMISSION_CODES = {"resource_not_generated", "duplicate_resource_dropped"}


def build(kinds: list[Kind]) -> tuple[InfrastructureSpec, dict[str, str]]:
    spec = InfrastructureSpec(name="probe", environment="dev")
    for index, kind in enumerate(kinds, start=1):
        info = service_for(kind)
        spec.add(Resource(id=f"{kind.value}_{index}", kind=kind,
                          name=f"{info.display} {index}", tier=info.tier, reason="probe"))
    ResourceMapper().map(spec)
    return spec, TerraformGenerator().generate(spec)


# --------------------------------------------------------------------------
# no false positives
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", CLEAN_PROMPTS)
def test_ordinary_designs_raise_no_emission_findings(prompt):
    result = PIPELINE.run(prompt)
    offenders = [f for f in result.findings if f.code in EMISSION_CODES]
    assert not offenders, [f.message for f in offenders]


def test_monitoring_without_targets_is_not_reported_as_missing():
    """Alarms are conditional by design; the validator covers that case."""
    result = PIPELINE.run("cloudwatch monitoring")
    assert not [f for f in result.findings if f.code in EMISSION_CODES]


def test_no_findings_when_nothing_was_generated():
    assert audit(InfrastructureSpec(name="empty"), {}) == []


# --------------------------------------------------------------------------
# the shortfall is detected
# --------------------------------------------------------------------------

def test_a_dropped_duplicate_is_reported():
    spec, files = build([Kind.CACHE, Kind.CACHE])
    codes = {code for _, code, _ in audit(spec, files)}
    assert "duplicate_resource_dropped" in codes


def test_the_report_names_the_type_and_the_counts():
    spec, files = build([Kind.CACHE, Kind.CACHE])
    message = next(m for _, code, m in audit(spec, files)
                   if code == "duplicate_resource_dropped")
    assert "aws_elasticache_replication_group" in message
    assert "2 resources" in message


def test_a_missing_resource_is_an_error_not_a_warning():
    spec, files = build([Kind.CACHE, Kind.CACHE])
    severities = {severity for severity, code, _ in audit(spec, files)
                  if code in EMISSION_CODES}
    assert severities == {"error"}


def test_the_pipeline_reports_a_generator_that_drops_a_resource():
    """The audit must run inside the pipeline, not only in this test file.

    A generator that loses a resource is simulated by stripping the instance
    out of the emitted Terraform, which is exactly the shape of the C5 defect.
    """
    pipeline = Pipeline()
    real_generate = pipeline.terraform.generate

    def lossy(spec):
        files = real_generate(spec)
        files["compute.tf"] = "# the instance went missing\n"
        return files

    pipeline.terraform.generate = lossy  # type: ignore[method-assign]
    try:
        result = pipeline.run("one EC2 instance")
    finally:
        pipeline.terraform.generate = real_generate  # type: ignore[method-assign]

    codes = {f.code for f in result.findings}
    assert "resource_not_generated" in codes


# --------------------------------------------------------------------------
# duplicates that ARE supported
# --------------------------------------------------------------------------

def test_two_databases_now_both_reach_the_terraform():
    spec, files = build([Kind.SQL_DATABASE, Kind.SQL_DATABASE])
    assert declared_types(files)["aws_db_instance"] == 2
    assert not [c for _, c, _ in audit(spec, files) if c in EMISSION_CODES]


def test_each_database_gets_its_own_password():
    _, files = build([Kind.SQL_DATABASE, Kind.SQL_DATABASE])
    assert declared_types(files)["random_password"] == 2


def test_two_buckets_both_reach_the_terraform():
    spec, files = build([Kind.OBJECT_STORAGE, Kind.OBJECT_STORAGE])
    assert declared_types(files)["aws_s3_bucket"] == 2
    assert not [c for _, c, _ in audit(spec, files) if c in EMISSION_CODES]


def test_two_functions_both_reach_the_terraform():
    spec, files = build([Kind.FUNCTION, Kind.FUNCTION])
    assert declared_types(files)["aws_lambda_function"] == 2


def test_two_queues_both_reach_the_terraform():
    spec, files = build([Kind.QUEUE, Kind.QUEUE])
    # Each queue also brings its own dead-letter queue.
    assert declared_types(files)["aws_sqs_queue"] >= 4


# --------------------------------------------------------------------------
# the counter itself
# --------------------------------------------------------------------------

def test_declared_types_counts_declarations_not_instances():
    """A resource with `count` is one declaration, however many it creates."""
    result = PIPELINE.run("three web servers")
    assert declared_types(result.terraform)["aws_instance"] == 1


def test_declared_types_ignores_non_terraform_files():
    counts = declared_types({
        "README.md": 'resource "aws_vpc" "main" {}',
        "main.tf": 'resource "aws_vpc" "main" {}',
    })
    assert counts["aws_vpc"] == 1
